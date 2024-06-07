import frappe
from frappe import _
from frappe.model.mapper import get_mapped_doc
from frappe.utils.data import flt


def on_submit(self, method=None):
	if self.custom_reserve_se:
		se_doc = frappe.get_doc("Stock Entry", self.custom_reserve_se)
		new_se_doc = frappe.copy_doc(se_doc)

		for row in new_se_doc.items:
			t_warehouse = frappe.db.get_value(
				"Material Request Item", row.material_request_item, "warehouse"
			)
			row.s_warehouse = row.t_warehouse
			row.t_warehouse = t_warehouse
			row.serial_and_batch_bundle = None

		new_se_doc.save()
		new_se_doc.submit()


@frappe.whitelist()
def make_stock_in_entry(source_name, target_doc=None):
	def set_missing_values(source, target):
		target.material_request_type = "Material Transfer"
		target.customer = source._customer
		target.set_missing_values()

	def update_item(source_doc, target_doc, source_parent):
		target_doc.material_request = source_doc.parent
		target_doc.material_request_item = source_doc.name
		target_doc.warehouse = ""
		target_doc.from_warehouse = source_doc.t_warehouse
		target_doc.qty = source_doc.qty

	doclist = get_mapped_doc(
		"Stock Entry",
		source_name,
		{
			"Stock Entry": {
				"doctype": "Material Request",
				# "field_map": {"name": "outgoing_stock_entry"},
				"validation": {"docstatus": ["=", 1]},
			},
			"Stock Entry Detail": {
				"doctype": "Material Request Item",
				"field_map": {
					"name": "ste_detail",
					"parent": "against_stock_entry",
					"serial_no": "custom_serial_no",
					"batch_no": "custom_batch_no",
				},
				"postprocess": update_item,
				# "condition": lambda doc: flt(doc.qty) - flt(doc.transferred_qty) > 0.01,
			},
		},
		target_doc,
		set_missing_values,
	)

	return doclist


@frappe.whitelist()
def make_stock_entry(source_name, target_doc=None):
	def update_item(obj, target, source_parent):
		qty = (
			flt(flt(obj.stock_qty) - flt(obj.ordered_qty)) / target.conversion_factor
			if flt(obj.stock_qty) > flt(obj.ordered_qty)
			else 0
		)
		target.qty = qty
		target.transfer_qty = qty * obj.conversion_factor
		target.conversion_factor = obj.conversion_factor

		if (
			source_parent.material_request_type == "Material Transfer"
			or source_parent.material_request_type == "Customer Provided"
		):
			target.t_warehouse = obj.warehouse
		else:
			target.s_warehouse = obj.warehouse

		if source_parent.material_request_type == "Customer Provided":
			target.allow_zero_valuation_rate = 1

		if source_parent.material_request_type == "Material Transfer":
			target.s_warehouse = obj.from_warehouse

	def set_missing_values(source, target):
		target.purpose = source.material_request_type
		# target.from_warehouse = source.set_from_warehouse
		# target.to_warehouse = source.set_warehouse
		# sending doc_id for reference
		target.custom_material_request_reference = source.name

		if source.job_card:
			target.purpose = "Material Transfer for Manufacture"

		if source.material_request_type == "Customer Provided":
			target.purpose = "Material Receipt"

		target.set_transfer_qty()
		target.set_actual_qty()
		target.calculate_rate_and_amount(raise_error_if_no_rate=False)
		if (
			source.material_request_type == "Material Transfer"
			and source.inventory_type == "Customer Goods"
		):
			target.stock_entry_type = "Customer Goods Transfer"
		else:
			target.stock_entry_type = target.purpose

		target.set_job_card_data()

		itm_batch = []
		dict = {}
		for i in source.items:
			dict.update(
				{
					"item": i.item_code,
					"batch": i.custom_batch_no,
					"serial": i.custom_serial_no,
					"idx": i.idx,
				}
			)
			itm_batch.append(dict)

		for itm in target.items:
			for b in itm_batch:
				if itm.item_code == b.get("item") and itm.idx == b.get("idx"):
					itm.batch_no = b.get("batch")
					itm.serial_no = b.get("serial")

		if source.job_card:
			job_card_details = frappe.get_all(
				"Job Card", filters={"name": source.job_card}, fields=["bom_no", "for_quantity"]
			)

			if job_card_details and job_card_details[0]:
				target.bom_no = job_card_details[0].bom_no
				target.fg_completed_qty = job_card_details[0].for_quantity
				target.from_bom = 1

	doclist = get_mapped_doc(
		"Material Request",
		source_name,
		{
			"Material Request": {
				"doctype": "Stock Entry",
				"validation": {
					"docstatus": ["=", 1],
					"material_request_type": [
						"in",
						["Material Transfer", "Material Issue", "Customer Provided"],
					],
				},
			},
			"Material Request Item": {
				"doctype": "Stock Entry Detail",
				"field_map": {
					"name": "material_request_item",
					"parent": "material_request",
					"uom": "stock_uom",
					"job_card_item": "job_card_item",
				},
				"postprocess": update_item,
				"condition": lambda doc: (
					flt(doc.ordered_qty, doc.precision("ordered_qty"))
					< flt(doc.stock_qty, doc.precision("ordered_qty"))
				),
			},
		},
		target_doc,
		set_missing_values,
	)

	return doclist


