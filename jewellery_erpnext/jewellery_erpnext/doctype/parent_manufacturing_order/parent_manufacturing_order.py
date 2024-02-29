# Copyright (c) 2023, Nirali and contributors
# For license information, please see license.txt

import frappe
from erpnext.controllers.item_variant import create_variant, get_variant
from frappe.model.document import Document
from frappe.model.mapper import get_mapped_doc
from frappe.utils import now

from jewellery_erpnext.utils import update_existing


class ParentManufacturingOrder(Document):
	def after_insert(self):
		if self.serial_no:
			serial_bom = frappe.db.exists("BOM", {"tag_no": self.serial_no})
			self.db_set("serial_id_bom", serial_bom)
		get_gemstone_details(self)

	def validate(self):
		update_bom_based_on_diamond_quality(self)
		pass

	def on_update(self):
		pass

	def on_submit(self):
		set_metal_tolerance_table(self)  # To Set Metal Product Tolerance Table
		set_diamond_tolerance_table(self)  # To Set Diamond Product Tolerance Table
		set_gemstone_tolerance_table(self)  # To Set Gemstone Product Tolerance Table
		create_manufacturing_work_order(self)
		gemstone_details_set_mandatory_field(self)
		for idx in range(0, int(self.qty)):
			self.create_material_requests()

		self.submit_bom()

	def on_cancel(self):
		update_existing(
			"Manufacturing Plan Table",
			self.rowname,
			"manufacturing_order_qty",
			f"manufacturing_order_qty - {self.qty}",
		)
		update_existing(
			"Sales Order Item",
			self.sales_order_item,
			"manufacturing_order_qty",
			f"manufacturing_order_qty - {self.qty}",
		)

	def submit_bom(self):
		bom = frappe.get_doc("BOM", self.master_bom)
		bom.submit()

	def update_estimated_delivery_date_in_prev_docs(self):
		frappe.db.set_value(
			"Manufacturing Plan",
			self.manufacturing_plan,
			"estimated_delivery_date",
			self.estimated_delivery_date,
		)

	def create_material_requests(self):
		bom = self.serial_id_bom or self.master_bom
		mnf_abb = frappe.get_value("Manufacturer", self.manufacturer, "custom_abbreviation")
		if not bom:
			frappe.throw("BOM is missing")
		check_bom_type = frappe.get_value("BOM", bom, "bom_type")
		if check_bom_type != "Manufacturing Process":
			frappe.throw(f"Master BOM <b>{bom}</b> BOM Type must be a Sales Order")
		rm_item_bom = frappe.get_all("BOM Item", {"parent": bom}, ["parent", "item_code", "qty"])
		deafault_department = frappe.db.get_value(
			"Manufacturing Setting", {"company": self.company}, "default_department"
		)
		deafault_warehouse = frappe.db.get_value(
			"Warehouse", {"department": deafault_department}, "name"
		)

		# Initialize separate lists for each item type
		bom_tables = [
			"BOM Metal Detail",
			"BOM Finding Detail",
			"BOM Diamond Detail",
			"BOM Gemstone Detail",
			"BOM Other Detail",
		]
		metal_items = []
		diamond_items = []
		gemstone_items = []
		finding_items = []
		other_items = []

		for bom_table in bom_tables:
			# Get bom table's
			if bom_table == "BOM Metal Detail":
				data = frappe.get_all(
					bom_table, {"parent": bom}, ["item_variant", "quantity", "is_customer_item"]
				)
			if bom_table == "BOM Finding Detail":
				data = frappe.get_all(
					bom_table, {"parent": bom}, ["item_variant", "quantity", "qty", "is_customer_item"]
				)
			if bom_table == "BOM Diamond Detail" or bom_table == "BOM Gemstone Detail":
				data = frappe.get_all(
					bom_table,
					{"parent": bom},
					["item_variant", "quantity", "is_customer_item", "sub_setting_type", "pcs"],
				)
			if bom_table == "BOM Other Detail":
				data = frappe.get_all(
					bom_table,
					{"parent": bom},
					["item_code", "quantity", "qty"],
				)
			if data:
				# Loop through the rows of the bom table
				for row in data:
					item_type = get_item_type(row.item_variant)
					if item_type == "metal_item":
						metal_items.append(
							{
								"item_code": row.item_variant,
								"qty": row.quantity,
								"warehouse": frappe.db.get_value(
									"Warehouse", {"department": self.metal_department}, "name"
								)
								or deafault_warehouse,
								"is_customer_item": row.is_customer_item,
								"sub_setting_type": None,
								"pcs": None,
							}
						)
					elif item_type == "finding_item":
						finding_items.append(
							{
								"item_code": row.item_variant,
								"qty": row.quantity,
								"warehouse": frappe.db.get_value(
									"Warehouse", {"department": self.finding_department}, "name"
								)
								or deafault_warehouse,
								"is_customer_item": row.is_customer_item,
								"sub_setting_type": None,
								"pcs": row.qty,
							}
						)
					elif item_type == "diamond_item":
						diamond_items.append(
							{
								"item_code": row.item_variant,
								"qty": row.quantity,
								"warehouse": frappe.db.get_value(
									"Warehouse", {"department": self.diamond_department}, "name"
								)
								or deafault_warehouse,
								"is_customer_item": row.is_customer_item,
								"sub_setting_type": row.sub_setting_type,
								"pcs": row.pcs,
							}
						)
					elif item_type == "gemstone_item":
						gemstone_items.append(
							{
								"item_code": row.item_variant,
								"qty": row.quantity,
								"warehouse": frappe.db.get_value(
									"Warehouse", {"department": self.gemstone_department}, "name"
								)
								or deafault_warehouse,
								"is_customer_item": row.is_customer_item,
								"sub_setting_type": row.sub_setting_type,
								"pcs": row.pcs,
							}
						)
					elif item_type == "other_item":
						other_items.append(
							{
								"item_code": row.item_code,
								"qty": row.quantity,
								"warehouse": frappe.db.get_value(
									"Warehouse", {"department": self.other_material_department}, "name"
								)
								or deafault_warehouse,
								"is_customer_item": "0",
								"sub_setting_type": None,
								"pcs": row.qty,
							}
						)
		items = {
			"metal_item": metal_items,
			"diamond_item": diamond_items,
			"gemstone_item": gemstone_items,
			"finding_item": finding_items,
			"other_item": other_items,
		}
		# frappe.throw(f"{items}")
		counter = 1
		trimmed_items = {item_type.split("_")[0][:1].lower(): val for item_type, val in items.items()}
		for item_type, val in trimmed_items.items():
			if val:
				mr_doc = frappe.new_doc("Material Request")
				tital = f"MR{item_type.upper()}-{mnf_abb}-({self.item_code})-{counter}"
				mr_doc.title = tital
				mr_doc.company = self.company
				mr_doc.material_request_type = "Material Transfer"
				mr_doc.schedule_date = frappe.utils.nowdate()
				mr_doc.manufacturing_order = self.name
				mr_doc.custom_manufacturer = self.manufacturer
				if (
					self.customer_gold == "Yes"
					and self.customer_diamond == "Yes"
					and self.customer_stone == "Yes"
					and self.customer_good == "Yes"
				):
					mr_doc._customer = self.customer
					mr_doc.inventory_type = "Customer Goods"

				for i in val:
					if i["qty"] > 0:
						mr_doc.append(
							"items",
							{
								"item_code": i["item_code"],
								"qty": i["qty"],
								"warehouse": i["warehouse"],
								"custom_is_customer_item": i.get("is_customer_item", 0),
								"custom_sub_setting_type": i.get("sub_setting_type", None),
								"pcs": i.get("pcs", None),
							},
						)
					else:
						frappe.throw(
							f"Please Check BOM Table:<b>{item_type.upper()}</b>, <b>{i['item_code']}</b> is {i['qty']} Not Allowed."
						)
				counter += 1
				mr_doc.save()
		frappe.msgprint("Material Request Created !!")

	def set_missing_value(self):
		if not self.is_new():
			pass

	@frappe.whitelist()
	def get_stock_summary(self):
		target_wh = frappe.db.get_value("Warehouse", {"department": self.department})
		mwo = frappe.get_all(
			"Manufacturing Work Order", {"manufacturing_order": self.name}, pluck="name"
		)
		mwo_last_operation = []
		for mwo_id in mwo:
			operation = frappe.db.get_value(
				"Manufacturing Work Order",
				filters={"name": mwo_id},
				fieldname="manufacturing_operation",
				order_by="modified desc",
			)
			if operation is not None:
				mwo_last_operation.append(operation)
		mwo_operations = "','".join(mwo_last_operation)
		data = frappe.db.sql(
			f"""SELECT
					se.manufacturing_work_order,
					se.manufacturing_operation,
					sed.parent,
					sed.item_code,
					sed.item_name,
					sed.inventory_type,
					sed.pcs,
					sed.batch_no,
					sed.qty,
					sed.uom
				FROM
					`tabStock Entry Detail` sed
				LEFT JOIN
					(
						SELECT
							MAX(se.modified) AS max_modified,
							se.manufacturing_operation
						FROM
							`tabStock Entry` se
						WHERE
							se.docstatus = 1
						GROUP BY
							se.manufacturing_operation
					) max_se ON sed.manufacturing_operation = max_se.manufacturing_operation
				LEFT JOIN
					`tabStock Entry` se ON sed.parent = se.name
										AND se.modified = max_se.max_modified
				WHERE
					se.docstatus = 1
					AND sed.manufacturing_operation IN ('{mwo_operations}')""",
			as_dict=True,
		)
		# frappe.throw(f"{data}<br>")
		# data = frappe.db.sql(
		# 	f"""select se.manufacturing_work_order, se.manufacturing_operation, sed.parent, sed.item_code,sed.item_name, sed.qty, sed.uom
		# 	   				from `tabStock Entry Detail` sed left join `tabStock Entry` se on sed.parent = se.name where
		# 					se.docstatus = 1 and
		# 					sed.manufacturing_operation in ('{"', '".join(mwo_last_operation)}')
		# 					group by se.manufacturing_operation, sed.item_code, sed.qty, sed.uom
		# 					order by sed.creation
		# 					""",
		# 	as_dict=1,
		# )
		total_qty = 0
		for row in data:
			if row.uom == "cts":
				total_qty += row.get("qty", 0) * 0.2
			else:
				total_qty += row.get("qty", 0)
		total_qty = round(total_qty, 4)
		return frappe.render_template(
			"jewellery_erpnext/jewellery_erpnext/doctype/parent_manufacturing_order/stock_summery.html",
			{"data": data, "total_qty": total_qty},
		)

	@frappe.whitelist()
	def get_linked_stock_entries(self):  # MOP Details
		mwo = frappe.get_all(
			"Manufacturing Work Order", {"manufacturing_order": self.name}, pluck="name"
		)
		data = frappe.db.sql(
			f"""select se.manufacturing_work_order, se.manufacturing_operation, se.department,se.to_department, se.employee,se.stock_entry_type,sed.parent, sed.item_code,sed.item_name, sed.qty, sed.uom
			   				from `tabStock Entry Detail` sed left join `tabStock Entry` se on sed.parent = se.name where
							se.docstatus = 1 and se.manufacturing_work_order in ('{"', '".join(mwo)}') ORDER BY se.modified ASC""",
			as_dict=1,
		)
		total_qty = len([item["qty"] for item in data])
		return frappe.render_template(
			"jewellery_erpnext/jewellery_erpnext/doctype/parent_manufacturing_order/stock_entry_details.html",
			{"data": data, "total_qty": total_qty},
		)


