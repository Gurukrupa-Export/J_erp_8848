import copy

import frappe
from erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle import (
	get_auto_batch_nos,
)
from frappe import _
from frappe.query_builder import Case
from frappe.query_builder.functions import Locate
from frappe.utils import flt

from jewellery_erpnext.jewellery_erpnext.customization.stock_entry.doc_events.subcontracting_utils import (
	create_subcontracting_doc,
)
from jewellery_erpnext.utils import get_item_from_attribute


def validate_inventory_dimention(self):
	for row in self.items:
		if row.custom_parent_manufacturing_order or self.manufacturing_order:
			pmo = row.custom_parent_manufacturing_order or self.manufacturing_order
			pmo_data = frappe.db.get_value(
				"Parent Manufacturing Order",
				pmo,
				[
					"is_customer_gold",
					"is_customer_diamond",
					"is_customer_gemstone",
					"is_customer_material",
					"customer",
					"manufacturer",
				],
				as_dict=1,
			)
			allow_customer_goods = frappe.db.get_value(
				"Manufacturer",
				pmo_data.get("manufacturer"),
				"custom_allow_regular_goods_instead_of_customer_goods",
			)

			if (
				row.inventory_type in ["Customer Goods", "Customer Stock"]
				and pmo_data.get("customer") != row.customer
			):
				frappe.throw(_("Only {0} allowed in Stock Entry").format(pmo_data.get("customer")))
			else:
				variant_mapping = {
					"M": "is_customer_gold",
					"F": "is_customer_gold",
					"D": "is_customer_diamond",
					"G": "is_customer_gemstone",
					"O": "is_customer_material",
				}

				if row.custom_variant_of in variant_mapping:
					customer_key = variant_mapping[row.custom_variant_of]
					if pmo_data.get(customer_key) == 1 and row.inventory_type not in [
						"Customer Goods",
						"Customer Stock",
					]:
						frappe.throw(_("Can not use regular stock inventory for Customer provided Item"))
					elif row.inventory_type in ["Customer Goods", "Customer Stock"]:
						if allow_customer_goods:
							frappe.msgprint(_("Can not use Customer Goods inventory for non provided customer Item"))
						else:
							frappe.throw(_("Can not use Customer Goods inventory for non provided customer Item"))


def get_fifo_batches(self, row):
	rows_to_append = []
	row.batch_no = None
	total_qty = row.qty
	existing_updated = False

	msl = self.main_slip or self.to_main_slip
	if msl and frappe.db.get_value("Main Slip", msl, "raw_material_warehouse") == row.s_warehouse:
		main_slip = self.main_slip or self.to_main_slip
		batch_data = get_batch_data_from_msl(row.item_code, main_slip, row.s_warehouse)
	else:
		batch_data = get_auto_batch_nos(
			frappe._dict(
				{"posting_date": self.posting_date, "item_code": row.item_code, "warehouse": row.s_warehouse}
			)
		)

	customer_item_data = frappe._dict({})
	if row.custom_parent_manufacturing_order:
		customer_item_data = frappe.db.get_value(
			"Parent Manufacturing Order",
			row.custom_parent_manufacturing_order,
			[
				"is_customer_gold",
				"is_customer_diamond",
				"is_customer_gemstone",
				"is_customer_material",
				"customer",
			],
		)

	variant_to_customer_key = {
		"M": "is_customer_gold",
		"F": "is_customer_gold",
		"D": "is_customer_diamond",
		"G": "is_customer_gemstone",
		"O": "is_customer_material",
	}

	if row.custom_variant_of in variant_to_customer_key and customer_item_data.get(
		variant_to_customer_key[row.custom_variant_of]
	):
		row.inventory_type = "Customer Goods"
		row.customer = customer_item_data.customer

	for batch in batch_data:
		if (
			row.inventory_type in ["Customer Goods", "Customer Stock"]
			and frappe.db.get_value("Batch", batch.batch_no, "custom_inventory_type") == row.inventory_type
			and frappe.db.get_value("Batch", batch.batch_no, "custom_customer") == row.customer
		):
			if total_qty > 0 and batch.qty > 0:
				if not existing_updated:
					row.db_set("qty", min(total_qty, batch.qty))
					row.db_set("transfer_qty", row.qty)
					row.db_set("batch_no", batch.batch_no)
					total_qty -= batch.qty
					existing_updated = True
					rows_to_append.append(row.__dict__)
				else:
					temp_row = copy.deepcopy(row.__dict__)
					temp_row["name"] = None
					temp_row["idx"] = None
					temp_row["batch_no"] = batch.batch_no
					temp_row["transfer_qty"] = 0
					temp_row["qty"] = flt(min(total_qty, batch.qty), 4)
					rows_to_append.append(temp_row)
					total_qty -= batch.qty
		elif row.inventory_type not in ["Customer Goods", "Customer Stock"]:
			if total_qty > 0 and batch.qty > 0:
				if not existing_updated:
					row.db_set("qty", min(total_qty, batch.qty))
					row.db_set("transfer_qty", row.qty)
					row.db_set("batch_no", batch.batch_no)
					total_qty -= batch.qty
					existing_updated = True
					rows_to_append.append(row.__dict__)
				else:
					temp_row = copy.deepcopy(row.__dict__)
					temp_row["name"] = None
					temp_row["idx"] = None
					temp_row["batch_no"] = batch.batch_no
					temp_row["transfer_qty"] = 0
					temp_row["qty"] = flt(min(total_qty, batch.qty), 4)
					rows_to_append.append(temp_row)
					total_qty -= batch.qty

	if total_qty > 0:
		message = _("For <b>{0}</b> {1} is missing in <b>{2}</b>").format(
			row.item_code, flt(total_qty, 2), row.s_warehouse
		)
		if row.manufacturing_operation:
			message += _("<br><b>Ref : {0}</b>").format(row.manufacturing_operation)
		if self.flags.throw_batch_error:
			frappe.throw(message)
			self.flags.throw_batch_error = False
		else:
			frappe.msgprint(message)

	return rows_to_append


