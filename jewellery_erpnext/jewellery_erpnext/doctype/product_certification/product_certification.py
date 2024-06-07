# Copyright (c) 2023, Nirali and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.mapper import get_mapped_doc
from frappe.utils import cint

from jewellery_erpnext.jewellery_erpnext.doctype.main_slip.main_slip import get_item_loss_item
from jewellery_erpnext.jewellery_erpnext.doctype.product_certification.doc_events.utils import (
	create_po,
	create_repack_entry,
	update_bom_details,
)


class ProductCertification(Document):
	def validate(self):
		if self.department and not frappe.db.exists("Warehouse", {"department": self.department}):
			frappe.throw(_("Please set warehouse for selected Department"))

		if self.supplier and not frappe.db.exists("Warehouse", {"subcontractor": self.supplier}):
			frappe.throw(_("Please set warehouse for selected supplier"))

		self.validate_items()
		self.update_bom()
		self.get_exploded_table()
		self.distribute_amount()

	def validate_items(self):
		if self.type == "Issue":
			return
		for row in self.product_details:
			if not frappe.db.get_value(
				"Product Details",
				{
					"parent": self.receive_against,
					"serial_no": row.serial_no,
					"item_code": row.item_code,
					"manufacturing_work_order": row.manufacturing_work_order,
				},
			):
				# frappe.throw(_(f"Row #{row.idx}: item not found in {self.receive_against}"))
				frappe.throw(_("Row #{0}: item not found in {1}").format(row.idx, self.receive_against))

	def update_bom(self):
		if self.service_type in ["Hall Marking Service", "Diamond Certificate service"]:
			for row in self.product_details:
				if not (row.serial_no or row.manufacturing_work_order):
					# frappe.throw(_(f"Row #{row.idx}: Either select serial no or manufacturing work order"))
					frappe.throw(
						_("Row #{0}: Either select serial no or manufacturing work order").format(row.idx)
					)
				if row.bom:
					continue
				if row.serial_no:
					row.bom = frappe.db.get_value("BOM", {"tag_no": row.serial_no}, "name")
				if not row.bom:
					row.bom = frappe.db.get_value("Item", row.item_code, "master_bom")
				if not row.bom:
					# frappe.throw(_(f"Row #{row.idx}: BOM not found for item or serial no"))
					frappe.throw(_("Row #{0}: BOM not found for item or serial no").format(row.idx))

	def distribute_amount(self):
		if not self.exploded_product_details:
			return
		length = len(self.exploded_product_details)
		if self.type == "Issue":
			self.total_amount = 0
		amt = self.total_amount / length
		for row in self.exploded_product_details:
			row.amount = amt

	def on_submit(self):
		create_stock_entry(self)
		self.update_huid()
		create_po(self)
		update_bom_details(self)

	def update_huid(self):
		for row in self.exploded_product_details:
			if row.serial_no:
				add_to_serial_no(row.serial_no, self, row)
			elif row.manufacturing_work_order:
				frappe.set_value("Manufacturing Work Order", row.manufacturing_work_order, "huid", row.huid)

	def get_exploded_table(self):
		exploded_product_details = []
		if self.service_type in ["Hall Marking Service", "Diamond Certificate service"]:
			cat_det = frappe.get_all(
				"Certification Settings", {"parent": "Jewellery Settings"}, ["category", "count"]
			)
			custom_cat = {row.category: row.count for row in cat_det}
			metal_det = None
			for row in self.product_details:
				metal_touch = ""
				metal_colour = frappe.db.get_value("BOM", row.bom, "metal_colour")
				count = 1
				if row.manufacturing_work_order:
					mwo = frappe.db.get_value(
						"Manufacturing Work Order",
						row.manufacturing_work_order,
						["department", "qty", "metal_touch", "metal_colour"],
						as_dict=1,
					)
					if self.department != mwo.department:
						# frappe.throw(_(f"Manufacturing Work Order should be in '{self.department}' department"))
						frappe.throw(_("Manufacturing Work Order should be in '{0}' department").format(row.idx))
					count *= cint(mwo.get("qty"))
					metal_touch = mwo.get("metal_touch")
					metal_colour = mwo.get("metal_colour")
				else:
					metal_det = frappe.db.get_all("BOM Metal Detail", {"parent": row.bom}, "DISTINCT metal_touch")
					# metal_det = frappe.db.sql(
					# 	f"""SELECT DISTINCT metal_touch FROM `tabBOM Metal Detail`
					# 					where parent = '{row.bom}'""",
					# 	as_dict=1,
					# )
					count *= cint(len(metal_det))

				if row.category in custom_cat:
					count *= custom_cat.get(row.category, 1)

				existing = []
				for i in self.exploded_product_details:
					if (
						(row.item_code == i.item_code or row.item_code == "")
						and (row.serial_no == i.serial_no or row.serial_no == "")
						and (
							row.manufacturing_work_order == i.manufacturing_work_order
							or row.manufacturing_work_order == ""
						)
					):
						existing.append(i)
				# existing = self.get(
				# 	"exploded_product_details",
				# 	{
				# 		"item_code": row.item_code,
				# 		"serial_no": row.serial_no,
				# 		"manufacturing_work_order": row.manufacturing_work_order,
				# 	},
				# )
				if existing and len(existing) == count:
					continue

				bom_weights = frappe.db.get_value(
					"BOM",
					row.bom,
					[
						"gross_weight",
						"metal_and_finding_weight",
						"diamond_weight",
						"gemstone_weight",
						"other_weight",
					],
					as_dict=1,
				)

				for i in range(0, count):
					if metal_det:
						if count == 2 and len(metal_det) < count:
							metal_touch = metal_det[0].get("metal_touch")
						else:
							metal_touch = metal_det[i].get("metal_touch")
					if existing and metal_touch in [a.get("metal_touch") for a in existing]:
						continue
					exploded_product_details.append(
						{
							"item_code": row.item_code,
							"serial_no": row.serial_no,
							"bom": row.bom,
							"gross_weight": bom_weights["gross_weight"] / count,
							"gold_weight": bom_weights["metal_and_finding_weight"] / count,
							"chain_weight": bom_weights["gemstone_weight"] / count,
							"other_weight": bom_weights["other_weight"] / count,
							"diamond_weight": bom_weights["diamond_weight"] / count,
							"manufacturing_work_order": row.manufacturing_work_order,
							"supply_raw_material": bool(row.manufacturing_work_order),
							"metal_touch": metal_touch,
							"metal_colour": metal_colour,
							"category": row.category,
							"sub_category": row.sub_category,
						}
					)

		elif self.service_type in ["Fire SI Services", "XRF Services"]:
			pure_item = frappe.db.get_value("Manufacturing Setting", self.company, "pure_gold_item")
			if not pure_item:
				frappe.throw(_("Please mention Pure Item in Manufacturing Setting"))

			existing_data = []
			for row in self.exploded_product_details:
				existing_data.append([row.main_slip, row.tree_no])
			for row in self.product_details:
				if [row.main_slip, row.tree_no] not in existing_data:
					exploded_product_details.append(
						{"item_code": row.item_code, "main_slip": row.main_slip, "tree_no": row.tree_no}
					)
					if self.service_type == "Fire SI Services":
						exploded_product_details.append(
							{"item_code": pure_item, "main_slip": row.main_slip, "tree_no": row.tree_no}
						)
					loss_item = get_item_loss_item(self.company, row.item_code, "M")
					exploded_product_details.append(
						{"item_code": loss_item, "main_slip": row.main_slip, "tree_no": row.tree_no}
					)
					row.loss_item = loss_item
				row.pure_item = pure_item

		for row in exploded_product_details:
			self.append("exploded_product_details", row)

	@frappe.whitelist()
	def get_item_from_main_slip(self, main_slip):
		metal = frappe.db.get_value(
			"Main Slip",
			main_slip,
			["metal_type", "metal_touch", "metal_purity", "metal_colour", "tree_number"],
			as_dict=1,
		)
		from jewellery_erpnext.utils import get_item_from_attribute

		return {
			"tree_no": metal.tree_number,
			"item_code": get_item_from_attribute(
				metal.metal_type, metal.metal_touch, metal.metal_purity, metal.metal_colour
			),
		}