def update_bom_based_on_diamond_quality(self):
	bom = frappe.get_doc("BOM", self.master_bom)
	if self.diamond_grade:
		for row in bom.diamond_detail:
			row.diamond_grade = self.diamond_grade
	for pmo_row in self.gemstone_table:
		for b_row in bom.gemstone_detail:
			if (
				pmo_row.gemstone_type == b_row.gemstone_type
				and pmo_row.cut_or_cab == b_row.cut_or_cab
				and pmo_row.stone_shape == b_row.stone_shape
				and pmo_row.pcs == b_row.pcs
				and pmo_row.gemstone_size == b_row.gemstone_size
			):
				b_row.gemstone_size = pmo_row.gemstone_size
				b_row.gemstone_code = pmo_row.gemstone_code
				b_row.gemstone_pr = pmo_row.gemstone_pr
				b_row.per_pc_or_per_carat = pmo_row.per_pc_or_per_carat
	bom.save()


def get_item_type(item_code):
	item_type = frappe.db.get_value("Item", item_code, "variant_of")
	if item_type == "M":
		return "metal_item"
	elif item_type == "D":
		return "diamond_item"
	elif item_type == "G":
		return "gemstone_item"
	elif item_type == "F":
		return "finding_item"
	else:
		return "other_item"


@frappe.whitelist()
def get_item_code(sales_order_item):
	return frappe.db.get_value("Sales Order Item", sales_order_item, "item_code")


