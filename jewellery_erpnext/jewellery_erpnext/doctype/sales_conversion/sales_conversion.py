# Copyright (c) 2024, Nirali and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class SalesConversion(Document):
	def on_submit(self):
		make_metal_stock_entry(self)
		pass

	def validate(self):
		pass

	@frappe.whitelist()
	def calculate_metal_conversion(self):
		source_item_purity = frappe.get_all(
			"Item Variant Attribute",
			filters={"parent": self.source_item, "attribute": "Metal Purity"},
			fields=["parent", "attribute", "attribute_value"],
		)
		target_item_purity = frappe.get_all(
			"Item Variant Attribute",
			filters={"parent": self.target_item, "attribute": "Metal Purity"},
			fields=["parent", "attribute", "attribute_value"],
		)
		if source_item_purity and target_item_purity:
			source_attribute_value = float(source_item_purity[0].get("attribute_value"))
			target_attribute_value = float(target_item_purity[0].get("attribute_value"))

			if target_attribute_value != 0:
				target_qty = float((self.source_qty * source_attribute_value) / target_attribute_value)
				alloy_qty = float((target_qty - self.source_qty))
			else:
				frappe.throw("Error: Target Item Purity value is zero.")

		return target_qty, alloy_qty


def make_metal_stock_entry(self):
	target_wh = self.target_warehouse
	source_wh = self.source_warehouse
	se = frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"stock_entry_type": "Repack",
			"custom_sales_conversion": self.name,
			"inventory_type": "Regular Stock",
			"auto_created": 1,
		}
	)
	source_item = []
	target_item = []
	source_item.append(
		{
			"item_code": self.source_item,
			"qty": self.source_qty,
			"inventory_type": "Regular Stock",
			"department": self.department,
			"employee": self.employee,
			"manufacturer": self.manufacturer,
			"s_warehouse": source_wh,
		}
	)
	if self.source_alloy:
		source_item.append(
			{
				"item_code": self.source_alloy,
				"qty": self.source_alloy_qty,
				"inventory_type": "Regular Stock",
				"department": self.department,
				"employee": self.employee,
				"manufacturer": self.manufacturer,
				"s_warehouse": source_wh,
			}
		)
	target_item.append(
		{
			"item_code": self.target_item,
			"qty": self.target_qty,
			"inventory_type": "Regular Stock",
			"department": self.department,
			"employee": self.employee,
			"manufacturer": self.manufacturer,
			"t_warehouse": target_wh,
		}
	)
	if self.target_alloy:
		target_item.append(
			{
				"item_code": self.target_alloy,
				"qty": self.target_alloy_qty,
				"inventory_type": "Regular Stock",
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
				"inventory_type": "Regular Stock",
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
				"inventory_type": "Regular Stock",
				"department": row["department"],
				"employee": row["employee"],
				"manufacturer": row["manufacturer"],
				"t_warehouse": row["t_warehouse"],
			},
		)
	se.save()
	self.stock_entry = se.name
