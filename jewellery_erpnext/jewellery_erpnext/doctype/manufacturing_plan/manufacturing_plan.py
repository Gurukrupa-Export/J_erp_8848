# Copyright (c) 2023, Nirali and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.mapper import get_mapped_doc
from frappe.utils import cint

from jewellery_erpnext.jewellery_erpnext.doc_events.purchase_order import make_subcontracting_order
from jewellery_erpnext.jewellery_erpnext.doctype.parent_manufacturing_order.parent_manufacturing_order import (
	make_manufacturing_order,
)
from jewellery_erpnext.utils import update_existing


class ManufacturingPlan(Document):
	def on_submit(self):
		for row in self.manufacturing_plan_table:
			update_existing(
				"Sales Order Item",
				row.docname,
				"manufacturing_order_qty",
				f"manufacturing_order_qty + {cint(row.manufacturing_order_qty) + cint(row.subcontracting_qty)}",
			)
			create_manufacturing_order(self, row)
			if row.subcontracting:
				create_subcontracting_order(self, row)
			if row.manufacturing_bom is None:
				frappe.throw(f"Row:{row.idx} Manufacturing Bom Missing")

	def on_cancel(self):
		for row in self.manufacturing_plan_table:
			update_existing(
				"Sales Order Item",
				row.docname,
				"manufacturing_order_qty",
				f"greatest(manufacturing_order_qty - {cint(row.manufacturing_order_qty) + cint(row.subcontracting_qty)},0)",
			)

	def validate(self):
		self.validate_qty()
		create_new_bom(self)

	def validate_qty(self):
		total = 0
		for row in self.manufacturing_plan_table:
			if not row.subcontracting:
				row.subcontracting_qty = 0
				row.supplier = None
			if (row.manufacturing_order_qty + row.subcontracting_qty) > row.pending_qty:
				frappe.throw(_(f"Row #{row.idx}: Total Order qty cannot be greater than {row.pending_qty}"))
			total += cint(row.manufacturing_order_qty) + cint(row.subcontracting_qty)
			if row.qty_per_manufacturing_order == 0:
				frappe.throw(_("Qty per Manufacturing Order cannot be Zero"))
			if row.manufacturing_order_qty % row.qty_per_manufacturing_order != 0:
				frappe.throw(
					_(
						f"Row #{row.idx}: `Manufacturing Order Qty` / `Qty per Manufacturing Order` must be a whole number"
					)
				)
		self.total_planned_qty = total

	@frappe.whitelist()
	def get_sales_orders(self):
		data = frappe.db.sql(
			"""select so.name
			from `tabSales Order` so left join `tabSales Order Item` soi on (soi.parenttype = 'Sales Order' and soi.parent = so.name)
			where soi.qty > soi.manufacturing_order_qty  and so.docstatus = 1.0 group by so.name
			order by so.modified DESC""",
			as_dict=1,
		)
		self.sales_order = []
		for row in data:
			self.append("sales_order", {"sales_order": row.name})

	@frappe.whitelist()
	def get_items_for_production(self):
		sales_orders = [row.sales_order for row in self.sales_order]
		items = frappe.db.sql(
			f"""
						select 	soi.name as docname, soi.parent as sales_order, soi.item_code as item_code, soi.bom as bom, itm.mould as mould_no, soi.diamond_quality,
								soi.custom_customer_sample as customer_sample,
								soi.custom_customer_voucher_no as customer_voucher_no,
								soi.custom_customer_gold as customer_gold,
								soi.custom_customer_diamond as customer_diamond,
								soi.custom_customer_stone as customer_stone,
								soi.custom_customer_good as customer_good,
								(soi.qty - soi.manufacturing_order_qty) as pending_qty
						from `tabSales Order Item` soi left join `tabItem` itm on soi.item_code = itm.name
						where soi.parent in ('{"', '".join(sales_orders)}') and soi.qty > soi.manufacturing_order_qty""",
			as_dict=1,
		)
		self.manufacturing_plan_table = []
		for item_row in items:
			so_bom = item_row.get("bom")
			item_code = item_row.get("item_code")
			item_master_bom = frappe.get_value("Item", item_code, "master_bom")
			if so_bom or item_master_bom:
				check_bom_type = frappe.get_value("BOM", so_bom or item_master_bom, "bom_type")
				if check_bom_type == "Sales Order":
					item_row["manufacturing_order_qty"] = item_row.get("pending_qty")
					item_row["qty_per_manufacturing_order"] = 1
					item_row["bom"] = so_bom or item_master_bom
					item_row["customer"] = frappe.db.get_value("Sales Order", item_row["sales_order"], "customer")
					self.append("manufacturing_plan_table", item_row)
				else:
					frappe.throw(
						f"{so_bom or item_master_bom} should be BOM Type <b>Sales Order</b> allowed in Manufacturing Process"
					)
			else:
				frappe.throw(
					f"Sales Order BOM Not Found.</br>Please Set Master BOM for <b>{item_code}</b> into Item Master"
				)