@frappe.whitelist()
def make_manufacturing_order(source_doc, row):
	so_doc = frappe.get_doc("Sales Order", row.sales_order)
	doc = frappe.new_doc("Parent Manufacturing Order")
	so_det = (
		frappe.get_value(
			"Sales Order Item",
			row.docname,
			["metal_type", "metal_touch", "metal_colour"],
			as_dict=1,
		)
		or {}
	)
	doc.company = source_doc.company
	doc.department = frappe.db.get_value(
		"Manufacturing Setting", {"company": source_doc.company}, "default_department"
	)
	doc.metal_department = frappe.db.get_value(
		"Manufacturing Setting", {"company": source_doc.company}, "default_department"
	)
	doc.diamond_department = (
		frappe.db.get_value(
			"Manufacturing Setting",
			{"company": source_doc.company},
			"default_diamond_department",
		)
		or ""
	)
	doc.gemstone_department = (
		frappe.db.get_value(
			"Manufacturing Setting",
			{"company": source_doc.company},
			"default_gemstone_department",
		)
		or ""
	)
	doc.finding_department = (
		frappe.db.get_value(
			"Manufacturing Setting",
			{"company": source_doc.company},
			"default_finding_department",
		)
		or ""
	)
	doc.other_material_department = (
		frappe.db.get_value(
			"Manufacturing Setting",
			{"company": source_doc.company},
			"default_other_material_department",
		)
		or ""
	)
	doc.sales_order = row.sales_order
	doc.sales_order_item = row.docname
	doc.item_code = row.item_code
	doc.metal_type = so_det.get("metal_type")
	doc.metal_touch = so_det.get("metal_touch")
	doc.metal_colour = so_det.get("metal_colour")
	doc.customer_sample = row.customer_sample
	doc.customer_voucher_no = row.customer_voucher_no
	doc.customer_gold = row.customer_gold
	doc.customer_diamond = row.customer_diamond
	doc.customer_stone = row.customer_stone
	doc.customer_good = row.customer_good
	# doc.sales_order_bom = row.bom
	doc.service_type = [
		frappe.get_doc(row)
		for row in frappe.get_all(
			"Service Type 2",
			{"parent": row.sales_order},
			["service_type1", "'Service Type 2' as doctype"],
		)
	]
	doc.manufacturing_plan = source_doc.name
	doc.manufacturer = frappe.db.get_value(
		"Manufacturer", {"company": source_doc.company}, "name", order_by="creation asc"
	)
	doc.qty = row.qty_per_manufacturing_order
	doc.rowname = row.name
	doc.master_bom = row.manufacturing_bom
	doc.save()

	diamond_grade = frappe.db.get_value(
		"Customer Diamond Grade",
		{"diamond_quality": doc.diamond_quality, "parent": so_doc.get("ref_customer") or doc.customer},
		"diamond_grade_1",
	)
	doc.db_set("diamond_grade", diamond_grade)


