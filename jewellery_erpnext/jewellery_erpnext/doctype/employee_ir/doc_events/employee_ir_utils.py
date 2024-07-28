import frappe
from frappe import _
from frappe.model.mapper import get_mapped_doc
from frappe.query_builder import DocType
from frappe.utils import flt

from jewellery_erpnext.utils import get_item_from_attribute


def valid_reparing_or_next_operation(self):
	if self.type == "Issue":
		mwo_list = [row.manufacturing_work_order for row in self.employee_ir_operations]
		operation = self.operation

		EmployeeIR = DocType("Employee IR")
		EmployeeIROperation = DocType("Employee IR Operation")

		query = (
			frappe.qb.from_(EmployeeIR)
			.join(EmployeeIROperation)
			.on(EmployeeIROperation.parent == EmployeeIR.name)
			.select(EmployeeIR.name)
			.where((EmployeeIR.name != self.name) & (EmployeeIR.operation == operation))
		)

		if mwo_list:
			query = query.where(EmployeeIROperation.manufacturing_work_order.isin(mwo_list))

		test = query.run(as_dict=True)
		if test:
			self.transfer_type = "Repairing"


def get_po_rates(supplier, operation, purchase_type, row):
	item_details = frappe.db.get_value(
		"Manufacturing Operation", row.manufacturing_operation, ["metal_type", "item_code"], as_dict=1
	)
	sub_category = frappe.db.get_value("Item", item_details.item_code, "item_subcategory")

	sup_ser_pri_item_sub = frappe.qb.DocType("Supplier Services Price Item Subcategory")
	supplier_serivice_price = frappe.qb.DocType("Supplier Services Price")
	return (
		frappe.qb.from_(sup_ser_pri_item_sub)
		.join(supplier_serivice_price)
		.on(supplier_serivice_price.name == sup_ser_pri_item_sub.parent)
		.select(sup_ser_pri_item_sub.rate_per_gm)
		.where(
			(sup_ser_pri_item_sub.supplier == supplier)
			and (sup_ser_pri_item_sub.metal_type == item_details.get("metal_type"))
			and (supplier_serivice_price.type_of_subcontracting == operation)
			and (supplier_serivice_price.purchase_type == purchase_type)
			and (sup_ser_pri_item_sub.sub_category == sub_category)
		)
	).run(as_dict=True)