def create_stock_entry(doc):
	if doc.type == "Issue" or doc.service_type in [
		"Hall Marking Service",
		"Diamond Certificate service",
	]:

		se_doc = frappe.new_doc("Stock Entry")
		se_doc.stock_entry_type = get_stock_entry_type(doc.service_type, doc.type)
		se_doc.company = doc.company
		se_doc.product_certification = doc.name
		warehouse_type = "Manufacturing"
		if doc.service_type in ["Fire SI Services", "XRF Services"]:
			warehouse_type = "Raw Material"
		s_warehouse = frappe.db.exists(
			"Warehouse",
			{
				"department": doc.department,
				"warehouse_type": warehouse_type,
				"disabled": 0,
			},
		)
		t_warehouse = frappe.db.exists(
			"Warehouse",
			{
				"subcontractor": doc.supplier,
				"warehouse_type": warehouse_type,
				"disabled": 0,
			},
		)

		added_mwo = []
		added_serial = []
		for row in doc.exploded_product_details:
			if row.supply_raw_material and row.manufacturing_work_order not in added_mwo:
				get_stock_item_against_mwo(se_doc, doc, row, s_warehouse, t_warehouse)
				added_mwo.append(row.manufacturing_work_order)
			else:
				if row.serial_no in added_serial:
					continue
				added_serial.append(row.serial_no)
				se_doc.append(
					"items",
					{
						"item_code": row.item_code,
						"serial_no": row.serial_no,
						"qty": 1 if row.serial_no else row.gross_weight,
						"s_warehouse": s_warehouse if doc.type == "Issue" else t_warehouse,
						"t_warehouse": t_warehouse if doc.type == "Issue" else s_warehouse,
						"Inventory_type": "Regular Stock",
						"reference_doctype": "Serial No",
						"reference_docname": row.serial_no,
						"serial_and_batch_bundle": None,
						"use_serial_batch_fields": True,
						"gross_weight": row.gross_weight,
					},
				)
		se_doc.inventory_type = "Regular Stock"
		se_doc.save()
		se_doc.submit()
		frappe.msgprint(_("Stock Entry created"))
	elif doc.type == "Receive" and doc.service_type in ["Fire SI Services", "XRF Services"]:
		create_repack_entry(doc)