def create_manufacturing_work_order(self):
	if not self.master_bom:
		return
	# metal_details = frappe.get_all("BOM Metal Detail", {"parent": self.master_bom}, ["metal_type","metal_touch","metal_purity","metal_colour"], group_by='metal_type, metal_purity, metal_colour')
	metal_details = frappe.db.sql(
		f"""SELECT DISTINCT metal_touch, metal_type, metal_purity, metal_colour
									FROM (
									SELECT metal_touch, metal_type, metal_purity, metal_colour, parent FROM `tabBOM Metal Detail`
									UNION
									SELECT metal_touch, metal_type, metal_purity, metal_colour, parent FROM `tabBOM Finding Detail`
									) AS combined_details where parent = '{self.master_bom}'""",
		as_dict=1,
	)
	grouped_data = {}
	for item in metal_details:
		metal_purity = item["metal_purity"]
		metal_colour = item["metal_colour"]

		if metal_purity not in grouped_data:
			grouped_data[metal_purity] = {metal_colour}
		else:
			grouped_data[metal_purity].add(metal_colour)

	result = [
		{"metal_purity": key, "metal_colours": list(value)} for key, value in grouped_data.items()
	]

	updated_data = []

	for entry in result:
		metal_purity = entry["metal_purity"]
		metal_colours = "".join(sorted([color[0].upper() for color in entry["metal_colours"]]))

		updated_entry = {"metal_purity": metal_purity, "metal_colours": metal_colours}

		updated_data.append(updated_entry)
	for row in metal_details:
		doc = get_mapped_doc(
			"Parent Manufacturing Order",
			self.name,
			{
				"Parent Manufacturing Order": {
					"doctype": "Manufacturing Work Order",
					"field_map": {"name": "manufacturing_order"},
				}
			},
		)
		for color in updated_data:
			if row.metal_purity == color["metal_purity"] and len(color["metal_colours"]) > 1:
				doc.multicolour = 1
				doc.allowed_colours = color["metal_colours"]
		doc.metal_touch = row.metal_touch
		doc.metal_type = row.metal_type
		doc.metal_purity = row.metal_purity
		doc.metal_colour = row.metal_colour
		doc.seq = int(self.name.split("-")[-1])
		doc.department = frappe.db.get_value(
			"Manufacturing Setting", {"company": doc.company}, "default_department"
		)
		doc.auto_created = 1
		doc.save()

	# for FG item
	fg_doc = get_mapped_doc(
		"Parent Manufacturing Order",
		self.name,
		{
			"Parent Manufacturing Order": {
				"doctype": "Manufacturing Work Order",
				"field_map": {"name": "manufacturing_order"},
			}
		},
	)
	fg_doc.metal_touch = row.metal_touch
	fg_doc.metal_type = row.metal_type
	fg_doc.metal_purity = row.metal_purity
	fg_doc.metal_colour = row.metal_colour
	fg_doc.seq = int(self.name.split("-")[-1])
	fg_doc.department = frappe.db.get_value(
		"Manufacturing Setting", {"company": doc.company}, "default_fg_department"
	)
	fg_doc.for_fg = 1
	fg_doc.auto_created = 1
	fg_doc.save()


