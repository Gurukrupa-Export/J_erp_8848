# Copyright (c) 2024, Nirali and contributors
# For license information, please see license.txt

import frappe
from frappe.desk.reportview import get_filters_cond, get_match_cond
from frappe.model.document import Document
from frappe.query_builder.functions import CombineDatetime, CurDate, Sum
from frappe.utils import nowdate, unique


class GemstoneConversion(Document):
	def on_submit(self):
		make_gemstone_stock_entry(self)
		if self.g_source_qty > self.batch_avail_qty:
			frappe.throw("Source Qty greater then batch available qty")

	def validate(self):
		if self.g_loss_qty < 0:
			frappe.throw("Target Qty not allowed greater than Source Qty")
		if self.g_target_qty > self.batch_avail_qty:
			frappe.throw("Target Qty not allowed greater than Batch Available Qty")
		if self.g_source_qty > self.batch_avail_qty:
			frappe.throw(
				f"Conversion failed batch available qty not meet. </br><b>(Batch Qty = {self.batch_avail_qty})</b><br>select another batch."
			)
		if self.g_source_qty == 0 or self.g_target_qty == 0:
			frappe.throw("Source Qty or Target Qty not allowed Zero to post transaction")
		if self.g_source_qty < 0:
			frappe.throw("Source Qty invalid")

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

		batch_qty = get_batches(self.g_source_item, self.source_warehouse, self.company)
		for batch_id, qty in batch_qty:
			if batch_id == self.batch:
				bal_qty = qty
				break
		batch_detail = frappe.db.get_all(
			"Batch",
			filters={"name": self.batch},
			fields={"name", "batch_qty", "reference_doctype", "reference_name"},
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


def make_gemstone_stock_entry(self):
	target_wh = self.target_warehouse
	source_wh = self.source_warehouse
	inventory_type = self.inventory_type
	batch_no = self.batch
	se = frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"stock_entry_type": "Repack-Gemstone Conversion",
			"purpose": "Repack",
			"company": self.company,
			"custom_gemstone_conversion": self.name,
			"inventory_type": inventory_type,
			"_customer": self.customer,
			"auto_created": 1,
		}
	)
	source_item = []
	target_item = []
	source_item.append(
		{
			"item_code": self.g_source_item,
			"qty": self.g_source_qty,
			"inventory_type": inventory_type,
			"batch_no": batch_no,
			"department": self.department,
			"employee": self.employee,
			"manufacturer": self.manufacturer,
			"s_warehouse": source_wh,
		}
	)
	target_item.append(
		{
			"item_code": self.g_target_item,
			"qty": self.g_target_qty,
			"inventory_type": inventory_type,
			"department": self.department,
			"employee": self.employee,
			"manufacturer": self.manufacturer,
			"t_warehouse": target_wh,
		}
	)
	if self.g_loss_qty > 0:
		target_item.append(
			{
				"item_code": self.g_loss_item,
				"qty": self.g_loss_qty,
				"inventory_type": inventory_type,
				"department": self.department,
				"employee": self.employee,
				"manufacturer": self.manufacturer,
				"t_warehouse": target_wh,
			}
		)
	for row in source_item:
		se.append(
			"items",
			{
				"item_code": row["item_code"],
				"qty": row["qty"],
				"inventory_type": row["inventory_type"],
				"batch_no": row["batch_no"],
				"department": row["department"],
				"employee": row["employee"],
				"manufacturer": row["manufacturer"],
				"s_warehouse": row["s_warehouse"],
			},
		)
	for row in target_item:
		se.append(
			"items",
			{
				"item_code": row["item_code"],
				"qty": row["qty"],
				"inventory_type": row["inventory_type"],
				"department": row["department"],
				"employee": row["employee"],
				"manufacturer": row["manufacturer"],
				"t_warehouse": row["t_warehouse"],
			},
		)
	se.save()
	se.submit()
	self.stock_entry = se.name


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