def get_batch_data_from_msl(item_code, main_slip, warehouse):
	batch_data = []
	msl_doc = frappe.get_doc("Main Slip", main_slip)

	if warehouse != msl_doc.raw_material_warehouse:
		frappe.msgprint(_("Please select batch manually for receving goods in Main Slip"))
		return batch_data

	for row in msl_doc.batch_details:
		if row.qty != row.consume_qty and row.item_code == item_code:
			batch_row = frappe._dict()
			batch_row.update({"batch_no": row.batch_no, "qty": row.qty - row.consume_qty})
			batch_data.append(batch_row)

	return batch_data


def create_repack_for_subcontracting(self, subcontractor, main_slip=None):
	if not subcontractor and main_slip:
		subcontractor = frappe.db.get_value("Main Slip", main_slip, "subcontractor")

	raw_warehouse = frappe.db.get_value(
		"Warehouse", {"subcontractor": subcontractor, "warehouse_type": "Raw Material"}
	)
	mfg_warehouse = frappe.db.get_value(
		"Warehouse", {"subcontractor": subcontractor, "warehouse_type": "Manufacturing"}
	)
	repack_raws = []
	receive = False
	for row in self.items:
		temp_raw = copy.deepcopy(row.__dict__)
		if row.t_warehouse == raw_warehouse:
			receive = True
			temp_raw["name"] = None
			temp_raw["idx"] = None
			repack_raws.append(temp_raw)
		elif row.s_warehouse == raw_warehouse and row.t_warehouse == mfg_warehouse:
			temp_raw["name"] = None
			temp_raw["idx"] = None
			repack_raws.append(temp_raw)

	if repack_raws:
		create_subcontracting_doc(self, subcontractor, self.department, repack_raws, main_slip, receive)


def validate_gross_weight_for_unpack(self):
	if self.stock_entry_type == "Repair Unpack":
		source_gr_wt = 0
		receive_gr_wt = 0
		for row in self.items:
			if row.s_warehouse:
				source_gr_wt += row.get("gross_weight") or 0
			elif row.t_warehouse:
				receive_gr_wt += row.get("gross_weight") or 0

		if flt(receive_gr_wt, 3) != flt(source_gr_wt, 3):
			frappe.throw(_("Gross weight does not match for source and target items"))


def validation_for_stock_entry_submission(self):
	for item in self.items:
		stock_reco = frappe.get_doc("Stock Reconciliation", {"set_warehouse": item.s_warehouse})
		if stock_reco.docstatus != 1:
			frappe.throw(
				_(
					"Please complete the Stock Reconciliation {0}  to Submit the Stock Entry".format_(
						stock_reco.name
					)
				)
			)