def get_diamond_item_code_by_variant(self, bom, target_warehouse):
	attributes = {}
	diamond_list = []
	diamond_bom = frappe.get_doc("BOM", bom)
	if diamond_bom.diamond_detail:
		# Loop through the rows of the bom table
		for row in diamond_bom.diamond_detail:
			# Get the template document for the current item
			template = frappe.get_doc("Item", row.item)
			if (
				template.name not in attributes
			):  # If the attributes for the current item have not been fetched yet
				attributes[template.name] = [
					attr.attribute for attr in template.attributes
				]  # Store the attributes in the dictionary
			# Create a dictionary of the attribute values from the row
			args = {
				attr: row.get(attr.replace(" ", "_").lower())
				for attr in attributes[template.name]
				if row.get(attr.replace(" ", "_").lower())
			}
			args["Diamond Grade"] = self.diamond_grade
			variant = get_variant(
				row.item, args
			)  # Get the variant for the current item and attribute values

			if variant:
				diamond_list.append({"item_code": variant, "qty": self.qty, "warehouse": target_warehouse})

			else:
				# Create a new variant
				variant = create_variant(row.item, args)
				variant.save()
				diamond_list.append(
					{"item_code": variant.item_code, "qty": self.qty, "warehouse": target_warehouse}
				)

		return diamond_list


def get_gemstone_item_code_by_variant(self, bom, target_warehouse):
	attributes = {}
	gemstone_list = []
	if len(self.gemstone_table) > 0:
		for row in self.gemstone_table:
			template = frappe.get_doc("Item", "G")
			if template.name not in attributes:
				attributes[template.name] = [attr.attribute for attr in template.attributes]
			args = {
				attr: row.get(attr.replace(" ", "_").lower())
				for attr in attributes[template.name]
				if row.get(attr.replace(" ", "_").lower())
			}
			variant = get_variant(row.item, args)

			if variant:
				gemstone_list.append(
					{"item_code": variant, "qty": row.quantity, "warehouse": target_warehouse}
				)
			else:
				# Create a new variant
				variant = create_variant(row.item, args)
				variant.save()
				gemstone_list.append(
					{
						"item_code": variant.item_code,
						"qty": row.quantity,
						"warehouse": target_warehouse,
					}
				)

		return gemstone_list


def get_gemstone_details(self):
	bom = self.serial_id_bom or self.master_bom
	if not bom:
		frappe.throw("Sales Order BOM is Missing on Manufacturing Plan Table")
	bom_doc = frappe.get_doc("BOM", bom)
	if len(bom_doc.gemstone_detail) > 0:
		for gem_row in bom_doc.gemstone_detail:
			self.append(
				"gemstone_table",
				{
					"price_list_type": gem_row.price_list_type,
					"gemstone_type": gem_row.gemstone_type,
					"cut_or_cab": gem_row.cut_or_cab,
					"stone_shape": gem_row.stone_shape,
					"gemstone_quality": gem_row.gemstone_quality,
					"gemstone_grade": gem_row.gemstone_grade,
					"is_customer_item": gem_row.is_customer_item,
					"total_gemstone_rate": gem_row.total_gemstone_rate,
					"gemstone_size": gem_row.gemstone_size,
					"gemstone_code": gem_row.gemstone_code,
					"sub_setting_type": gem_row.sub_setting_type,
					"pcs": gem_row.pcs,
					"quantity": gem_row.quantity,
					"weight_in_gms": gem_row.weight_in_gms,
					"stock_uom": gem_row.stock_uom,
					"item_variant": gem_row.item_variant,
					"gemstone_rate_for_specified_quantity": gem_row.gemstone_rate_for_specified_quantity,
					"navratna": gem_row.navratna,
					"gemstone_pr": gem_row.gemstone_pr,
					"per_pc_or_per_carat": gem_row.per_pc_or_per_carat,
				},
			)
		self.save()


