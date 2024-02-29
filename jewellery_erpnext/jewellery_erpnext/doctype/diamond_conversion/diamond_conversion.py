# Copyright (c) 2024, Nirali and contributors
# For license information, please see license.txt

import frappe
from frappe.desk.reportview import get_filters_cond, get_match_cond
from frappe.model.document import Document
from frappe.query_builder.functions import CombineDatetime, CurDate, Sum
from frappe.utils import nowdate, unique


class DiamondConversion(Document):
	def on_submit(self):
		make_diamond_stock_entry(self)

	def validate(self):
		to_check_valid_qty_in_table(self)

	@frappe.whitelist()
	def get_detail_tab_value(self):
		dpt = frappe.get_value("Employee", self.employee, "department")
		mnf = frappe.get_value("Department", dpt, "manufacturer")
		if dpt:
			self.department = dpt
			s_wh = frappe.get_value("Warehouse", {"department": dpt}, "name")
			if s_wh:
				self.source_warehouse = s_wh
				self.target_warehouse = s_wh
			else:
				frappe.throw(f"{self.employee} Warehouse Master Department Not Set")
		else:
			frappe.throw(f"{self.employee} Employee Master Department Not Set")
		if mnf:
			self.manufacturer = mnf
		else:
			frappe.throw(f"{self.employee} Department Master Manufacturer Not Set")

	@frappe.whitelist()
	def get_batch_detail(self):
		bal_qty = ""
		supplier = ""
		customer = ""
		inventory_type = ""

		for row in self.sc_source_table:
			batch_qty = get_batches(row.item_code, self.source_warehouse, self.company)
			for batch_id, qty in batch_qty:
				if batch_id == row.batch:
					bal_qty = qty
					break

			batch_detail = frappe.db.get_all(
				"Batch",
				filters={"name": row.batch},
				fields={"name", "reference_doctype", "reference_name"},
			)
			if batch_detail:
				ref_doctype = batch_detail[0].reference_doctype
				ref_name = batch_detail[0].reference_name
				if ref_doctype == "Purchase Receipt":
					supplier = frappe.get_value(ref_doctype, ref_name, "supplier")
				if ref_doctype == "Stock Entry":
					customer, inventory_type = frappe.get_value(
						ref_doctype, ref_name, ["_customer", "inventory_type"]
					)
		return bal_qty or None, supplier or None, customer or None, inventory_type or None


def to_check_valid_qty_in_table(self):
	for row in self.sc_source_table:
		if row.qty <= 0:
			frappe.throw("Source Table Qty not allowed Nigative or Zero Value")
	for row in self.sc_target_table:
		if row.qty <= 0:
			frappe.throw("Target Table Qty not allowed Nigative or Zero Value")
	if not self.sc_source_table:
		frappe.throw("Source table is empty. Please add rows.")
	if not self.sc_target_table:
		frappe.throw("Target table is empty. Please add rows.")


def make_diamond_stock_entry(self):
	target_wh = self.target_warehouse
	source_wh = self.source_warehouse

	se = frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"company": self.company,
			"stock_entry_type": "Repack-Diamond Conversion",
			"purpose": "Repack",
			"custom_diamond_conversion": self.name,
			"inventory_type": "Regular Stock",
			"auto_created": 1,
		}
	)
	for row in self.sc_source_table:
		se.append(
			"items",
			{
				"item_code": row.item_code,
				"qty": row.qty,
				"inventory_type": row.inventory_type,
				"batch_no": row.batch,
				"department": self.department,
				"employee": self.employee,
				"manufacturer": self.manufacturer,
				"s_warehouse": source_wh,
			},
		)
	for row in self.sc_target_table:
		se.append(
			"items",
			{
				"item_code": row.item_code,
				"qty": row.qty,
				"inventory_type": "Regular Stock",  # row.inventory_type,
				# "batch_no":row.batch,
				"department": self.department,
				"employee": self.employee,
				"manufacturer": self.manufacturer,
				"t_warehouse": target_wh,
			},
		)
	se.save()
	se.submit()
	self.stock_entry = se.name


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_filtered_batches(doctype, txt, searchfield, start, page_len, filters):
	doctype = "Stock Ledger Entry"
	searchfield = "batch_no"
	conditions = []
	item_code = filters.get("item_code")
	warehouse = filters.get("warehouse")
	company = filters.get("company")
	fields = get_fields(doctype, ["name"])
	# frappe.throw(f"{item_code}")
	data = get_batches(item_code, warehouse, company)
	return data


def get_batches(item_code, warehouse, company, qty=1, throw=False, serial_no=None):
	batch = frappe.qb.DocType("Batch")
	sle = frappe.qb.DocType("Stock Ledger Entry")
	query = (
		frappe.qb.from_(batch)
		.join(sle)
		.on(batch.batch_id == sle.batch_no)
		.select(
			batch.batch_id.as_("batch_no"),
			Sum(sle.actual_qty).as_("qty"),
		)
		.where(
			(sle.item_code == item_code)
			& (sle.warehouse == warehouse)
			& (sle.is_cancelled == 0)
			& ((batch.expiry_date >= CurDate()) | (batch.expiry_date.isnull()))
		)
		.groupby(batch.batch_id)
		.having(Sum(sle.actual_qty) != 0)
		.orderby(batch.expiry_date, batch.creation)
	)
	batch_data = query.run(as_dict=False)
	return batch_data


def get_fields(doctype, fields=None):
	if fields is None:
		fields = []
	meta = frappe.get_meta(doctype)
	fields.extend(meta.get_search_fields())

	if meta.title_field and not meta.title_field.strip() in fields:
		fields.insert(1, meta.title_field.strip())

	return unique(fields)