def create_chain_stock_entry(self, row):
	status = "Finished"
	new_opration = create_operation_for_next_op(row.manufacturing_operation, employee_ir=self.name)
	frappe.db.set_value(
		"Manufacturing Work Order",
		row.manufacturing_work_order,
		"manufacturing_operation",
		new_opration,
	)
	frappe.db.set_value(
		"Manufacturing Operation",
		row.manufacturing_operation,
		"status",
		status,
	)
	metal_data = frappe.db.get_value(
		"Manufacturing Work Order",
		row.manufacturing_work_order,
		["metal_type", "metal_touch", "metal_purity", "metal_colour", "master_bom", "item_code"],
		as_dict=1,
	)

	item = metal_data["item_code"]
	metal_item = get_item_from_attribute(
		metal_data.metal_type, metal_data.metal_touch, metal_data.metal_purity, metal_data.metal_colour
	)
	department_wh = frappe.get_value(
		"Warehouse", {"department": self.department, "warehouse_type": "Manufacturing", "disabled": 0}
	)
	if self.subcontracting == "Yes":
		employee_wh = frappe.get_value(
			"Warehouse", {"subcontractor": self.subcontractor, "warehouse_type": "Manufacturing"}
		)
	else:
		employee_wh = frappe.get_value(
			"Warehouse", {"employee": self.employee, "warehouse_type": "Manufacturing"}
		)

	bom_items = frappe.db.get_all("BOM Item", {"parent": metal_data.master_bom}, "item_code")

	exploded_items = frappe.db.get_all(
		"BOM Explosion Item", {"parent": metal_data.master_bom}, "item_code"
	)

	pure_item = frappe.db.get_value("Manufacturing Setting", {"name": self.company}, "pure_gold_item")

	item_list = [row.item_code for row in bom_items + exploded_items]

	filters = {
		"parentfield": "batch_details",
		"parent": self.main_slip,
		"item_code": ["in", item_list],
		"qty": [">", 0],
	}

	metal_filters = {
		"parentfield": "batch_details",
		"parent": self.main_slip,
		"item_code": metal_item,
		"qty": [">", 0],
	}

	pure_filters = {
		"parentfield": "batch_details",
		"parent": self.main_slip,
		"item_code": pure_item,
		"qty": [">", 0],
	}

	main_slip_data = frappe.db.get_all(
		"Main Slip SE Details",
		filters,
		["item_code", "batch_no", "qty", "consume_qty", "inventory_type"],
	)

	metal_item_data = frappe.db.get_all(
		"Main Slip SE Details",
		metal_filters,
		["item_code", "batch_no", "qty", "consume_qty", "inventory_type"],
	)

	pure_data = frappe.db.get_all(
		"Main Slip SE Details",
		pure_filters,
		["item_code", "batch_no", "qty", "consume_qty", "inventory_type"],
	)

	diff = row.received_gross_wt - row.gross_wt

	se_doc = frappe.new_doc("Stock Entry")
	se_doc.stock_entry_type = "Material Transfer"
	se_doc.purpose = "Material Transfer"
	se_doc.manufacturing_order = frappe.db.get_value(
		"Manufacturing Work Order", row.manufacturing_work_order, "manufacturing_order"
	)
	se_doc.manufacturing_work_order = row.manufacturing_work_order
	se_doc.manufacturing_operation = row.manufacturing_operation
	se_doc.department = self.department
	se_doc.to_department = self.department
	se_doc.main_slip = self.main_slip
	se_doc.employee = self.employee
	se_doc.subcontractor = self.subcontractor
	se_doc.auto_created = True
	se_doc.employee_ir = self.name
	se_doc.branch = "GE-BR-00001"
	warehouse = frappe.db.get_value("Main Slip", self.main_slip, "raw_material_warehouse")
	mfg_warehouse = frappe.db.get_value("Main Slip", self.main_slip, "warehouse")

	temp_diff = abs(diff)
	if abs(diff) > 0 and main_slip_data:
		temp_diff, reg_batch_list = create_repack(self, row, item, main_slip_data, warehouse, temp_diff)
		for batch in reg_batch_list:
			se_doc.append(
				"items",
				{
					"item_code": item,
					"s_warehouse": warehouse,
					"t_warehouse": mfg_warehouse,
					"to_employee": None,
					"employee": self.employee,
					"to_subcontractor": None,
					"use_serial_batch_fields": True,
					"serial_and_batch_bundle": None,
					"subcontractor": self.subcontractor,
					"to_main_slip": None,
					"main_slip": self.main_slip,
					"qty": reg_batch_list.get(batch) or 0,
					"manufacturing_operation": row.manufacturing_operation,
					"department": self.department,
					"to_department": self.department,
					"manufacturer": self.manufacturer,
					"material_request": None,
					"material_request_item": None,
					"batch_no": batch,
				},
			)

	if temp_diff > 0 and metal_item_data:
		temp_diff, batch_list = create_repack(self, row, item, metal_item_data, warehouse, temp_diff)
		for batch in batch_list:
			se_doc.append(
				"items",
				{
					"item_code": item,
					"s_warehouse": warehouse,
					"t_warehouse": mfg_warehouse,
					"to_employee": None,
					"employee": self.employee,
					"to_subcontractor": None,
					"use_serial_batch_fields": True,
					"serial_and_batch_bundle": None,
					"subcontractor": self.subcontractor,
					"to_main_slip": None,
					"main_slip": self.main_slip,
					"qty": batch_list.get(batch) or 0,
					"manufacturing_operation": row.manufacturing_operation,
					"department": self.department,
					"to_department": self.department,
					"manufacturer": self.manufacturer,
					"material_request": None,
					"material_request_item": None,
					"batch_no": batch,
				},
			)

	if temp_diff > 0 and pure_data:
		temp_diff, pure_batches = create_purity_repack(
			self, row, item, pure_data, warehouse, temp_diff, metal_data.metal_purity
		)
		for batch in pure_batches:
			se_doc.append(
				"items",
				{
					"item_code": item,
					"s_warehouse": warehouse,
					"t_warehouse": mfg_warehouse,
					"to_employee": None,
					"employee": self.employee,
					"to_subcontractor": None,
					"use_serial_batch_fields": True,
					"serial_and_batch_bundle": None,
					"subcontractor": self.subcontractor,
					"to_main_slip": None,
					"main_slip": self.main_slip,
					"qty": pure_batches.get(batch) or 0,
					"manufacturing_operation": row.manufacturing_operation,
					"department": self.department,
					"to_department": self.department,
					"manufacturer": self.manufacturer,
					"material_request": None,
					"material_request_item": None,
					"batch_no": batch,
				},
			)

	if temp_diff > 0:
		frappe.throw(_("Qty not available"))

	se_doc.set_posting_date = 1
	se_doc.posting_time = frappe.utils.nowtime()
	se_doc.save()
	se_doc.submit()

	mop_se_doc = frappe.new_doc("Stock Entry")
	mop_se_doc.stock_entry_type = "Material Transfer"
	mop_se_doc.purpose = "Material Transfer"
	mop_se_doc.manufacturing_order = frappe.db.get_value(
		"Manufacturing Work Order", row.manufacturing_work_order, "manufacturing_order"
	)
	mop_se_doc.manufacturing_work_order = row.manufacturing_work_order
	mop_se_doc.manufacturing_operation = row.manufacturing_operation
	mop_se_doc.department = self.department
	mop_se_doc.to_department = self.department
	mop_se_doc.main_slip = self.main_slip
	mop_se_doc.employee = self.employee
	mop_se_doc.subcontractor = self.subcontractor
	mop_se_doc.auto_created = True
	mop_se_doc.employee_ir = self.name

	import copy

	for row in se_doc.items:
		copy_row = copy.deepcopy(row)
		copy_row.s_warehouse = mfg_warehouse
		copy_row.t_warehouse = department_wh
		copy_row.serial_and_batch_bundle = None
		mop_se_doc.append("items", copy_row)

	mop_se_doc.save()
	mop_se_doc.submit()

	# create_loss_entry(self, row, employee_wh, warehouse)