def gemstone_details_set_mandatory_field(self):
	errors = []
	if self.gemstone_table:
		for row in self.gemstone_table:
			if not row.gemstone_quality:
				errors.append("Gemstone Details Table <b>Quality</b> is required.")
			if not row.gemstone_grade:
				errors.append("Gemstone Details Table <b>Grade</b> is required.")
			if not row.gemstone_pr:
				errors.append("Gemstone Details Table <b>Gemstone PR</b> is required.")
			if not row.per_pc_or_per_carat:
				errors.append("Gemstone Details Table <b>Per Pc or Per Carat</b> is required.")
	if errors:
		frappe.throw("<br>".join(errors))


def set_metal_tolerance_table(self):  # To Set Metal Product Tolerance Table
	cpt = frappe.db.get_value(
		"Customer Product Tolerance Master", {"customer_name": self.customer}, ["name"]
	)
	if not cpt:
		return
	cptm = frappe.get_doc("Customer Product Tolerance Master", cpt)
	bom = self.serial_id_bom or self.master_bom
	if not bom:
		frappe.throw("BOM is missing")
	bom_doc = frappe.get_doc("BOM", bom)
	if len(cptm.metal_tolerance_table) > 0:
		for mtt_tbl in cptm.metal_tolerance_table:
			if mtt_tbl.weight_type == "Gross Weight":
				bom_gross_wt = bom_doc.gross_weight
			else:
				bom_gross_wt = bom_doc.metal_and_finding_weight

			if mtt_tbl.range_type == "Weight Range":
				from_tolerance_wt = round(bom_gross_wt - mtt_tbl.tolerance_range, 4)
				to_tolerance_wt = round(bom_gross_wt + mtt_tbl.tolerance_range, 4)
			else:
				from_tolerance_wt = round(bom_gross_wt * (100 - mtt_tbl.minus_percent), 4)
				to_tolerance_wt = round(bom_gross_wt * (100 + mtt_tbl.plus_percent), 4)

			child_row = {
				"doctype": "Metal Product Tolerance",
				"parent": self.name,
				"parenttype": self.doctype,
				"parentfield": "metal_product_tolerance",
				"metal_type": mtt_tbl.metal_type,
				"from_tolerance_wt": from_tolerance_wt,
				"to_tolerance_wt": to_tolerance_wt,
				"standard_tolerance_wt": round(bom_gross_wt, 4),
				"product_wt": self.gross_weight,
			}
			try:
				self.append("metal_product_tolerance", child_row)
			except Exception as e:
				frappe.throw(
					f"Error appending <b>Metal Product Tolerance Table</b> Please check <b>Customer Product Tolerance Master</b> Doctype Correctly configured or not:</br></br> {str(e)}"
				)
	self.save()


