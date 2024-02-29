# Copyright (c) 2023, Nirali and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname
from frappe.utils import get_timedelta, now, time_diff

from jewellery_erpnext.utils import set_values_in_bulk, update_existing


class ManufacturingOperation(Document):
	def validate(self):
		self.set_start_finish_time()
		self.update_weights()
		self.validate_loss()

	def on_update(self):
		self.attach_cad_cam_file_into_item_master()  # To set MOP doctype CAD-CAM Attachment's & respective details into Item Master.
		self.set_wop_weight_details()  # To Set WOP doctype Weight details from MOP Doctype.
		self.set_pmo_weight_details()  # To Set PMO doctype Weight details from MOP Doctype.

	def update_weights(self):
		res = get_material_wt(self)
		self.update(res)

	def validate_loss(self):
		if self.is_new() or not self.loss_details:
			return
		items = get_stock_entries_against_mfg_operation(self)
		for row in self.loss_details:
			if row.item_code not in items.keys():
				frappe.throw(_(f"Row #{row.idx}: Invalid item for loss"), title="Loss Details")
			if row.stock_uom != items[row.item_code].get("uom"):
				frappe.throw(
					_(f"Row #{row.idx}: UOM should be {items[row.item_code].get('uom')}"), title="Loss Details"
				)
			if row.stock_qty > items[row.item_code].get("qty", 0):
				frappe.throw(
					_(f"Row #{row.idx}: qty cannot be greater than {items[row.item_code].get('qty',0)}"),
					title="Loss Details",
				)

	def set_start_finish_time(self):
		if self.has_value_changed("status"):
			if self.status == "WIP" and not self.start_time:
				self.start_time = now()
				self.finish_time = None
			elif self.status == "Finished":
				if not self.start_time:
					self.start_time = now()
				self.finish_time = now()
		if self.start_time and self.finish_time:
			self.time_taken = get_timedelta(time_diff(self.finish_time, self.start_time))

	def attach_cad_cam_file_into_item_master(self):
		self.ref_name = self.name
		existing_child = self.get_existing_child("Item", self.item_code, "Cam Weight Detail", self.name)

		record_filter_from_mnf_setting = frappe.get_all(
			"CAM Weight Details Mapping",
			filters={"parent": self.company, "parenttype": "Manufacturing Setting"},
			fields=["operation"],
		)

		if existing_child:
			# Update the existing row
			existing_child.update(
				{
					"cad_numbering_file": self.cad_numbering_file,
					"support_cam_file": self.support_cam_file,
					"mop_series": self.ref_name,
					"platform_wt": self.platform_wt,
					"rpt_wt_issue": self.rpt_wt_issue,
					"rpt_wt_receive": self.rpt_wt_receive,
					"rpt_wt_loss": self.rpt_wt_loss,
					"estimated_rpt_wt": self.estimated_rpt_wt,
				}
			)
			existing_child.save()
		else:
			# Create a new child record
			filter_record = [row.get("operation") for row in record_filter_from_mnf_setting]
			if self.operation in filter_record:
				self.add_child_record(
					"Item",
					self.item_code,
					"Cam Weight Detail",
					{
						"cad_numbering_file": self.cad_numbering_file,
						"support_cam_file": self.support_cam_file,
						"mop_reference": self.ref_name,
						"mop_series": self.ref_name,
						"platform_wt": self.platform_wt,
						"rpt_wt_issue": self.rpt_wt_issue,
						"rpt_wt_receive": self.rpt_wt_receive,
						"rpt_wt_loss": self.rpt_wt_loss,
						"estimated_rpt_wt": self.estimated_rpt_wt,
					},
				)

	def get_existing_child(self, parent_doctype, parent_name, child_doctype, mop_reference):
		# Check if the child record already exists
		existing_child = frappe.get_all(
			child_doctype,
			filters={
				"parent": parent_name,
				"parenttype": parent_doctype,
				"mop_reference": mop_reference,
				"mop_series": self.ref_name,
			},
			fields=["name"],
		)
		if existing_child:
			return frappe.get_doc(child_doctype, existing_child[0]["name"])
		else:
			return None

	def add_child_record(self, parent_doctype, parent_name, child_doctype, child_fields):
		# Create a new child document
		child_doc = frappe.get_doc(
			{
				"doctype": child_doctype,
				"parent": parent_name,
				"parenttype": parent_doctype,
				"parentfield": "custom_cam_weight_detail",
			}
		)
		# Set values for the child document fields
		for fieldname, value in child_fields.items():
			child_doc.set(fieldname, value)
		# Save the child document
		child_doc.insert()

	@frappe.whitelist()
	def create_fg(self):
		se_name = create_manufacturing_entry(self)
		pmo = frappe.db.get_value(
			"Manufacturing Work Order", self.manufacturing_work_order, "manufacturing_order"
		)
		wo = frappe.get_all("Manufacturing Work Order", {"manufacturing_order": pmo}, pluck="name")
		set_values_in_bulk("Manufacturing Work Order", wo, {"status": "Completed"})
		create_finished_goods_bom(self, se_name)

	@frappe.whitelist()
	def get_linked_stock_entries(self):
		target_wh = frappe.db.get_value("Warehouse", {"department": self.department})
		pmo = frappe.db.get_value(
			"Manufacturing Work Order", self.manufacturing_work_order, "manufacturing_order"
		)
		se = frappe.new_doc("Stock Entry")
		se.stock_entry_type = "Manufacture"
		mwo = frappe.get_all(
			"Manufacturing Work Order",
			{
				"name": ["!=", self.manufacturing_work_order],
				"manufacturing_order": pmo,
				"docstatus": ["!=", 2],
				"department": ["=", self.department],
			},
			pluck="name",
		)
		data = frappe.db.sql(
			f"""select se.manufacturing_work_order, se.manufacturing_operation, sed.parent, sed.item_code,sed.item_name, sed.batch_no, sed.qty, sed.uom,
					   			ifnull(sum(if(sed.uom='cts',sed.qty*0.2, sed.qty)),0) as gross_wt
			   				from `tabStock Entry Detail` sed left join `tabStock Entry` se on sed.parent = se.name where
							se.docstatus = 1 and se.manufacturing_work_order in ('{"', '".join(mwo)}') and sed.t_warehouse = '{target_wh}'
							group by sed.manufacturing_operation,  sed.item_code, sed.qty, sed.uom """,
			as_dict=1,
		)
		total_qty = 0
		for row in data:
			total_qty += row.get("gross_wt", 0)
		total_qty = round(total_qty, 4)  # sum(item['qty'] for item in data)

		return frappe.render_template(
			"jewellery_erpnext/jewellery_erpnext/doctype/manufacturing_operation/stock_entry_details.html",
			{"data": data, "total_qty": total_qty},
		)

	@frappe.whitelist()
	def get_linked_stock_entries_for_serial_number_creator(self):
		target_wh = frappe.db.get_value("Warehouse", {"department": self.department})
		pmo = frappe.db.get_value(
			"Manufacturing Work Order", self.manufacturing_work_order, "manufacturing_order"
		)
		se = frappe.new_doc("Stock Entry")
		se.stock_entry_type = "Manufacture"
		mwo = frappe.get_all(
			"Manufacturing Work Order",
			{
				"name": ["!=", self.manufacturing_work_order],
				"manufacturing_order": pmo,
				"docstatus": ["!=", 2],
				"department": ["=", self.department],
			},
			pluck="name",
		)
		data = frappe.db.sql(
			f"""select se.manufacturing_work_order, se.manufacturing_operation, sed.parent, sed.item_code,sed.item_name, sed.batch_no, sed.qty, sed.uom,
					   			ifnull(sum(if(sed.uom='cts',sed.qty*0.2, sed.qty)),0) as gross_wt
			   				from `tabStock Entry Detail` sed left join `tabStock Entry` se on sed.parent = se.name where
							se.docstatus = 1 and se.manufacturing_work_order in ('{"', '".join(mwo)}') and sed.t_warehouse = '{target_wh}'
							group by sed.manufacturing_operation,  sed.item_code, sed.qty, sed.uom """,
			as_dict=1,
		)

		total_qty = 0
		for row in data:
			total_qty += row.get("gross_wt", 0)
		total_qty = round(total_qty, 4)  # sum(item['qty'] for item in data)
		bom_id = self.design_id_bom  # self.fg_bom
		mnf_qty = self.qty
		return data, bom_id, mnf_qty, total_qty

	@frappe.whitelist()
	def get_stock_entry(self):
		data = frappe.db.sql(
			f"""select se.manufacturing_work_order, se.manufacturing_operation, se.department,se.to_department,
						se.employee,se.stock_entry_type,sed.parent, sed.item_code,sed.item_name, sed.qty, sed.uom
						from `tabStock Entry Detail` sed left join `tabStock Entry` se on sed.parent = se.name where
						se.docstatus = 1 and sed.manufacturing_operation = ('{self.name}') ORDER BY se.modified DESC""",
			as_dict=1,
		)
		total_qty = len([item["qty"] for item in data])
		return frappe.render_template(
			"jewellery_erpnext/jewellery_erpnext/doctype/manufacturing_operation/stock_entry.html",
			{"data": data, "total_qty": total_qty},
		)

	@frappe.whitelist()
	def get_stock_summary(self):
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
					AND sed.manufacturing_operation IN ('{self.name}')""",
			as_dict=True,
		)
		total_qty = 0
		for row in data:
			if row.uom == "cts":
				total_qty += row.get("qty", 0) * 0.2
			else:
				total_qty += row.get("qty", 0)
		total_qty = round(total_qty, 4)
		return frappe.render_template(
			"jewellery_erpnext/jewellery_erpnext/doctype/manufacturing_operation/stock_summery.html",
			{"data": data, "total_qty": total_qty},
		)

	def set_wop_weight_details(doc):
		get_wop_weight = frappe.db.get_value(
			"Manufacturing Operation",
			{"manufacturing_work_order": doc.manufacturing_work_order, "status": ["!=", "Not Started"]},
			[
				"gross_wt",
				"net_wt",
				"diamond_wt",
				"gemstone_wt",
				"other_wt",
				"received_gross_wt",
				"received_net_wt",
				"loss_wt",
				"diamond_wt_in_gram",
				"diamond_pcs",
				"gemstone_pcs",
			],
			order_by="modified DESC",
			as_dict=1,
		)
		if get_wop_weight is None:
			return
		else:
			frappe.db.set_value(
				"Manufacturing Work Order",
				doc.manufacturing_work_order,
				{
					"gross_wt": get_wop_weight.gross_wt,
					"net_wt": get_wop_weight.net_wt,
					"diamond_wt": get_wop_weight.diamond_wt,
					"gemstone_wt": get_wop_weight.gemstone_wt,
					"other_wt": get_wop_weight.other_wt,
					"received_gross_wt": get_wop_weight.received_gross_wt,
					"received_net_wt": get_wop_weight.received_net_wt,
					"loss_wt": get_wop_weight.loss_wt,
					"diamond_wt_in_gram": get_wop_weight.diamond_wt_in_gram,
					"diamond_pcs": get_wop_weight.diamond_pcs,
					"gemstone_pcs": get_wop_weight.gemstone_pcs,
				},
				update_modified=False,
			)
			# frappe.throw(str(get_wop_weight))

	def set_pmo_weight_details(doc):
		get_mwo_weight = frappe.db.sql(
			f"""select
											sum(gross_wt) as gross_wt,
											sum(net_wt) as net_wt,
											sum(diamond_wt) as diamond_wt,
											sum(gemstone_wt)as gemstone_wt,
											sum(other_wt) as other_wt,
											sum(received_gross_wt) as received_gross_wt,
											sum(received_net_wt)as received_net_wt,
											sum(loss_wt) as loss_wt,
											sum(diamond_wt_in_gram) as diamond_wt_in_gram,
											sum(diamond_pcs) as diamond_pcs,
											sum(gemstone_pcs) as gemstone_pcs
										from `tabManufacturing Work Order`
								 		where manufacturing_order = "{doc.manufacturing_order}"
								 		and docstatus = 1""",
			as_dict=1,
		)
		if get_mwo_weight is None:
			return
		else:
			frappe.db.set_value(
				"Parent Manufacturing Order",
				doc.manufacturing_order,
				{
					"gross_weight": get_mwo_weight[0].gross_wt,
					"net_weight": get_mwo_weight[0].net_wt,
					"diamond_weight": get_mwo_weight[0].diamond_wt,
					"gemstone_weight": get_mwo_weight[0].gemstone_wt,
					# "finding_weight"		:get_mwo_weight[0].,
					"other_weight": get_mwo_weight[0].other_wt,
				},
				update_modified=False,
			)

			# To Set Product WT on PMO Tolerance METAL/Diamond/Gemstone Table.
			docname = doc.manufacturing_order
			for row in frappe.get_all(
				"Metal Product Tolerance", filters={"parent": docname}, fields=["name"]
			):
				if row:
					row_doc = frappe.get_doc("Metal Product Tolerance", row.name)
					frappe.db.set_value(
						"Metal Product Tolerance",
						row_doc.name,
						"product_wt",
						get_mwo_weight[0].gross_wt or get_mwo_weight[0].net_wt,
					)

			for row in frappe.get_all(
				"Diamond Product Tolerance", filters={"parent": docname}, fields=["name"]
			):
				if row:
					row_doc = frappe.get_doc("Diamond Product Tolerance", row.name)
					frappe.db.set_value(
						"Diamond Product Tolerance", row_doc.name, "product_wt", get_mwo_weight[0].diamond_wt
					)

			for row in frappe.get_all(
				"Gemstone Product Tolerance", filters={"parent": docname}, fields=["name"]
			):
				if row:
					row_doc = frappe.get_doc("Gemstone Product Tolerance", row.name)
					frappe.db.set_value(
						"Gemstone Product Tolerance", row_doc.name, "product_wt", get_mwo_weight[0].gemstone_wt
					)


def create_manufacturing_entry(doc, row_data):
	target_wh = frappe.db.get_value("Warehouse", {"department": doc.department})
	to_wh = frappe.db.get_value(
		"Manufacturing Setting", {"company": doc.company}, "default_fg_warehouse"
	)
	if not to_wh:
		frappe.throw("<b>Manufacturing Setting</b> Default FG Warehouse Missing...!")
	pmo = frappe.db.get_value(
		"Manufacturing Work Order", doc.manufacturing_work_order, "manufacturing_order"
	)
	pmo_det = frappe.db.get_value(
		"Parent Manufacturing Order",
		pmo,
		["name", "sales_order_item", "manufacturing_plan", "item_code", "qty"],
		as_dict=1,
	)
	if not pmo_det.qty:
		frappe.throw(f"{pmo_det.name} : Have {pmo_det.qty} Cannot Create Stock Entry")

	finish_other_tagging_operations(doc, pmo)

	se = frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"purpose": "Manufacture",
			"manufacturing_order": pmo,
			"stock_entry_type": "Manufacture",
			"department": doc.department,
			"to_department": doc.department,
			"manufacturing_work_order": doc.manufacturing_work_order,
			"manufacturing_operation": doc.manufacturing_operation,  # name,
			"custom_serial_number_creator": doc.name,
			"inventory_type": "Regular Stock",
			"auto_created": 1,
		}
	)
	# mwo = frappe.get_all("Manufacturing Work Order",
	# 			  {"name": ["!=",doc.manufacturing_work_order],"manufacturing_order": pmo, "docstatus":["!=",2], "department":["=",doc.department]},
	# 			  pluck="name")
	# data = frappe.db.sql(f"""select se.manufacturing_work_order, se.manufacturing_operation, sed.parent, sed.item_code,sed.item_name, sed.qty, sed.uom
	# 		  				from `tabStock Entry Detail` sed left join `tabStock Entry` se on sed.parent = se.name where
	# 						se.docstatus = 1 and se.manufacturing_work_order in ('{"', '".join(mwo)}') and sed.t_warehouse = '{target_wh}'
	# 						group by sed.manufacturing_operation,  sed.item_code, sed.qty, sed.uom """, as_dict=1)
	# for entry in data:
	# frappe.throw(f"{row_data}")
	for entry in row_data:
		se.append(
			"items",
			{
				"item_code": entry["item_code"],  # .item_code,
				"qty": entry["qty"],  # .qty,
				"uom": entry["uom"],  # .uom,
				"manufacturing_operation": doc.manufacturing_operation,  # doc.name,
				"department": doc.department,
				"inventory_type": "Regular Stock",
				"to_department": doc.department,
				"s_warehouse": target_wh,
			},
		)
	sr_no = ""
	compose_series = genrate_serial_no(doc)  # ,mwo_no
	# serial_no=[]
	# for i in range(pmo_det.qty):
	# 	sr_no = make_autoname(compose_series)
	# 	serial_no.append(sr_no)
	# sr_no = "\n".join(serial_no)
	sr_no = make_autoname(compose_series)
	new_bom_serial_no = sr_no  # serial_no[0]
	# doc.serial_no = sr_no
	se.append(
		"items",
		{
			"item_code": pmo_det.item_code,
			"qty": 1,  # pmo_det.qty,
			"t_warehouse": to_wh,  # target_wh,
			"department": doc.department,
			"to_department": doc.department,
			"inventory_type": "Regular Stock",
			"manufacturing_operation": doc.manufacturing_operation,  # doc.name,
			"serial_no": sr_no,
			"is_finished_item": 1,
		},
	)
	se.save()
	se.submit()
	update_produced_qty(pmo_det)
	frappe.msgprint("Finished Good created successfully")

	if doc.for_fg:
		# doc.finish_good_serial_number = get_serial_no(new_bom_serial_no) #get_serial_no(se_name)
		for row in doc.fg_details:
			for entry in row_data:
				if row.id == entry["id"] and row.row_material == entry["item_code"]:
					row.serial_no = get_serial_no(new_bom_serial_no)

	return new_bom_serial_no


