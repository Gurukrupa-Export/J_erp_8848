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
		is_subcontracting = False
		for row in self.manufacturing_plan_table:
			if row.docname:
				update_existing(
					"Sales Order Item",
					row.docname,
					"manufacturing_order_qty",
					f"manufacturing_order_qty + {cint(row.manufacturing_order_qty) + cint(row.subcontracting_qty)}",
				)
			create_manufacturing_order(self, row)
			if row.subcontracting:
				is_subcontracting = True
				# create_subcontracting_order(self, row)
			if row.manufacturing_bom is None:
				frappe.throw(f"Row:{row.idx} Manufacturing Bom Missing")

		if is_subcontracting:
			create_subcontracting_order(self)

	def on_cancel(self):
		for row in self.manufacturing_plan_table:
			update_existing(
				"Sales Order Item",
				row.docname,
				"manufacturing_order_qty",
				f"greatest(manufacturing_order_qty - {cint(row.manufacturing_order_qty) + cint(row.subcontracting_qty)},0)",
			)

	def validate(self):
		self.validate_qty_with_bom_creation()
		# create_new_bom(self)

	def validate_qty_with_bom_creation(self):
		total = 0
		for row in self.manufacturing_plan_table:
			# Validate Qty
			if not row.subcontracting:
				row.subcontracting_qty = 0
				row.supplier = None
			if (row.manufacturing_order_qty + row.subcontracting_qty) > row.pending_qty:
				error_message = _("Row #{0}: Total Order qty cannot be greater than {1}").format(
					row.idx, row.pending_qty
				)
				frappe.throw(error_message)
			total += cint(row.manufacturing_order_qty) + cint(row.subcontracting_qty)
			if row.qty_per_manufacturing_order == 0:
				frappe.throw(_("Qty per Manufacturing Order cannot be Zero"))
			if row.manufacturing_order_qty % row.qty_per_manufacturing_order != 0:
				error_message = _(
					"Row #{0}: `Manufacturing Order Qty` / `Qty per Manufacturing Order` must be a whole number"
				).format(row.idx)
				frappe.throw(error_message)

			# Create BOM
			if not row.manufacturing_bom and frappe.db.exists("BOM", row.bom):
				bom_type = frappe.get_value("BOM", {"name": row.bom}, "bom_type")
				if bom_type == "Sales Order":
					manufacturing_bom_name = create_manufacturing_process_bom(self, row)
					if manufacturing_bom_name:
						row.manufacturing_bom = manufacturing_bom_name
				else:
					frappe.throw(
						_("Manufactuing Bom Creation Error: {0} BOM Type Must be a Sales Order.").format(row.bom)
					)
		self.total_planned_qty = total

	@frappe.whitelist()
	def get_sales_orders(self):
		SalesOrder = frappe.qb.DocType("Sales Order")
		SalesOrderItem = frappe.qb.DocType("Sales Order Item")

		query = (
			frappe.qb.from_(SalesOrder)
			.left_join(SalesOrderItem)
			.on((SalesOrderItem.parenttype == "Sales Order") & (SalesOrderItem.parent == SalesOrder.name))
			.select(SalesOrder.name)
			.where(
				(SalesOrder.docstatus == 1.0) & (SalesOrderItem.qty > SalesOrderItem.manufacturing_order_qty)
			)
			.groupby(SalesOrder.name)
			.orderby(SalesOrder.modified, order=frappe.qb.desc)
		)

		data = query.run(as_dict=True)

		self.sales_order = []
		for row in data:
			self.append("sales_order", {"sales_order": row.name})

	@frappe.whitelist()
	def get_items_for_production(self):
		if not self.manufacturing_work_order:
			SalesOrderItem = frappe.qb.DocType("Sales Order Item")
			Item = frappe.qb.DocType("Item")

			sales_orders = [row.sales_order for row in self.sales_order]

			query = (
				frappe.qb.from_(SalesOrderItem)
				.left_join(Item)
				.on(SalesOrderItem.item_code == Item.name)
				.select(
					SalesOrderItem.name.as_("docname"),
					SalesOrderItem.parent.as_("sales_order"),
					SalesOrderItem.item_code,
					SalesOrderItem.bom,
					Item.mould.as_("mould_no"),
					SalesOrderItem.diamond_quality,
					SalesOrderItem.custom_customer_sample.as_("customer_sample"),
					SalesOrderItem.custom_customer_voucher_no.as_("customer_voucher_no"),
					SalesOrderItem.custom_customer_gold.as_("customer_gold"),
					SalesOrderItem.custom_customer_diamond.as_("customer_diamond"),
					SalesOrderItem.custom_customer_stone.as_("customer_stone"),
					SalesOrderItem.custom_customer_good.as_("customer_good"),
					SalesOrderItem.custom_customer_weight.as_("customer_weight"),
					(SalesOrderItem.qty - SalesOrderItem.manufacturing_order_qty).as_("pending_qty"),
					SalesOrderItem.order_form_type,
					SalesOrderItem.custom_repair_type.as_("repair_type"),
					SalesOrderItem.custom_product_type.as_("product_type"),
					SalesOrderItem.serial_no,
					SalesOrderItem.serial_id_bom,
				)
				.where(
					(SalesOrderItem.parent.isin(sales_orders))
					& (SalesOrderItem.qty > SalesOrderItem.manufacturing_order_qty)
				)
			)

			if self.setting_type:
				query = query.where(SalesOrderItem.setting_type == self.setting_type)

			items = query.run(as_dict=True)

			self.manufacturing_plan_table = []
			for item_row in items:
				so_bom = item_row.get("bom")
				item_code = item_row.get("item_code")
				item_master_bom = frappe.get_value("Item", item_code, "master_bom")
				if so_bom or item_master_bom:
					check_bom_type = frappe.get_value("BOM", so_bom or item_master_bom, "bom_type")
					if check_bom_type == "Sales Order":
						item_row["manufacturing_order_qty"] = item_row.get("pending_qty")
						if self.is_subcontracting:
							item_row["subcontracting"] = self.is_subcontracting
							item_row["subcontracting_qty"] = item_row.get("pending_qty")
							item_row["supplier"] = self.supplier
							item_row["estimated_delivery_date"] = self.estimated_date
							item_row["purchase_type"] = self.purchase_type
							item_row["manufacturing_order_qty"] = 0

						item_row["qty_per_manufacturing_order"] = 1
						item_row["bom"] = so_bom or item_master_bom
						item_row["customer"] = frappe.db.get_value(
							"Sales Order", item_row["sales_order"], "customer"
						)
						item_row["order_form_type"] = item_row.get("order_form_type")
						self.append("manufacturing_plan_table", item_row)
					else:
						frappe.throw(
							f"{so_bom or item_master_bom} should be BOM Type <b>Sales Order</b> allowed in Manufacturing Process"
						)
				else:
					frappe.throw(
						f"Sales Order BOM Not Found.</br>Please Set Master BOM for <b>{item_code}</b> into Item Master"
					)
		else:
			self.manufacturing_plan_table = []
			mwo_lst = [row.manufacturing_work_order for row in self.manufacturing_work_order]

			mwo_data = []
			for row in mwo_lst:
				mwo_data.append(
					frappe.db.get_value(
						"Manufacturing Work Order", row, ["name", "item_code", "master_bom", "qty"], as_dict=1
					)
				)

			unique_item_dict = {}
			for row in mwo_data:
				if unique_item_dict.get(row.item_code):
					unique_item_dict[row.item_code]["qty"] += row.qty
				else:
					unique_item_dict[row.item_code] = {"qty": row.qty, "mwo": row.name}

			for row in unique_item_dict:
				self.append(
					"manufacturing_plan_table",
					{
						"item_code": row,
						"pending_qty": unique_item_dict[row]["qty"],
						"manufacturing_order_qty": unique_item_dict[row]["qty"],
						"qty_per_manufacturing_order": unique_item_dict[row]["qty"],
						"mwo": unique_item_dict[row]["mwo"],
					},
				)