def set_diamond_tolerance_table(self):  # To Set Diamond Product Tolerance Table
	cpt = frappe.db.get_value(
		"Customer Product Tolerance Master", {"customer_name": self.customer}, ["name"]
	)
	if not cpt:
		return
	cptm = frappe.get_doc("Customer Product Tolerance Master", cpt)
	bom = self.serial_id_bom or self.master_bom
	if not bom:
		frappe.throw("BOM is missing")
	bom_doc = frappe.get_doc("BOM", bom)
	if len(cptm.diamond_tolerance_table) > 0:
		for dtt_tbl in cptm.diamond_tolerance_table:
			if dtt_tbl.weight_type == "MM Size wise":
				for dimond_row in bom_doc.diamond_detail:
					if dimond_row.diamond_sieve_size == dtt_tbl.sieve_size:
						sieve_size_range = dimond_row.sieve_size_range
						pcs = dimond_row.pcs
						weight_in_cts = dimond_row.quantity
						from_tolerance_wt = round(weight_in_cts * ((100 - dtt_tbl.minus_percent) / 100), 4)
						to_tolerance_wt = round(weight_in_cts * ((100 + dtt_tbl.minus_percent) / 100), 4)
						child_row = {
							"doctype": "Diamond Product Tolerance",
							"parent": self.name,
							"parenttype": self.doctype,
							"parentfield": "diamond_product_tolerance",
							"weight_type": dtt_tbl.weight_type,
							"sieve_size": dtt_tbl.sieve_size,
							"size_in_mm": round(dimond_row.size_in_mm, 4),
							"from_tolerance_wt": from_tolerance_wt,
							"to_tolerance_wt": to_tolerance_wt,
							"standard_tolerance_wt": round(weight_in_cts, 4),
							"product_wt": self.diamond_weight,
						}
			if dtt_tbl.weight_type == "Group Size wise":
				sieve_size_ranges = set()
				quantity_sum = 0
				size_in_mm = 0
				for dimond_row in bom_doc.diamond_detail:
					if dtt_tbl.sieve_size_range == dimond_row.sieve_size_range:
						quantity_sum += dimond_row.quantity
						size_in_mm += dimond_row.size_in_mm
						sieve_size_ranges.add(dimond_row.sieve_size_range)
				from_tolerance_wt = round(quantity_sum * ((100 - dtt_tbl.minus_percent) / 100), 4)
				to_tolerance_wt = round(quantity_sum * ((100 + dtt_tbl.plus_percent) / 100), 4)
				for sieve_size_range in sorted(sieve_size_ranges):
					if dtt_tbl.sieve_size_range == sieve_size_range:
						child_row = {
							"doctype": "Diamond Product Tolerance",
							"parent": self.name,
							"parenttype": self.doctype,
							"parentfield": "diamond_product_tolerance",
							"weight_type": dtt_tbl.weight_type,
							"sieve_size": None,
							"sieve_size_range": sieve_size_range,
							"size_in_mm": round(size_in_mm, 4),
							"from_tolerance_wt": from_tolerance_wt,
							"to_tolerance_wt": to_tolerance_wt,
							"standard_tolerance_wt": round(quantity_sum, 4),
							"product_wt": self.diamond_weight,
						}
			if dtt_tbl.weight_type == "Weight wise":
				diamond_total_wt = 0
				for dimond_row in bom_doc.diamond_detail:
					diamond_total_wt += dimond_row.quantity
					from_tolerance_wt = round(diamond_total_wt * ((100 - dtt_tbl.minus_percent) / 100), 4)
					to_tolerance_wt = round(diamond_total_wt * ((100 + dtt_tbl.plus_percent) / 100), 4)
					child_row = {
						"doctype": "Diamond Product Tolerance",
						"parent": self.name,
						"parenttype": self.doctype,
						"parentfield": "diamond_product_tolerance",
						"weight_type": dtt_tbl.weight_type,
						"sieve_size": None,
						"sieve_size_range": None,
						"from_tolerance_wt": from_tolerance_wt,
						"to_tolerance_wt": to_tolerance_wt,
						"standard_tolerance_wt": round(diamond_total_wt, 4),
						"product_wt": self.diamond_weight,
					}
			if dtt_tbl.weight_type == "Universal":
				empty_sieve_size_ranges = set()
				empty_quantity_sum = 0
				empty_size_in_mm = 0
				for dimond_row in bom_doc.diamond_detail:
					if dimond_row.sieve_size_range is None:
						empty_quantity_sum += dimond_row.quantity
						empty_size_in_mm += dimond_row.size_in_mm
						empty_sieve_size_ranges.add(dimond_row.sieve_size_range)
				empty_from_tolerance_wt = round(empty_quantity_sum * ((100 - dtt_tbl.minus_percent) / 100), 4)
				empty_to_tolerance_wt = round(empty_quantity_sum * ((100 + dtt_tbl.plus_percent) / 100), 4)
				if empty_sieve_size_ranges:
					for sieve_size_range in sorted(empty_sieve_size_ranges):
						child_row = {
							"doctype": "Diamond Product Tolerance",
							"parent": self.name,
							"parenttype": self.doctype,
							"parentfield": "diamond_product_tolerance",
							"weight_type": "Universal",
							"sieve_size": None,
							"sieve_size_range": None,
							"size_in_mm": round(empty_size_in_mm, 4),
							"from_tolerance_wt": empty_from_tolerance_wt,
							"to_tolerance_wt": empty_to_tolerance_wt,
							"standard_tolerance_wt": round(empty_quantity_sum, 4),
							"product_wt": self.diamond_weight,
						}
			try:
				self.append("diamond_product_tolerance", child_row)
			except Exception as e:
				frappe.throw(
					f"Error appending <b>Diamond Product Tolerance Table</b> Please check <b>Customer Product Tolerance Master</b> Doctype Correctly configured or not:</br></br> {str(e)}"
				)
	self.save()


