# Copyright (c) 2024, Nirali and contributors
# For license information, please see license.txt

import frappe
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
		if not self.batch:
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

		batch_qty = get_batches(self.source_item, self.source_warehouse, self.company)
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
				inventory_type = "Regular Stock"
			if ref_doctype == "Stock Entry":
				customer, inventory_type = frappe.get_value(
					ref_doctype, ref_name, ["_customer", "inventory_type"]
				)
		return bal_qty or None, supplier or None, customer or None, inventory_type or None

	@frappe.whitelist()
	def get_child_batch_detail(self, table_item, talble_source_warehouse, table_batch):
		bal_qty = None
		supplier = None
		customer = None
		inventory_type = None

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
				(sle.item_code == table_item)
				& (sle.warehouse == talble_source_warehouse)
				& (sle.is_cancelled == 0)
				& ((batch.expiry_date >= CurDate()) | (batch.expiry_date.isnull()))
				# & (batch.batch_id.like(f"%{txt}%"))
			)
			.groupby(batch.batch_id)
			.having(Sum(sle.actual_qty) != 0)
			.orderby(batch.expiry_date, batch.creation)
		)
		batch_qty = query.run(as_dict=False)

		for row in self.mc_source_table:
			for batch_id, qty in batch_qty:
				if batch_id == row.batch:
					bal_qty = qty
					break

		batch_detail = frappe.db.get_all(
			"Batch",
			filters={"name": table_batch},
			fields={"name", "reference_doctype", "reference_name"},
		)
		if batch_detail:
			ref_doctype = batch_detail[0].reference_doctype
			ref_name = batch_detail[0].reference_name
			if ref_doctype == "Stock Entry":
				customer, inventory_type = frappe.get_value(
					ref_doctype, ref_name, ["_customer", "inventory_type"]
				)
			if ref_doctype == "Purchase Receipt":
				supplier = frappe.get_value(ref_doctype, ref_name, "supplier")

		# frappe.throw(f"{table_item, table_batch}")
		return bal_qty or None, supplier or None, customer or None, inventory_type or None

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
				alloy_qty = round(float((target_qty - self.source_qty)), 3)
			else:
				frappe.throw("Error: Target Item Purity value is zero.")

		return target_qty, alloy_qty

	@frappe.whitelist()
	def calculate_Multiple_conversion(self):
		if not self.m_target_item:
			frappe.throw("Target Item Code Missing")

		target_item_purity = frappe.get_all(
			"Item Variant Attribute",
			filters={"parent": self.m_target_item, "attribute": "Metal Purity"},
			fields=["parent", "attribute", "attribute_value"],
		)
		if not target_item_purity:
			frappe.throw("Item purity list is empty. Cannot access elements.")

		source_attribute_value = float(target_item_purity[0].get("attribute_value"))

		sum_total = 0
		sum_source_qty = 0
		inventory_types_source = set()
		for row in self.mc_source_table:
			inventory_types_source.add(row.inventory_type)
			sum_total += row.total
			sum_source_qty += row.qty

		if len(inventory_types_source) > 1:
			frappe.throw("Inventory types in <b>Source Table</b> are not consistent. Please check.")

		if isinstance(source_attribute_value, float):
			target_qty = round(sum_total / source_attribute_value, 3)
			alloy_qty = round(float((target_qty - sum_source_qty)), 3)
		else:
			frappe.throw(f"Attribute value set properly in master <b>{source_attribute_value}</b>")
		# frappe.throw(f"{target_qty}")
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

		source_item_purity = frappe.get_all(
			"Item Variant Attribute",
			filters={"parent": item_code, "attribute": "Metal Purity"},
			fields=["parent", "attribute", "attribute_value"],
		)
		if not source_item_purity:
			frappe.throw("Item purity list is empty. Cannot access elements.")

		source_attribute_value = float(source_item_purity[0].get("attribute_value"))

		if isinstance(source_attribute_value, float):
			total = qty * source_attribute_value
		else:
			frappe.throw(f"Attribute value set properly in master <b>{source_attribute_value}</b>")
		return total


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
	# target_wh = self.target_warehouse
	# inventory_type = self.inventory_type
	# batch_no = self.batch
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
		}
	)
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


from frappe.desk.reportview import get_filters_cond, get_match_cond
from frappe.query_builder.functions import CombineDatetime, CurDate, Sum
from frappe.utils import nowdate, unique


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

	data = get_batches(item_code, warehouse, company, txt)
	# frappe.throw(f"{data}")
	return data


def get_batches(item_code, warehouse, company, txt=None, qty=1, throw=False, serial_no=None):
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
			# & (batch.batch_id.like(f"%{txt}%"))
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


@frappe.whitelist()
def metal_filter(doctype, txt, searchfield, start, page_len, filters):
	# pass
	if filters.get("attribute_value"):
		return frappe.db.sql(
			"""SELECT parent FROM `tabItem Variant Attribute`
               WHERE attribute_value = %s AND {searchfield} LIKE %s
               LIMIT %s OFFSET %s""".format(
				searchfield=searchfield
			),
			(
				filters.get("attribute_value"),
				"%%%s%%" % txt,
				page_len,
				start,
			),
		)
		return frappe.db.sql(
			"""SELECT parent FROM `tabItem Variant Attribute` where attribute_value =%(attribute_value)s,%(page_len)s""",
			{
				"attribute_value": filters.get("attribute_value"),
				"start": start,
				"page_len": page_len,
				"txt": "%%%s%%" % txt,
			},
		)

	# 		""" select item_code,item_name,parent,qty,stock_uom from `tabPurchase Order Item`
	# 	where parent = %(parent)s and (qty-received_qty)>0 and item_name like %(txt)s
	# 	limit %(start)s, %(page_len)s""", {
	# 		'parent': filters.get("parent"),
	# 		'start': start,
	# 		'page_len': page_len,
	# 		'txt': "%%%s%%" % txt
	# 	})
	# from frappe.desk.reportview import get_match_cond
	# meta = frappe.get_meta(doctype)
	# attribute = filters.pop("attribute")
	# att_val = filters.pop("attribute_value")
	# attribute_value = frappe.get_value("Item Variant Attribute",{"parent":filters.pop("s_item"),"attribute":"Metal Type"},"attribute_value")
	# searchfields = meta.get_search_fields()
	# if searchfield and (meta.get_field(searchfield) or searchfield in frappe.db.DEFAULT_COLUMNS):
	# 	searchfields.append(searchfield)
	# frappe.throw(f"{searchfields}")
	# return frappe.db.sql(
	#     """SELECT `tabItem Variant Attribute`.parent
	#        FROM `tabItem`
	#        JOIN `tabItem Variant Attribute`
	#        ON (`tabItem Variant Attribute`.parent = `tabItem`.name AND `tabItem Variant Attribute`.parenttype = 'Item')
	#        WHERE `tabItem Variant Attribute`.attribute = 'Metal Type'
	#        AND `tabItem Variant Attribute`.attribute_value = %(attribute_value)s
	#        AND `{searchfield}` LIKE %(txt)s
	#        LIMIT %(page_len)s OFFSET %(start)s""".format(
	#            searchfield=searchfield,
	#        ),
	#     {
	#         "txt": "%" + txt + "%",
	#         "start": start,
	#         "page_len": page_len,
	#         "name": "name" ,
	#     },
	#     as_dict=True
	# )