def genrate_serial_no(doc):  # ,mwo_no
	errors = []
	mwo_no = (
		doc.manufacturing_work_order
	)  # mwo_no#frappe.db.get_value("Manufacturing Operation", doc.name, ['manufacturing_work_order'])
	# frappe.throw(f"{doc.name}{mwo_no}")
	if mwo_no:
		series_start = frappe.db.get_value("Manufacturing Setting", doc.company, ["series_start"])
		diamond_grade, manufacturer, posting_date = frappe.db.get_value(
			"Manufacturing Work Order", mwo_no, ["diamond_grade", "manufacturer", "posting_date"]
		)
		mnf_abbr = frappe.db.get_value("Manufacturer", manufacturer, ["custom_abbreviation"])
		dg_abbr = frappe.db.get_value("Attribute Value", diamond_grade, ["abbreviation"])
		date = f"{posting_date.year %100:02d}"
		date_to_letter = {0: "J", 1: "A", 2: "B", 3: "C", 4: "D", 5: "E", 6: "F", 7: "G", 8: "H", 9: "I"}
		final_date = date[0] + date_to_letter[int(date[1])]
		if not series_start:
			errors.append(
				f"Please set value <b>Series Start</b> on Manufacturing Setting for <strong>{doc.company}</strong>"
			)
		if not mnf_abbr:
			errors.append(
				f"Please set value <b>Abbreviation</b> on Manufacturer doctype for <strong>{doc.company}</strong>"
			)
		if not dg_abbr:
			errors.append(
				f"Please set value <b>Abbreviation</b> on Attribute Value doctype respective Diamond Grade:<b>{diamond_grade}</b>"
			)
	if errors:
		frappe.throw("<br>".join(errors))

	compose_series = str(series_start + mnf_abbr + dg_abbr + final_date + ".####")
	return compose_series


