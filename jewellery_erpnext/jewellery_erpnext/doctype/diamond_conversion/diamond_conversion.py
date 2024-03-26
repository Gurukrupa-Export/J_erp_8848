# Copyright (c) 2024, Nirali and contributors
# For license information, please see license.txt

import frappe
from erpnext.stock.doctype.batch.batch import get_batch_qty
from frappe.model.document import Document


class DiamondConversion(Document):
	def on_submit(self):
		make_diamond_stock_entry(self)

	def validate(self):
		to_check_valid_qty_in_table(self)

	@frappe.whitelist()
	def get_detail_tab_value(self):
		errors = []
		dpt, branch = frappe.get_value("Employee", self.employee, ["department", "branch"])
		if not dpt:
			errors.append(f"Department Messing against <b>{self.employee} Employee Master</b>")
		if not branch:
			errors.append(f"Branch Messing against <b>{self.employee} Employee Master</b>")
		mnf = frappe.get_value("Department", dpt, "manufacturer")
		if not mnf:
			errors.append("Manufacturer Messing against <b>Department Master</b>")
		s_wh = frappe.get_value("Warehouse", {"department": dpt}, "name")
		if not mnf:
			errors.append("Warehouse Missing Warehouse Master Department Not Set")
		if errors:
			frappe.throw("<br>".join(errors))
		if dpt and mnf and s_wh:
			self.department = dpt
			self.branch = branch
			self.manufacturer = mnf
			self.source_warehouse = s_wh
			self.target_warehouse = s_wh

	@frappe.whitelist()
	def get_batch_detail(self):
		bal_qty = ""
		supplier = ""
		customer = ""
		inventory_type = ""

		error = []
		for row in self.sc_source_table:
			bal_qty = get_batch_qty(batch_no=row.batch, warehouse=self.source_warehouse)
			reference_doctype, reference_name = frappe.get_value(
				"Batch", row.batch, ["reference_doctype", "reference_name"]
			)
			if not bal_qty:
				error.append("Batch Qty zero")
			if reference_doctype:
				if reference_doctype == "Purchase Receipt":
					supplier = frappe.get_value(reference_doctype, reference_name, "supplier")
					inventory_type = "Regular Stock"
				if reference_doctype == "Stock Entry":
					inventory_type = frappe.get_value(reference_doctype, reference_name, "inventory_type")
					if inventory_type == "Customer Goods":
						customer = frappe.get_value(reference_doctype, reference_name, "_customer")
			if error:
				frappe.throw(", ".join(error))
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
			"branch": self.branch,
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
