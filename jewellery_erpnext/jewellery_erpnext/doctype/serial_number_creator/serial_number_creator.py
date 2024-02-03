# Copyright (c) 2024, Nirali and contributors
# For license information, please see license.txt

import json

import frappe
from frappe.model.document import Document

from jewellery_erpnext.jewellery_erpnext.doctype.manufacturing_operation.manufacturing_operation import (
	create_finished_goods_bom,
	create_manufacturing_entry,
	set_values_in_bulk,
)


class SerialNumberCreator(Document):
	def validate(self):
		pass

	def on_submit(self):
		validate_qty(self)
		calulate_id_wise_sum_up(self)
		to_prepare_data_for_make_mnf_stock_entry(self)


def to_prepare_data_for_make_mnf_stock_entry(self):
	id_wise_data_split = {}
	for row in self.fg_details:
		if row.id:
			key = row.id
			if key not in id_wise_data_split:
				id_wise_data_split[key] = []
				id_wise_data_split[key].append(
					{"item_code": row.row_material, "qty": row.qty, "uom": row.uom, "id": row.id}
				)
			else:
				id_wise_data_split[key].append(
					{"item_code": row.row_material, "qty": row.qty, "uom": row.uom, "id": row.id}
				)
	for key, row_data in id_wise_data_split.items():
		se_name = create_manufacturing_entry(self, row_data)
		pmo = frappe.db.get_value(
			"Manufacturing Work Order", self.manufacturing_work_order, "manufacturing_order"
		)
		wo = frappe.get_all("Manufacturing Work Order", {"manufacturing_order": pmo}, pluck="name")
		set_values_in_bulk("Manufacturing Work Order", wo, {"status": "Completed"})
		create_finished_goods_bom(self, se_name)


def validate_qty(self):
	for row in self.fg_details:
		if row.qty == 0:
			frappe.throw("FG Details Table Quantity Zero Not Allowed")


@frappe.whitelist()
def get_operation_details(data, docname, mwo, pmo, company, mnf, dpt, for_fg, design_id_bom):
	exist_snc_doc = frappe.get_all(
		"Serial Number Creator",
		filters={"manufacturing_operation": docname, "docstatus": ["!=", 2]},
		fields=["name"],
	)
	if exist_snc_doc:
		frappe.throw(f"Document Already Created...! {exist_snc_doc[0]['name']}")

	snc_doc = frappe.new_doc("Serial Number Creator")
	mnf_op_doc = frappe.get_doc("Manufacturing Operation", docname)
	data_dict = json.loads(data)
	stock_data = data_dict[0]
	bom_id = data_dict[1]
	mnf_qty = data_dict[2]
	total_qty = data_dict[3]
	bom_doc = frappe.get_doc("BOM", bom_id)
	matched_items = []
	unmatched_items = []
	for mnf_id in range(1, mnf_qty + 1):
		for bom_item in bom_doc.items:
			matched = False
			for data_entry in stock_data:
				if bom_item.item_code == data_entry["item_code"]:
					# Combine information for matched items
					_qty = round(data_entry["qty"] / mnf_qty, 4)
					combined_item = {
						"default_bom_rm": bom_item.item_code,
						"bom_qty": bom_item.qty,
						"row_material": data_entry["item_code"],
						"id": mnf_id,
						"batch_no": data_entry["batch_no"],
						"qty": data_entry["qty"],
						"uom": data_entry["uom"],
						"gross_wt": data_entry["gross_wt"],
					}
					matched_items.append(combined_item)
					matched = True
					break
			if not matched:
				unmatched_items.append({"default_bom_rm": bom_item.item_code, "bom_qty": bom_item.qty})
	for item in matched_items:
		snc_doc.append(
			"fg_details",
			{
				"default_bom_rm": item["default_bom_rm"],
				"bom_qty": item["bom_qty"],
				"row_material": item["row_material"],
				"id": item["id"],
				"batch_no": item["batch_no"],
				"qty": item["qty"],
				"uom": item["uom"],
				"gross_wt": item["gross_wt"],
			},
		)
	# frappe.throw(f"{unmatched_items}Matched= {matched_items}")
	for item in unmatched_items:
		snc_doc.append(
			"fg_details",
			{
				"default_bom_rm": item.get("default_bom_rm", ""),
				"bom_qty": item.get("bom_qty", 0),
			},
		)

	for data_entry in stock_data:
		snc_doc.append(
			"source_table",
			{
				"row_material": data_entry.get("item_code", ""),
				"qty": data_entry.get("qty", 0),
				"uom": data_entry.get("uom", ""),
				# # 'default_bom_rm': bom_item.item_code,
				# # 'bom_qty': bom_item.qty,
				# 'row_material': data_entry['item_code'],
				# # 'id': mnf_id,
				# # 'batch_no': data_entry['batch_no'],
				# 'qty': data_entry['qty'],
				# 'uom': data_entry['uom'],
				# # 'gross_wt': data_entry['gross_wt'],
			},
		)

	snc_doc.type = "Manufacturing"
	snc_doc.manufacturing_operation = mnf_op_doc.name
	snc_doc.manufacturing_work_order = mwo
	snc_doc.parent_manufacturing_order = pmo
	snc_doc.company = company
	snc_doc.manufacturer = mnf
	snc_doc.department = dpt
	snc_doc.for_fg = for_fg
	snc_doc.design_id_bom = design_id_bom
	snc_doc.total_weight = total_qty
	snc_doc.save()
	mnf_op_doc.status = "Finished"
	mnf_op_doc.save()
	frappe.msgprint(
		f"<b>Serial Number Creator</b> Document Created...! <b>Doc NO:</b> {snc_doc.name}"
	)


from decimal import ROUND_HALF_UP, Decimal


def calulate_id_wise_sum_up(self):
	id_qty_sum = {}  # Dictionary to store the sum of 'qty' for each 'id'
	item_wise_total = []
	for row in self.source_table:
		# if row.uom == "cts":
		# 	item_wise={
		# 		"item": row.row_material,
		# 		"qty":round(row.qty * 0.2,3)
		# 	}
		# else:
		item_wise = {
			"item": row.row_material,
			"qty": float(
				Decimal(str(row.qty)).quantize(Decimal("0.000"), rounding=ROUND_HALF_UP)
			),  # round(row.qty,3)
		}
		item_wise_total.append(item_wise)

	for row in self.fg_details:
		if row.id and row.row_material:
			key = row.row_material
			if key not in id_qty_sum:
				id_qty_sum[key] = float(Decimal("0.000"))  # round(0,3)

			# if row.uom == "cts":
			# 	id_qty_sum[key] += round(row.qty * 0.2,3)
			# else:
			# id_qty_sum[key] += round(row.qty,3)
			id_qty_sum[key] += float(
				Decimal(str(row.qty)).quantize(Decimal("0.000"), rounding=ROUND_HALF_UP)
			)
	id_qty_sum = {key: round(float(value), 3) for key, value in id_qty_sum.items()}
	# frappe.throw(f"{item_wise_total}</br>{id_qty_sum}")
	for (row_material), qty_sum in id_qty_sum.items():
		for row in item_wise_total:
			if row_material == row["item"] and round(qty_sum, 3) != round(row["qty"], 3):
				frappe.throw(
					f"Sum of Qty of Row Material <b>{row_material}</b> does not match </br><b>Your Sum of:</b>{round(qty_sum, 3)}</br><b>Must Be Need</b>:{row['qty']}"
				)
