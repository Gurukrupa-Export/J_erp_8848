# Copyright (c) 2024, Nirali and contributors
# For license information, please see license.txt

import frappe
from erpnext.controllers.queries import get_batch_no
from erpnext.stock.doctype.batch.batch import get_batch_qty
from frappe.model.document import Document


class MetalConversions(Document):
	def on_submit(self):
		if self.multiple_metal_converter == 0:
			self.get_alloy_bailance()
			make_metal_stock_entry(self)
		if self.multiple_metal_converter == 1:
			if self.mc_source_table == []:
				frappe.throw("Source Item Missing")
			if self.m_target_qty <= 0 or self.m_target_item == None:
				frappe.throw("Target Item or Target Qty Missing")
			if self.alloy_qty <= 0 or self.alloy == None:
				frappe.throw("Alloy Item or Alloy Qty Missing")
			self.get_alloy_bailance()
			make_multiple_metal_stock_entry(self)

		if self.multiple_metal_converter == 0:
			if self.target_qty <= 0 or self.source_qty <= 0:
				frappe.throw("Source Qty or Target Qty not allowed Zero to post transaction")

	def validate(self):
		if not self.batch and self.multiple_metal_converter == 0:
			frappe.throw("Batch Missing")

	@frappe.whitelist()
	def clear_fields(self):
		for field in self.meta.fields:
			if field.fieldname not in (
				"name",
				"creation",
				"modified",
				"multiple_metal_converter",
				"employee",
				"company",
				"department",
				"manufacturer",
				"date",
				"source_warehouse",
				"target_warehouse",
			):
				self.set(field.fieldname, None)

	@frappe.whitelist()
	def set_attribute_value(self):
		return frappe.db.get_value(
			"Item Variant Attribute", {"parent": self.source_item}, "attribute_value"
		)

	@frappe.whitelist()
	def get_batch_detail(self):
		bal_qty = ""
		supplier = ""
		customer = ""
		inventory_type = ""
		error = []
		if self.batch:
			bal_qty = get_batch_qty(batch_no=self.batch, warehouse=self.source_warehouse)
			reference_doctype, reference_name = frappe.get_value(
				"Batch", self.batch, ["reference_doctype", "reference_name"]
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

	@frappe.whitelist()
	def get_child_batch_detail(self, table_item, talble_source_warehouse, table_batch):
		bal_qty = None
		supplier = None
		customer = None
		inventory_type = None
		error = []
		if table_batch:
			bal_qty = get_batch_qty(batch_no=table_batch, warehouse=self.source_warehouse)
			reference_doctype, reference_name = frappe.get_value(
				"Batch", table_batch, ["reference_doctype", "reference_name"]
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
	def calculate_metal_conversion(self):

		source_item_purity = get_metal_purity_percentage(self.source_item)
		target_item_purity = get_metal_purity_percentage(self.target_item)

		if not source_item_purity:
			frappe.throw("<b>Source Item</b> in Attribute Value doctype <b>Purity Percentage</b> Missing")
		if not target_item_purity:
			frappe.throw("<b>Target Item</b> in Attribute Value doctype <b>Purity Percentage</b> Missing")

		if source_item_purity:
			if target_item_purity:
				if target_item_purity != 0:
					target_qty = float((self.source_qty * source_item_purity) / target_item_purity)
					alloy_qty = round(float((target_qty - self.source_qty)), 3)
				else:
					frappe.throw("Error: Target Item Purity value is zero.")
			else:
				frappe.throw("Error: Target Item Purity not found.")
		else:
			frappe.throw("Error: Source Item Purity not found.")
		return target_qty, alloy_qty

	@frappe.whitelist()
	def calculate_Multiple_conversion(self):
		if not self.m_target_item:
			frappe.throw("Target Item Code Missing")

		target_item_purity = get_metal_purity_percentage(self.m_target_item)
		if not target_item_purity:
			frappe.throw("<b>Target Item</b> in Attribute Value doctype <b>Purity Percentage</b> Missing")

		sum_total = 0
		sum_source_qty = 0
		inventory_types_source = set()
		for row in self.mc_source_table:
			inventory_types_source.add(row.inventory_type)
			sum_total += row.total
			sum_source_qty += row.qty
		target_qty = round(sum_total / target_item_purity, 3)
		alloy_qty = round(float((target_qty - sum_source_qty)), 3)
		return target_qty, alloy_qty

	@frappe.whitelist()
	def get_alloy_bailance(self):
		if self.multiple_metal_converter == 0:
			_alloy_qty = self.source_alloy_qty or self.target_alloy_qty
			if _alloy_qty:
				_alloy = self.source_alloy or self.target_alloy
				if not _alloy:
					frappe.throw("Alloy Missing")
				alloy_qty_bail = frappe.get_value(
					"Bin", {"warehouse": self.source_warehouse, "item_code": _alloy}, "actual_qty"
				)

				if alloy_qty_bail:
					if _alloy_qty > alloy_qty_bail:
						frappe.throw(
							f"Alloy <b>{_alloy}</b> Bailance qty is {alloy_qty_bail}</br>We need {_alloy_qty} Respective <b>{self.source_warehouse}</b> Warehouse."
						)
				else:
					frappe.throw(
						f"Alloy <b>{_alloy}</b> Stock Not Available Respective <b>{self.source_warehouse}</b> Warehouse."
					)
		else:
			if self.alloy_qty:
				if not self.alloy:
					frappe.throw("Alloy Missing")
				actual_qty = frappe.get_value(
					"Bin", {"warehouse": self.source_warehouse, "item_code": self.alloy}, "actual_qty"
				)

				if actual_qty:
					if self.alloy_qty > actual_qty:
						frappe.throw(
							f"Alloy <b>{self.alloy}</b> Bailance qty is {actual_qty}</br>We need {self.alloy_qty} Respective <b>{self.source_warehouse}</b> Warehouse."
						)
				else:
					frappe.throw(
						f"Alloy <b>{self.alloy}</b> Stock Not Available Respective <b>{self.source_warehouse}</b> Warehouse."
					)

	@frappe.whitelist()
	def get_mc_table_purity(self, item_code, qty):
		if not item_code:
			frappe.throw("Item Code Missing")

		source_item_purity = get_metal_purity_percentage(item_code)
		if not source_item_purity:
			frappe.throw("<b>Source Item</b> in Attribute Value doctype <b>Purity Percentage</b> Missing")

		total = qty * source_item_purity
		return total


def get_metal_purity_percentage(item_code):
	item_variant_attribute_value = frappe.get_all(
		"Item Variant Attribute",
		filters={"parent": item_code, "attribute": "Metal Purity"},
		fields=["parent", "attribute", "attribute_value"],
	)
	if not item_variant_attribute_value:
		frappe.throw("Attribute Value Missing")
	target_purity = float(
		frappe.get_value(
			"Attribute Value", item_variant_attribute_value[0].get("attribute_value"), "purity_percentage"
		)
	)
	return target_purity


def make_metal_stock_entry(self):
	target_wh = self.target_warehouse
	source_wh = self.source_warehouse
	inventory_type = self.inventory_type
	batch_no = self.batch
	se = frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"stock_entry_type": "Repack-Metal Conversion",
			"purpose": "Repack",
			"company": self.company,
			"custom_metal_conversions": self.name,
			"inventory_type": inventory_type,
			"_customer": self.customer,
			"auto_created": 1,
			"branch": self.branch,
		}
	)
	source_item = []
	target_item = []
	source_item.append(
		{
			"item_code": self.source_item,
			"qty": self.source_qty,
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
			"item_code": self.target_item,
			"qty": self.target_qty,
			"inventory_type": inventory_type,
			"department": self.department,
			"employee": self.employee,
			"manufacturer": self.manufacturer,
			"t_warehouse": target_wh,
		}
	)
	if self.source_alloy and self.source_alloy_qty > 0:
		source_item.append(
			{
				"item_code": self.source_alloy,
				"qty": self.source_alloy_qty,
				"inventory_type": "Regular Stock",
				"batch_no": None,
				"department": self.department,
				"employee": self.employee,
				"manufacturer": self.manufacturer,
				"s_warehouse": source_wh,
			}
		)
	if self.target_alloy and self.target_alloy_qty > 0:
		target_item.append(
			{
				"item_code": self.target_alloy,
				"qty": self.target_alloy_qty,
				"inventory_type": inventory_type,
				"department": self.department,
				"employee": self.employee,
				"manufacturer": self.manufacturer,
				"t_warehouse": target_wh,
			}
		)
	inventory_types_source = set()
	inventory_types_target = set()
	inventory_types_final = set()
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
		inventory_types_source.add(row["inventory_type"])
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
		inventory_types_target.add(row["inventory_type"])

	inventory_types_final = inventory_types_source.union(inventory_types_target)
	if len(inventory_types_final) > 1:
		frappe.throw("Inventory types in <b>Source Table</b> are not consistent. Please check.")
	se.save()
	se.submit()
	self.stock_entry = se.name