def get_stock_entry_type(txn_type, purpose):
	if purpose == "Issue":
		if txn_type == "Hall Marking Service":
			return "Material Issue for Hallmarking"
		else:
			return "Material Issue for Certification"
	else:
		if txn_type == "Hall Marking Service":
			return "Material Receipt for Hallmarking"
		else:
			return "Material Receipt for Certification"


def get_stock_item_against_mwo(se_doc, doc, row, s_warehouse, t_warehouse):
	if doc.type == "Issue":
		target_wh = frappe.get_value(
			"Warehouse",
			{"department": doc.department, "warehouse_type": "Manufacturing"},
			"name",
		)
		filters = [
			["Stock Entry Detail", "custom_manufacturing_work_order", "=", row.manufacturing_work_order],
			["Stock Entry Detail", "manufacturing_operation", "is", "set"],
			["Stock Entry Detail", "t_warehouse", "=", target_wh],
			["Stock Entry Detail", "employee", "is", "not set"],
		]
	else:
		filters = [
			["Stock Entry", "product_certification", "=", doc.receive_against],
			["Stock Entry Detail", "reference_docname", "=", row.manufacturing_work_order],
			["Stock Entry Detail", "reference_doctype", "=", "Manufacturing Work Order"],
		]
	stock_entries = frappe.get_all(
		"Stock Entry",
		filters=filters,
		fields=[
			"`tabStock Entry Detail`.item_code",
			"`tabStock Entry Detail`.qty",
			"`tabStock Entry Detail`.batch_no",
		],
		join="right join",
	)
	if len(stock_entries) < 1:
		frappe.msgprint(
			f"No Stock entry Found against the Manufacturing Work Order: <strong> {row.manufacturing_work_order}</strong>"
		)

	for item in stock_entries:
		se_doc.append(
			"items",
			{
				"item_code": item.item_code,
				"qty": item.qty,
				"s_warehouse": s_warehouse if doc.type == "Issue" else t_warehouse,
				"t_warehouse": t_warehouse if doc.type == "Issue" else s_warehouse,
				"Inventory_type": "Regular Stock",
				"reference_doctype": "Manufacturing Work Order",
				"reference_docname": row.manufacturing_work_order,
				"use_serial_batch_fields": True,
				"batch_no": item.get("batch_no"),
			},
		)


@frappe.whitelist()
def create_product_certification_receive(source_name, target_doc=None):
	def set_missing_values(source, target):
		target.type = "Receive"

	doc = get_mapped_doc(
		"Product Certification",
		source_name,
		{
			"Product Certification": {
				"doctype": "Product Certification",
				"field_map": {"name": "receive_against"},
				"field_no_map": ["date"],
			},
		},
		target_doc,
		set_missing_values,
		ignore_permissions=True,
	)

	return doc


def add_to_serial_no(serial_no, doc, row):
	serial_doc = frappe.get_doc("Serial No", serial_no)
	existing_data = [huild.huid for huild in serial_doc.huid]
	if row.huid and row.huid not in existing_data:
		serial_doc.append("huid", {"huid": row.huid, "date": doc.date})
	serial_doc.save()