# def create_new_bom(self):
# 	"""
# 	This Function Creates Manufacturing Process Type BOM from Sales Bom
# 	"""
# 	for row in self.manufacturing_plan_table:
# 		if not row.manufacturing_bom and frappe.db.exists("BOM", row.bom):
# 			bom_type = frappe.get_value("BOM", {"name": row.bom}, "bom_type")
# 			if bom_type == "Sales Order":
# 				manufacturing_bom_name = create_manufacturing_process_bom(self, row)
# 				if manufacturing_bom_name:
# 					row.manufacturing_bom = manufacturing_bom_name
# 			else:
# 				frappe.throw(f"Manufactuing Bom Creation Error: {row.bom} BOM Type Must be a Sales Order.")


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
	doc.custom_creation_doctype = self.doctype
	doc.is_default = 0
	doc.is_active = 0
	doc.bom_type = "Manufacturing Process"
	doc.save(ignore_permissions=True)
	doc.db_set("custom_creation_docname", self.name)
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
	frappe.msgprint(_("Parent Manufacturing Order Created"))


def create_subcontracting_order(doc):
	# for row in doc.manufacturing_plan_table:
	make_subcontracting_order(doc)


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
	SalesOrder = frappe.qb.DocType("Sales Order")
	SalesOrderItem = frappe.qb.DocType("Sales Order Item")

	conditions = (
		(SalesOrderItem.qty > SalesOrderItem.manufacturing_order_qty)
		& (SalesOrderItem.order_form_type != "Repair Order")
		& (SalesOrder.custom_repair_order_form.isnull())
	)

	if txt:
		conditions &= SalesOrder.name.like(f"%{txt}%")

	if customer := filters.get("customer"):
		conditions &= SalesOrder.customer == customer

	if company := filters.get("company"):
		conditions &= SalesOrder.company == company

	if branch := filters.get("branch"):
		conditions &= SalesOrder.branch == branch

	if txn_date := filters.get("transaction_date"):
		conditions &= SalesOrder.transaction_date == txn_date

	query = (
		frappe.qb.from_(SalesOrder)
		.distinct()
		.from_(SalesOrderItem)
		.select(SalesOrder.name, SalesOrder.transaction_date, SalesOrder.company, SalesOrder.customer)
		.where((SalesOrder.name == SalesOrderItem.parent) & (SalesOrder.docstatus == 1) & conditions)
		.orderby(SalesOrder.transaction_date, order=frappe.qb.desc)
		.limit(page_len)
		.offset(start)
	)
	so_data = query.run(as_dict=True)

	return so_data


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_repair_pending_ppo_sales_order(doctype, txt, searchfield, start, page_len, filters):
	SalesOrder = frappe.qb.DocType("Sales Order")
	SalesOrderItem = frappe.qb.DocType("Sales Order Item")

	conditions = (SalesOrderItem.qty > SalesOrderItem.manufacturing_order_qty) & (
		SalesOrderItem.order_form_type == "Repair Order"
	)

	if txt:
		conditions &= SalesOrder.name.like(f"%{txt}%")

	if customer := filters.get("customer"):
		conditions &= SalesOrder.customer == customer

	if company := filters.get("company"):
		conditions &= SalesOrder.company == company

	if branch := filters.get("branch"):
		conditions &= SalesOrder.branch == branch

	if txn_date := filters.get("transaction_date"):
		conditions &= SalesOrder.transaction_date == txn_date

	query = (
		frappe.qb.from_(SalesOrder)
		.distinct()
		.from_(SalesOrderItem)
		.select(SalesOrder.name, SalesOrder.transaction_date, SalesOrder.company, SalesOrder.customer)
		.where((SalesOrder.name == SalesOrderItem.parent) & conditions)
		.orderby(SalesOrder.transaction_date, order=frappe.qb.desc)
		.limit(page_len)
		.offset(start)
	)

	so_data = query.run(as_dict=True)
	return so_data