@frappe.whitelist()
def make_in_transit_stock_entry(source_name, to_warehouse, transfer_type, pmo=None, mnfr=None):
	pmo_doc = frappe.get_doc("Parent Manufacturing Order", pmo) if pmo else None
	to_department = frappe.db.get_value("Warehouse", to_warehouse, "department")
	in_transit_warehouse = frappe.db.get_value(
		"Warehouse", to_warehouse, "default_in_transit_warehouse"
	)

	if not in_transit_warehouse:
		frappe.throw(_("Transit warehouse is not mentioned in Target Warehouse"))

	ste_doc = make_stock_entry(source_name)
	if not ste_doc.employee:
		ste_doc.add_to_transit = 1

	stock_entry_type = frappe.db.get_value("Transfer Type", transfer_type, "stock_entry_type")
	if not stock_entry_type:
		frappe.throw(_("Please mention Stock Entry type for selected Transfer type."))

	ste_doc.stock_entry_type = stock_entry_type
	ste_doc.to_warehouse = in_transit_warehouse
	ste_doc.to_department = to_department

	if mnfr:
		ste_doc.stock_entry_type = "Material Transfer to Department"
		if (
			pmo_doc.customer_sample
			and pmo_doc.customer_voucher_no
			and pmo_doc.customer_gold
			and pmo_doc.customer_diamond
			and pmo_doc.customer_stone
			and pmo_doc.customer_good
		):
			ste_doc.inventory_type = "Customer Goods"
			ste_doc.customer = pmo_doc.customer
			for row in ste_doc.items:
				row.inventory_type = "Customer Goods"
				row.customer = pmo_doc.customer

	for row in ste_doc.items:
		row.t_warehouse = in_transit_warehouse
	return ste_doc


@frappe.whitelist()
def create_stock_entry(self, method):
	if (
		self.workflow_state == "Material Reserved"
		and not self.custom_reserve_se
		and self.manufacturing_order
	):
		# variant_based_warehouse = {}
		# warehouse_data = frappe.db.get_all(
		# 	"Variant based Warehouse", {"parent": self.custom_manufacturer}, ["variant", "department"]
		# )
		# for row in warehouse_data:
		# 	s_warehouse = frappe.db.get_value(
		# 		"Warehouse", {"department": row.department, "warehouse_type": "Raw Material"}
		# 	)
		# 	t_warehouse = frappe.db.get_value(
		# 		"Warehouse", {"department": row.department, "warehouse_type": "Reserve"}
		# 	)

		# 	variant_based_warehouse[row.variant] = {"s_warehouse": s_warehouse, "t_warehouse": t_warehouse}

		se_doc = frappe.new_doc("Stock Entry")
		se_doc.company = self.company
		stock_entry_type = frappe.db.get_value(
			"Transfer Type", self.custom_tranfer_type, "stock_entry_type"
		)
		if not stock_entry_type:
			frappe.throw(_("Please mention Stock Entry type for selected Transfer type."))
		se_doc.stock_entry_type = stock_entry_type
		se_doc.purpose = "Material Transfer"
		se_doc.add_to_transit = True

		for row in self.items:
			department = frappe.db.get_value("Warehouse", row.from_warehouse, "department")
			t_warehouse = frappe.db.get_value(
				"Warehouse", {"department": department, "warehouse_type": "Reserve"}, "name"
			)
			if not t_warehouse:
				frappe.throw(_("Transit warehouse not found for {0}").format(department))
			se_doc.append(
				"items",
				{
					"material_request": self.name,
					"material_request_item": row.name,
					"s_warehouse": row.from_warehouse,
					"t_warehouse": t_warehouse,
					"item_code": row.custom_alternative_item or row.item_code,
					"qty": row.qty,
					"inventory_type": row.inventory_type,
					"customer": row.customer,
					"batch_no": row.batch_no,
					"pcs": row.pcs,
					"cost_center": row.cost_center,
					"sub_setting_type": row.custom_sub_setting_type,
					"use_serial_batch_fields": True,
				},
			)

		se_doc.save()
		self.custom_reserve_se = se_doc.name
		se_doc.submit()
		frappe.msgprint(_("Reserved Stock Entry {0} has been created").format(se_doc.name))