def set_gemstone_tolerance_table(self):  # To Set Gemstone Product Tolerance Table
	cpt = frappe.db.get_value(
		"Customer Product Tolerance Master", {"customer_name": self.customer}, ["name"]
	)
	if not cpt:
		return
	cptm = frappe.get_doc("Customer Product Tolerance Master", cpt)
	bom = self.serial_id_bom or self.master_bom
	if not bom:
		frappe.throw("BOM is missing")
	bom_doc = frappe.get_doc("BOM", bom)
	if len(cptm.gemstone_tolerance_table) > 0:
		for dtt_tbl in cptm.gemstone_tolerance_table:

			if dtt_tbl.weight_type == "Weight Range":
				shapes = set()
				quantity_sum = 0
				for gem_row in bom_doc.gemstone_detail:
					if dtt_tbl.gemstone_shape == gem_row.stone_shape:
						quantity_sum += gem_row.quantity
						shapes.add(gem_row.stone_shape)
				from_tolerance_wt = round(quantity_sum * ((100 - dtt_tbl.minus_percent) / 100), 4)
				to_tolerance_wt = round(quantity_sum * ((100 + dtt_tbl.plus_percent) / 100), 4)
				for shape in sorted(shapes):
					if dtt_tbl.gemstone_shape == shape:
						child_row = {
							"doctype": "Gemstone Product Tolerance",
							"parent": self.name,
							"parenttype": self.doctype,
							"parentfield": "gemstone_product_tolerance",
							"weight_type": dtt_tbl.weight_type,
							"gemstone_shape": shape,
							"gemstone_type": None,
							"standard_tolerance_wt": quantity_sum,
							"from_tolerance_wt": from_tolerance_wt,
							"to_tolerance_wt": to_tolerance_wt,
							"product_wt": self.diamond_weight,
						}
			if dtt_tbl.weight_type == "Gemstone Type Range":
				gemstone_types = set()
				quantity_sum = 0
				for gem_row in bom_doc.gemstone_detail:
					if dtt_tbl.gemstone_type == gem_row.gemstone_type:
						quantity_sum += gem_row.quantity
						gemstone_types.add(gem_row.gemstone_type)
				from_tolerance_wt = round(quantity_sum * ((100 - dtt_tbl.minus_percent) / 100), 4)
				to_tolerance_wt = round(quantity_sum * ((100 + dtt_tbl.plus_percent) / 100), 4)
				for gemstone_type in sorted(gemstone_types):
					if dtt_tbl.gemstone_type == gemstone_type:
						child_row = {
							"doctype": "Gemstone Product Tolerance",
							"parent": self.name,
							"parenttype": self.doctype,
							"parentfield": "gemstone_product_tolerance",
							"weight_type": dtt_tbl.weight_type,
							"gemstone_shape": None,
							"gemstone_type": gemstone_type,
							"standard_tolerance_wt": quantity_sum,
							"from_tolerance_wt": from_tolerance_wt,
							"to_tolerance_wt": to_tolerance_wt,
							"product_wt": self.diamond_weight,
						}
			if dtt_tbl.weight_type == "Weight wise":
				quantity_sum = 0
				for gem_row in bom_doc.gemstone_detail:
					quantity_sum += gem_row.quantity
					from_tolerance_wt = round(quantity_sum * ((100 - dtt_tbl.minus_percent) / 100), 4)
					to_tolerance_wt = round(quantity_sum * ((100 + dtt_tbl.plus_percent) / 100), 4)
					child_row = {
						"doctype": "Gemstone Product Tolerance",
						"parent": self.name,
						"parenttype": self.doctype,
						"parentfield": "gemstone_product_tolerance",
						"weight_type": dtt_tbl.weight_type,
						"gemstone_shape": None,
						"gemstone_type": None,
						"standard_tolerance_wt": quantity_sum,
						"from_tolerance_wt": from_tolerance_wt,
						"to_tolerance_wt": to_tolerance_wt,
						"product_wt": self.diamond_weight,
					}
			try:
				self.append("gemstone_product_tolerance", child_row)
			except Exception as e:
				frappe.throw(
					f"Error appending <b>Gemstone Product Tolerance Table</b> Please check <b>Customer Product Tolerance Master</b> Doctype Correctly configured or not:</br></br> {str(e)}"
				)
	self.save()
