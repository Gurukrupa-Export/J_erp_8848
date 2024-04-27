import copy

import frappe
from erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle import (
	get_auto_batch_nos,
)
from frappe import _
from frappe.utils import flt

from jewellery_erpnext.utils import get_item_from_attribute


def get_fifo_batches(self, row):
	rows_to_append = []
	row.batch_no = None
	total_qty = row.qty
	existing_updated = False

	msl = self.main_slip or self.to_main_slip
	if msl and frappe.db.get_value("Main Slip", msl, "raw_material_warehouse") == row.s_warehouse:
		main_slip = self.main_slip or self.to_main_slip
		batch_data = get_batch_data_from_msl(main_slip, row.s_warehouse)
	else:
		batch_data = get_auto_batch_nos(
			frappe._dict(
				{"posting_date": self.posting_date, "item_code": row.item_code, "warehouse": row.s_warehouse}
			)
		)
	for batch in batch_data:
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
		frappe.msgprint(
			_(f"For <b>{row.item_code}</b> {flt(total_qty, 2)} is missing in <b>{row.s_warehouse}</b>")
		)

	return rows_to_append


def get_batch_data_from_msl(main_slip, warehouse):
	batch_data = []
	msl_doc = frappe.get_doc("Main Slip", main_slip)

	if warehouse != msl_doc.raw_material_warehouse:
		frappe.msgprint(_("Please select batch manually for receving goods in Main Slip"))
		return batch_data

	for row in msl_doc.batch_details:
		if row.qty != row.consume_qty:
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


def create_subcontracting_doc(
	self, subcontractor, department, repack_raws, main_slip=None, receive=False
):
	if not frappe.db.exists("Subcontracting", {"parent_stock_entry": self.name, "docstatus": 0}):
		sub_doc = frappe.new_doc("Subcontracting")
		sub_doc.supplier = subcontractor
		sub_doc.department = department
		sub_doc.date = frappe.utils.today()
		sub_doc.main_slip = main_slip
		sub_doc.company = self.company
		sub_doc.work_order = self.manufacturing_work_order
		sub_doc.manufacturing_order = self.manufacturing_order
		sub_doc.operation = self.manufacturing_operation

		sub_doc.finish_item = frappe.db.get_value("Manufacturing Setting", self.company, "service_item")

		if self.manufacturing_operation:
			metal_data = frappe.db.get_value(
				"Manufacturing Operation",
				self.manufacturing_operation,
				["metal_type", "metal_touch", "metal_purity", "metal_colour"],
				as_dict=True,
			)
		elif main_slip:
			metal_data = frappe.db.get_value(
				"Main Slip",
				main_slip,
				["metal_type", "metal_touch", "metal_purity", "metal_colour"],
				as_dict=True,
			)

		sub_doc.metal_type = metal_data.metal_type
		sub_doc.metal_touch = metal_data.metal_touch
		sub_doc.metal_purity = metal_data.metal_purity
		sub_doc.metal_colour = metal_data.metal_colour

		for row in repack_raws:

			temp_warehouse = row["s_warehouse"]
			row["s_warehouse"] = row["t_warehouse"] if receive else None
			row["t_warehouse"] = temp_warehouse if not receive else None
			sub_doc.append("source_table", row)

		sub_doc.transaction_type = "Receive" if receive else "Issue"
		sub_doc.parent_stock_entry = self.name
		sub_doc.save()
	else:
		sub_doc = frappe.get_doc("Subcontracting", {"parent_stock_entry": self.name, "docstatus": 0})
	if receive:
		frappe.enqueue(
			udpate_stock_entry,
			queue="short",
			event="Update Stock Entry",
			enqueue_after_commit=True,
			docname=self.name,
			subcontracting=sub_doc.name,
		)
	else:
		sub_doc.submit()


def udpate_stock_entry(docname, subcontracting):
	doc = frappe.get_doc("Stock Entry", docname)
	doc.subcontracting = subcontracting
	doc.save()


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def warehouse_query_filters(doctype, txt, searchfield, start, page_len, filters):
	filters["department"] = frappe.db.get_value(
		"Employee", {"user_id": frappe.session.user}, "department"
	)

	conditions = ""

	return frappe.db.sql(
		"""select  name, warehouse_name from `tabWarehouse`
		where is_group = 0
			and company = '{company}'
			and ({key} like %(txt)s
				or warehouse_name like %(txt)s)
			{fcond}
		order by
			(case when locate(%(_txt)s, name) > 0 then locate(%(_txt)s, name) else 99999 end),
			(case when locate(%(_txt)s, warehouse_name) > 0 then locate(%(_txt)s, warehouse_name) else 99999 end),
			idx desc,
			name, warehouse_name
		limit %(page_len)s offset %(start)s""".format(
			**{
				"company": filters["company"],
				"key": searchfield,
				"fcond": get_filters_cond(filters, conditions),
			}
		),
		{"txt": "%%%s%%" % txt, "_txt": txt.replace("%", ""), "start": start, "page_len": page_len},
	)