def create_loss_entry(self, row, employee_wh, warehouse):
	manual_loss_items = [
		{
			"item_code": loss_item.item_code,
			"loss_qty": loss_item.proportionally_loss,
			"batch_no": loss_item.batch_no,
			"inventory_type": loss_item.inventory_type,
			"customer": loss_item.customer,
			"pcs": loss_item.pcs,
			"manufacturing_work_order": loss_item.manufacturing_work_order,
			"manufacturing_operation": loss_item.manufacturing_operation,
			"variant_of": loss_item.variant_of,
			"sub_setting_type": loss_item.sub_setting_type,
			"loss_type": loss_item.loss_type,
		}
		for loss_item in self.manually_book_loss_details
		if loss_item.manufacturing_work_order == row.manufacturing_work_order
	]
	if manual_loss_items:
		se_doc = frappe.new_doc("Stock Entry")
		se_doc.stock_entry_type = "Process Loss"
		se_doc.purpose = "Material Transfer"
		se_doc.main_slip = self.main_slip
		se_doc.employee = self.employee
		se_doc.subcontractor = self.subcontractor
		se_doc.auto_created = True
		se_doc.employee_ir = self.name
		se_doc.branch = "GE-BR-00001"

		for row in manual_loss_items:
			se_doc.append(
				"items",
				{
					"item_code": row.item_code,
					"s_warehouse": employee_wh,
					"t_warehouse": warehouse,
					"to_employee": None,
					"employee": self.employee,
					"to_subcontractor": None,
					"use_serial_batch_fields": True,
					"serial_and_batch_bundle": None,
					"subcontractor": self.subcontractor,
					"to_main_slip": None,
					"main_slip": self.main_slip,
					"qty": row.loss_qty,
					"manufacturing_operation": row.manufacturing_operation,
					"department": self.department,
					"to_department": self.department,
					"manufacturer": self.manufacturer,
					"material_request": None,
					"material_request_item": None,
				},
			)

		se_doc.save()
		se_doc.submit()