def update_produced_qty(pmo_det, cancel=False):
	qty = pmo_det.qty * (-1 if cancel else 1)
	if docname := frappe.db.exists(
		"Manufacturing Plan Table",
		{"docname": pmo_det.sales_order_item, "parent": pmo_det.manufacturing_plan},
	):
		update_existing("Manufacturing Plan Table", docname, {"produced_qty": f"produced_qty + {qty}"})
		update_existing(
			"Manufacturing Plan",
			pmo_det.manufacturing_plan,
			{"total_produced_qty": f"total_produced_qty + {qty}"},
		)


def get_stock_entries_against_mfg_operation(doc):
	if isinstance(doc, str):
		doc = frappe.get_doc("Manufacturing Operation", doc)
	wh = frappe.db.get_value("Warehouse", {"department": doc.department}, "name")
	if doc.employee:
		wh = frappe.db.get_value("Warehouse", {"employee": doc.employee}, "name")
	if doc.for_subcontracting and doc.subcontractor:
		wh = frappe.db.get_value("Warehouse", {"subcontractor": doc.subcontractor}, "name")
	sed = frappe.db.get_all(
		"Stock Entry Detail",
		filters={"t_warehouse": wh, "manufacturing_operation": doc.name, "docstatus": 1},
		fields=["item_code", "qty", "uom"],
	)
	items = {}
	for row in sed:
		existing = items.get(row.item_code)
		if existing:
			qty = existing.get("qty", 0) + row.qty
		else:
			qty = row.qty
		items[row.item_code] = {"qty": qty, "uom": row.uom}
	return items