def create_new_bom(self):
	"""
	This Function Creates Manufacturing Process Type BOM from Sales Bom
	"""
	for row in self.manufacturing_plan_table:
		if not row.manufacturing_bom and frappe.db.exists("BOM", row.bom):
			bom_type = frappe.get_value("BOM", {"name": row.bom}, "bom_type")
			if bom_type == "Sales Order":
				manufacturing_bom_name = create_manufacturing_process_bom(self, row)
				if manufacturing_bom_name:
					row.manufacturing_bom = manufacturing_bom_name
			else:
				frappe.throw(f"Manufactuing Bom Creation Error: {row.bom} BOM Type Must be a Sales Order.")


def create_manufacturing_process_bom(self, row):
	doc = get_mapped_doc(
		"BOM",
		row.bom,
		{
			"BOM": {
				"doctype": "BOM",
			}
		},
		ignore_permissions=True,
	)
	doc.is_default = 0
	doc.is_active = 0
	doc.bom_type = "Manufacturing Process"
	doc.save(ignore_permissions=True)
	return doc.name


def cancel_bom(self):
	for row in self.manufacturing_plan_table:
		if row.manufacturing_bom:
			bom = frappe.get_doc("BOM", row.manufacturing_bom)
			row.manufacturing_bom = ""
			bom.docstatus = 2
			bom.save()


def create_manufacturing_order(doc, row):
	cnt = int(row.manufacturing_order_qty / row.qty_per_manufacturing_order)
	for i in range(0, cnt):
		make_manufacturing_order(doc, row)
	frappe.msgprint("Parent Manufacturing Order Created")


def create_subcontracting_order(doc, row):
	for row in doc.manufacturing_plan_table:
		make_subcontracting_order(doc, row)


@frappe.whitelist()
def get_sales_order(source_name, target_doc=None):
	if not target_doc:
		target_doc = frappe.new_doc("Manufacturing Plan")
	elif isinstance(target_doc, str):
		target_doc = frappe.get_doc(json.loads(target_doc))
	if not target_doc.get("sales_order", {"sales_order": source_name}):
		target_doc.append(
			"sales_order",
			{
				"sales_order": source_name,
				"customer": frappe.db.get_value("Sales Order", source_name, "customer"),
			},
		)
	return target_doc


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_pending_ppo_sales_order(doctype, txt, searchfield, start, page_len, filters):
	conditions = " and soi.qty > soi.manufacturing_order_qty and soi.order_form_type <> 'Repair Order' and so.custom_repair_order_form is null"
	if txt:
		conditions += " and so.name like '%%" + txt + "%%' "
	if customer := filters.get("customer"):
		conditions += f" and so.customer = '{customer}'"
	if company := filters.get("company"):
		conditions += f" and so.company = '{company}'"
	if branch := filters.get("branch"):
		conditions += f" and so.branch = '{branch}'"
	if txn_date := filters.get("transaction_date"):
		conditions += f" and so.transaction_date = '{txn_date}'"
	so_data = frappe.db.sql(
		f"""
		select
			distinct so.name, so.transaction_date,
			so.company, so.customer
		from
			`tabSales Order` so, `tabSales Order Item` soi
		where
			so.name = soi.parent
			and so.docstatus = 1
			{conditions}
		order by so.transaction_date Desc
		limit %(page_len)s offset %(start)s """,
		{
			"page_len": page_len,
			"start": start,
		},
		as_dict=1,
	)

	return so_data

@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_repair_pending_ppo_sales_order(doctype, txt, searchfield, start, page_len, filters):
	conditions = " and soi.qty > soi.manufacturing_order_qty and soi.order_form_type='Repair Order' and so.custom_repair_order_form <> '' "
	if txt:
		conditions += " and so.name like '%%" + txt + "%%' "
	if customer := filters.get("customer"):
		conditions += f" and so.customer = '{customer}'"
	if company := filters.get("company"):
		conditions += f" and so.company = '{company}'"
	if branch := filters.get("branch"):
		conditions += f" and so.branch = '{branch}'"
	if txn_date := filters.get("transaction_date"):
		conditions += f" and so.transaction_date = '{txn_date}'"
	so_data = frappe.db.sql(
		f"""
		select
			distinct so.name, so.transaction_date,
			so.company, so.customer
		from
			`tabSales Order` so, `tabSales Order Item` soi
		where
			so.name = soi.parent
			and so.docstatus = 1
			{conditions}
		order by so.transaction_date Desc
		limit %(page_len)s offset %(start)s """,
		{
			"page_len": page_len,
			"start": start,
		},
		as_dict=1,
	)

	return so_data