def create_repack(self, row, item, metal_data, warehouse, temp_diff):
	se_doc = frappe.new_doc("Stock Entry")
	se_doc.stock_entry_type = "Repack"
	se_doc.purpose = "Repack"
	se_doc.main_slip = self.main_slip
	se_doc.employee = self.employee
	se_doc.subcontractor = self.subcontractor
	se_doc.auto_created = True
	se_doc.employee_ir = self.name
	se_doc.branch = "GE-BR-00001"
	se_doc.posting_time = frappe.utils.nowtime()
	se_doc.set_posting_time = 1
	batch_dict = {}
	for row in metal_data:
		if temp_diff > 0:
			if (row.consume_qty + temp_diff) <= row.qty:
				se_qty = temp_diff
				temp_diff = 0
			else:
				se_qty = row.qty - row.consume_qty
				temp_diff -= se_qty

			se_doc.append(
				"items",
				{
					"item_code": row.item_code,
					"s_warehouse": warehouse,
					"to_employee": None,
					"employee": self.employee,
					"to_subcontractor": None,
					"use_serial_batch_fields": True,
					"serial_and_batch_bundle": None,
					"subcontractor": self.subcontractor,
					"to_main_slip": None,
					"main_slip": self.main_slip,
					"qty": abs(se_qty),
					"manufacturer": self.manufacturer,
					"material_request": None,
					"material_request_item": None,
					"batch_no": row.batch_no,
					"inventory_type": row.inventory_type,
				},
			)

			from frappe.model.naming import make_autoname

			batch_number_series = frappe.db.get_value("Item", item, "batch_number_series")

			batch_doc = frappe.new_doc("Batch")
			batch_doc.item = item

			if batch_number_series:
				batch_doc.batch_id = make_autoname(batch_number_series, doc=batch_doc)

			batch_doc.flags.ignore_permissions = True
			batch_doc.save()
			se_doc.append(
				"items",
				{
					"item_code": item,
					"t_warehouse": warehouse,
					"to_employee": None,
					"employee": self.employee,
					"to_subcontractor": None,
					"use_serial_batch_fields": True,
					"serial_and_batch_bundle": None,
					"subcontractor": self.subcontractor,
					"to_main_slip": None,
					"main_slip": self.main_slip,
					"qty": abs(se_qty),
					"manufacturer": self.manufacturer,
					"material_request": None,
					"material_request_item": None,
					"batch_no": batch_doc.name,
					"manufacturing_operation": row.manufacturing_operation,
				},
			)
			batch_dict.update({batch_doc.name: abs(se_qty)})

	se_doc.save()
	se_doc.submit()
	return temp_diff, batch_dict