def get_loss_details(docname):
	data = frappe.get_all(
		"Operation Loss Details",
		{"parent": docname},
		["item_code", "stock_qty as qty", "stock_uom as uom"],
	)
	items = {}
	total_loss = 0
	for row in data:
		existing = items.get(row.item_code)
		if existing:
			qty = existing.get("qty", 0) + row.qty
		else:
			qty = row.qty
		total_loss += row.qty * 0.2 if row.uom == "cts" else row.qty
		items[row.item_code] = {"qty": qty, "uom": row.uom}
	items["total_loss"] = total_loss
	return items


def get_previous_operation(manufacturing_operation):
	mfg_operation = frappe.db.get_value(
		"Manufacturing Operation",
		manufacturing_operation,
		["previous_operation", "manufacturing_work_order"],
		as_dict=1,
	)
	if not mfg_operation.previous_operation:
		return None
	return frappe.db.get_value(
		"Manufacturing Operation",
		{
			"operation": mfg_operation.previous_operation,
			"manufacturing_work_order": mfg_operation.manufacturing_work_order,
		},
	)


def get_material_wt(doc):
	filters = {}
	if doc.for_subcontracting:
		if doc.subcontractor:
			filters["subcontractor"] = doc.subcontractor
	else:
		if doc.employee:
			filters["employee"] = doc.employee
	if not filters:
		filters["department"] = doc.department
	t_warehouse = frappe.db.get_value("Warehouse", filters, "name")
	res = frappe.db.sql(
		f"""select ifnull(sum(if(sed.uom='cts',sed.qty*0.2, sed.qty)),0) as gross_wt, ifnull(sum(if(i.variant_of = 'M',sed.qty,0)),0) as net_wt,
		ifnull(sum(if(i.variant_of = 'D',sed.qty,0)),0) as diamond_wt, ifnull(sum(if(i.variant_of = 'D',if(sed.uom='cts',sed.qty*0.2, sed.qty),0)),0) as diamond_wt_in_gram,
		ifnull(sum(if(i.variant_of = 'G',sed.qty,0)),0) as gemstone_wt, ifnull(sum(if(i.variant_of = 'G',if(sed.uom='cts',sed.qty*0.2, sed.qty),0)),0) as gemstone_wt_in_gram,
		ifnull(sum(if(i.variant_of = 'O',sed.qty,0)),0) as other_wt
		from `tabStock Entry Detail` sed left join `tabStock Entry` se on sed.parent = se.name left join `tabItem` i on i.name = sed.item_code
			where sed.t_warehouse = "{t_warehouse}" and sed.manufacturing_operation = "{doc.name}" and se.docstatus = 1""",
		as_dict=1,
	)
	if res:
		return res[0]
	return {}