def get_filters_cond(filters, conditions):
	if filters["stock_entry_type"] == "Material Transfer (DEPARTMENT)" and filters.get("department"):
		raw_department = frappe.db.get_value(
			"Warehouse", {"warehouse_type": "Raw Material", "department": filters.get("department")}
		)
		conditions += "AND warehouse_type = 'Transit'"
		if raw_department:
			conditions += "OR name = '%s'" % raw_department

	elif filters["stock_entry_type"] in (
		"Material Transfer (MAIN SLIP)",
		"Material Transfer (Subcontracting Work Order)",
	) and filters.get("department"):
		raw_department = frappe.db.get_value(
			"Warehouse", {"warehouse_type": "Raw Material", "department": filters.get("department")}
		)

		if filters["stock_entry_type"] == "Material Transfer (MAIN SLIP)":
			conditions += " AND (employee != '' or employee != NULL)"
		else:
			conditions += " AND (subcontracter != '' or subcontracter != NULL)"
		if raw_department:
			conditions += " OR name = '%s'" % raw_department
			conditions += " AND warehouse_type = 'Raw Material'"

	elif filters["stock_entry_type"] == "Material Transfer (WORK ORDER)" and filters.get(
		"department"
	):
		raw_department = frappe.db.get_value(
			"Warehouse", {"warehouse_type": "Raw Material", "department": filters.get("department")}
		)
		conditions += "AND warehouse_type = 'Manufacturing' AND ((employee != '' or employee != NULL) or (department != '' or department != NULL))"
		if raw_department:
			conditions += "OR name = '%s'" % raw_department

	return conditions


def update_main_slip_se_details(doc, stock_entry_type, se_row, auto_created=0, is_cancelled=False):
	to_remove = []

	based_on = "employee"
	based_on_value = doc.employee
	if doc.subcontractor:
		based_on = "subcontractor"
		based_on_value = doc.subcontractor

	m_warehouse = frappe.db.get_value(
		"Warehouse", {based_on: based_on_value, "warehouse_type": "Manufacturing"}
	)
	r_warehouse = frappe.db.get_value(
		"Warehouse", {based_on: based_on_value, "warehouse_type": "Raw Material"}
	)

	qty = "qty"
	consume_qty = "consume_qty"
	if se_row.manufacturing_operation and stock_entry_type not in (
		"Material Transfer (MAIN SLIP)",
		"Manufacture",
	):
		qty = "mop_qty"
		consume_qty = "mop_consume_qty"

	if is_cancelled:
		to_remove = [row for row in doc.stock_details if row.se_item == se_row.name]

	else:
		exsting_se_details = [row.se_item for row in doc.stock_details]
		if se_row.s_warehouse == m_warehouse:
			if se_row.name not in exsting_se_details:
				if se_row.manufacturing_operation:
					consume_qty = "mop_consume_qty"
				doc.append(
					"stock_details",
					{
						"batch_no": se_row.batch_no,
						consume_qty: se_row.qty,
						"se_item": se_row.name,
						"auto_created": auto_created,
						"stock_entry": se_row.parent,
					},
				)

		if se_row.t_warehouse == r_warehouse:
			if se_row.name not in exsting_se_details:
				doc.append(
					"stock_details",
					{
						"item_code": se_row.item_code,
						"batch_no": se_row.batch_no,
						qty: se_row.qty,
						"se_item": se_row.name,
						"auto_created": auto_created,
						"stock_entry": se_row.parent,
					},
				)

		if se_row.s_warehouse == r_warehouse and se_row.t_warehouse == m_warehouse:
			if se_row.name not in exsting_se_details:
				doc.append(
					"stock_details",
					{
						"item_code": se_row.item_code,
						"batch_no": se_row.batch_no,
						"consume_qty": se_row.qty,
						"se_item": se_row.name,
						"auto_created": auto_created,
						"stock_entry": se_row.parent,
					},
				)
				doc.append(
					"stock_details",
					{
						"item_code": se_row.item_code,
						"batch_no": se_row.batch_no,
						"mop_qty": se_row.qty,
						"se_item": se_row.name,
						"auto_created": auto_created,
						"stock_entry": se_row.parent,
					},
				)

		elif se_row.s_warehouse == r_warehouse:
			if se_row.name not in exsting_se_details:
				doc.append(
					"stock_details",
					{
						"batch_no": se_row.batch_no,
						consume_qty: se_row.qty,
						"se_item": se_row.name,
						"auto_created": auto_created,
						"stock_entry": se_row.parent,
					},
				)

		elif se_row.t_warehouse == m_warehouse:
			if se_row.name not in exsting_se_details:
				doc.append(
					"stock_details",
					{
						"batch_no": se_row.batch_no,
						qty: se_row.qty,
						"se_item": se_row.name,
						"auto_created": auto_created,
						"stock_entry": se_row.parent,
					},
				)

	for row in to_remove:
		doc.remove(row)


def validate_gross_weight_for_unpack(self):
	if self.stock_entry_type == "Repair Unpack":
		source_gr_wt = 0
		receive_gr_wt = 0
		for row in self.items:
			if row.s_warehouse:
				source_gr_wt += row.gross_weight
			elif row.t_warehouse:
				receive_gr_wt += row.gross_weight

		if flt(receive_gr_wt, 3) != flt(source_gr_wt, 3):
			frappe.throw(_("Gross weight does not match for source and target items"))