def make_multiple_metal_stock_entry(self):
	source_wh = self.source_warehouse
	se = frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"stock_entry_type": "Repack-Metal Conversion",
			"purpose": "Repack",
			"company": self.company,
			"custom_metal_conversions": self.name,
			# "inventory_type": inventory_type,
			"_customer": self.customer,
			"auto_created": 1,
			"branch": self.branch,
		}
	)
	se.branch = self.branch
	source_item = []
	target_item = []
	inventory_types_source = set()
	for row in self.mc_source_table:
		source_item.append(
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
		se.inventory_type = row.inventory_type
		inventory_types_source.add(row.inventory_type)
	if len(inventory_types_source) > 1:
		frappe.throw("Inventory types in <b>Source Table</b> are not consistent. Please check.")

	target_item.append(
		{
			"item_code": self.m_target_item,
			"qty": self.m_target_qty,
			"inventory_type": se.inventory_type,
			"department": self.department,
			"employee": self.employee,
			"manufacturer": self.manufacturer,
			"t_warehouse": source_wh,
		}
	)
	if self.alloy and self.alloy_qty > 0:
		if self.alloy_check == 0:
			source_item.append(
				{
					"item_code": self.alloy,
					"qty": self.alloy_qty,
					"inventory_type": se.inventory_type,
					"batch_no": None,
					"department": self.department,
					"employee": self.employee,
					"manufacturer": self.manufacturer,
					"s_warehouse": source_wh,
				}
			)
		if self.alloy_check == 1:
			target_item.append(
				{
					"item_code": self.alloy,
					"qty": self.alloy_qty,
					"inventory_type": se.inventory_type,
					"department": self.department,
					"employee": self.employee,
					"manufacturer": self.manufacturer,
					"t_warehouse": source_wh,
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


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_filtered_batches(doctype, txt, searchfield, start, page_len, filters):
	data = get_batch_no(doctype, txt, searchfield, start, page_len, filters)
	return data


def get_batch_details(batch):
	batch_details = frappe.get_doc("Batch", batch)
	return batch_details