def create_finished_goods_bom(self, se_name):
	data = get_stock_entry_data(self)

	new_bom = frappe.copy_doc(frappe.get_doc("BOM", self.design_id_bom))
	new_bom.bom_type = "Finish Goods"
	new_bom.tag_no = get_serial_no(se_name)
	new_bom.custom_serial_number_creator = self.name
	new_bom.metal_detail = []
	new_bom.finding_detail = []
	new_bom.diamond_detail = []
	new_bom.gemstone_detail = []
	new_bom.other_detail = []
	# new_bom.items = []

	for item in data:
		item_row = frappe.get_doc("Item", item["item_code"])

		if item_row.variant_of == "M":
			row = {}
			for attribute in item_row.attributes:
				atrribute_name = format_attrbute_name(attribute.attribute)
				row[atrribute_name] = attribute.attribute_value
				row["quantity"] = item["qty"]
			new_bom.append("metal_detail", row)

		elif item_row.variant_of == "F":
			row = {}
			for attribute in item_row.attributes:
				atrribute_name = format_attrbute_name(attribute.attribute)
				row[atrribute_name] = attribute.attribute_value
				row["quantity"] = item["qty"]
			new_bom.append("finding_detail", row)

		elif item_row.variant_of == "D":
			row = {}
			for attribute in item_row.attributes:
				atrribute_name = format_attrbute_name(attribute.attribute)
				row[atrribute_name] = attribute.attribute_value
				row["quantity"] = item["qty"]
			new_bom.append("diamond_detail", row)

		elif item_row.variant_of == "G":
			row = {}
			for attribute in item_row.attributes:
				atrribute_name = format_attrbute_name(attribute.attribute)
				row[atrribute_name] = attribute.attribute_value
				row["quantity"] = item["qty"]
			new_bom.append("gemstone_detail", row)

		elif item_row.variant_of == "O":
			row = {}
			for attribute in item_row.attributes:
				atrribute_name = format_attrbute_name(attribute.attribute)
				row[atrribute_name] = attribute.attribute_value
				row["quantity"] = item["qty"]
			new_bom.append("other_detail", row)

	new_bom.insert(ignore_mandatory=True)
	self.fg_bom = new_bom.name