def create_purity_repack(self, row, item, pure_data, warehouse, temp_diff, purity_doc):
	se_doc = frappe.new_doc("Stock Entry")
	se_doc.stock_entry_type = "Repack"
	se_doc.purpose = "Repack"
	se_doc.main_slip = self.main_slip
	se_doc.employee = self.employee
	se_doc.subcontractor = self.subcontractor
	se_doc.auto_created = True
	se_doc.employee_ir = self.name
	se_doc.branch = "GE-BR-00001"
	batch_dict = {}
	for row in pure_data:
		purity = frappe.db.get_value("Attribute Value", purity_doc, "purity_percentage")
		reqd_qty = temp_diff
		if purity > 0:
			reqd_qty = (purity * temp_diff) / 100

		reqd_qty = flt(reqd_qty, 3)
		if temp_diff > 0:
			if (row.consume_qty + reqd_qty) <= row.qty:
				se_qty = reqd_qty
				temp_diff = 0
			else:
				se_qty = row.qty - row.consume_qty
				temp_diff -= flt((se_qty * 100) / purity, 3)

			se_doc.append(
				"items",
				{
					"item_code": row.item_code,
					"s_warehouse": warehouse,
					"to_employee": None,
					"employee": self.employee,
					"to_subcontractor": None,
					"use_serial_batch_fields": True,
					"serial_and_batch_bundle": None,
					"subcontractor": self.subcontractor,
					"to_main_slip": None,
					"main_slip": self.main_slip,
					"qty": abs(se_qty),
					"manufacturer": self.manufacturer,
					"material_request": None,
					"material_request_item": None,
					"batch_no": row.batch_no,
					"inventory_type": row.inventory_type,
				},
			)

			from frappe.model.naming import make_autoname

			batch_number_series = frappe.db.get_value("Item", item, "batch_number_series")

			batch_doc = frappe.new_doc("Batch")
			batch_doc.item = item

			if batch_number_series:
				batch_doc.batch_id = make_autoname(batch_number_series, doc=batch_doc)

			batch_doc.flags.ignore_permissions = True
			batch_doc.save()
			se_doc.append(
				"items",
				{
					"item_code": item,
					"t_warehouse": warehouse,
					"to_employee": None,
					"employee": self.employee,
					"to_subcontractor": None,
					"use_serial_batch_fields": True,
					"serial_and_batch_bundle": None,
					"subcontractor": self.subcontractor,
					"to_main_slip": None,
					"main_slip": self.main_slip,
					"qty": abs(flt((se_qty * 100) / purity, 3)),
					"manufacturer": self.manufacturer,
					"material_request": None,
					"material_request_item": None,
					"batch_no": batch_doc.name,
					"manufacturing_operation": row.manufacturing_operation,
				},
			)
			batch_dict.update({batch_doc.name: abs(flt((se_qty * 100) / purity, 3))})

	se_doc.save()
	se_doc.submit()
	return temp_diff, batch_dict


def create_operation_for_next_op(docname, target_doc=None, employee_ir=None):
	def set_missing_value(source, target):
		target.previous_operation = source.operation
		target.prev_gross_wt = source.received_gross_wt or source.gross_wt or source.prev_gross_wt
		target.previous_mop = source.name

	target_doc = get_mapped_doc(
		"Manufacturing Operation",
		docname,
		{
			"Manufacturing Operation": {
				"doctype": "Manufacturing Operation",
				"field_no_map": [
					"status",
					"employee",
					"start_time",
					"subcontractor",
					"for_subcontracting",
					"finish_time",
					"time_taken",
					"department_issue_id",
					"department_receive_id",
					"department_ir_status",
					"operation",
					"previous_operation",
					"start_time",
					"finish_time",
					"time_taken",
					"started_time",
					"current_time",
					"on_hold",
					"total_minutes",
					"time_logs",
				],
			}
		},
		target_doc,
		set_missing_value,
	)
	target_doc.department_source_table = []
	target_doc.department_target_table = []
	target_doc.employee_source_table = []
	target_doc.employee_target_table = []
	target_doc.employee_ir = employee_ir
	target_doc.time_taken = None
	target_doc.save()
	target_doc.db_set("employee", None)

	# timer code
	target_doc.start_time = ""
	target_doc.finish_time = ""
	target_doc.time_taken = ""
	target_doc.started_time = ""
	target_doc.current_time = ""
	target_doc.time_logs = []
	target_doc.total_time_in_mins = ""
	target_doc.save()
	return target_doc.name