def get_stock_entry_data(self):
	target_wh = frappe.db.get_value("Warehouse", {"department": self.department})
	pmo = frappe.db.get_value(
		"Manufacturing Work Order", self.manufacturing_work_order, "manufacturing_order"
	)
	# se = frappe.new_doc("Stock Entry")
	# se.stock_entry_type = "Manufacture"
	mwo = frappe.get_all(
		"Manufacturing Work Order",
		{
			"name": ["!=", self.manufacturing_work_order],
			"manufacturing_order": pmo,
			"docstatus": ["!=", 2],
			"department": ["=", self.department],
		},
		pluck="name",
	)
	data = frappe.db.sql(
		f"""select se.manufacturing_work_order, se.manufacturing_operation, sed.parent, sed.item_code,sed.item_name, sed.qty, sed.uom
						from `tabStock Entry Detail` sed left join `tabStock Entry` se on sed.parent = se.name where
						se.docstatus = 1 and se.manufacturing_work_order in ('{"', '".join(mwo)}') and sed.t_warehouse = '{target_wh}'
						group by sed.manufacturing_operation,  sed.item_code, sed.qty, sed.uom """,
		as_dict=1,
	)

	return data


def format_attrbute_name(input_string):
	# Replace spaces with underscores and convert to lowercase
	formatted_string = input_string.replace(" ", "_").lower()
	return formatted_string


def get_serial_no(se_name):
	# se_doc = frappe.get_doc('Stock Entry',se_name)
	# for row in se_doc.items:
	# 	if row.is_finished_item:
	# 		serial_no = row.serial_no
	serial_no = se_name
	return str(serial_no)


def finish_other_tagging_operations(doc, pmo):
	mop_data = frappe.db.sql(
		"""SELECT manufacturing_order,name as manufacturing_operation,status
				FROM `tabManufacturing Operation`
				WHERE manufacturing_order = %(manufacturing_order)s
				AND name != %(manufacturing_operation)s
				AND status != 'Finished' AND department = %(department)s """,
		(
			{
				"manufacturing_order": pmo,
				"department": doc.department,
				"manufacturing_operation": doc.manufacturing_operation,
			}
		),
		as_dict=1,
	)  # name

	for mop in mop_data:
		frappe.db.set_value("Manufacturing Operation", mop.manufacturing_operation, "status", "Finished")
