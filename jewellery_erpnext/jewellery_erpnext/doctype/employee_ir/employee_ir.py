# Copyright (c) 2023, Nirali and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.mapper import get_mapped_doc

# timer code
from frappe.utils import cint, flt, get_datetime, time_diff_in_seconds, today

from jewellery_erpnext.jewellery_erpnext.doctype.department_ir.department_ir import (
	get_material_wt,
	update_stock_entry_dimensions,
)
from jewellery_erpnext.jewellery_erpnext.doctype.main_slip.main_slip import get_main_slip_item

# from jewellery_erpnext.jewellery_erpnext.doctype.qc.qc import create_qc_record
from jewellery_erpnext.jewellery_erpnext.doctype.manufacturing_operation.manufacturing_operation import (
	get_loss_details,
	get_previous_operation,
)
from jewellery_erpnext.utils import (
	get_item_from_attribute,
	get_item_from_attribute_full,
	update_existing,
)


class EmployeeIR(Document):
	@frappe.whitelist()
	def get_operations(self):
		records = frappe.get_list(
			"Manufacturing Operation",
			{"department": self.department, "employee": ["is", "not set"], "operation": ["is", "not set"]},
			["name", "gross_wt"],
		)
		self.employee_ir_operations = []
		if records:
			for row in records:
				self.append("employee_ir_operations", {"manufacturing_operation": row.name})

	def on_submit(self):
		if self.type == "Issue":
			self.on_submit_issue()
			if self.subcontracting == "Yes":
				self.create_subcontracting_order()
		else:
			self.on_submit_receive()

	def validate(self):
		self.validate_gross_wt()
		self.validate_main_slip()
		self.update_main_slip()
		if not self.is_new():
			self.validate_qc("Warn")

	def after_insert(self):
		self.validate_qc("Warn")

	def on_cancel(self):
		if self.type == "Issue":
			self.on_submit_issue(cancel=True)
		else:
			self.on_submit_receive(cancel=True)

	def validate_gross_wt(self):
		precision = cint(frappe.db.get_single_value("System Settings", "float_precision"))
		for row in self.employee_ir_operations:
			row.gross_wt = frappe.db.get_value(
				"Manufacturing Operation", row.manufacturing_operation, "gross_wt"
			)
			if not self.main_slip:
				if flt(row.gross_wt, precision) < flt(row.received_gross_wt, precision):
					frappe.throw(f"Row #{row.idx}: Received gross wt cannot be greater than gross wt")

	# for issue
	def on_submit_issue(self, cancel=False):
		# timer code
		now = frappe.utils.now()

		employee = None if cancel else self.employee
		operation = None if cancel else self.operation
		status = "Not Started" if cancel else "WIP"
		values = {"operation": operation, "status": status}
		if self.subcontracting == "Yes":
			values["for_subcontracting"] = 1
			values["subcontractor"] = None if cancel else self.subcontractor
		else:
			values["employee"] = employee
		for row in self.employee_ir_operations:
			if not cancel:
				update_stock_entry_dimensions(self, row, row.manufacturing_operation, True)
				create_stock_entry(self, row)
				res = get_material_wt(self, row.manufacturing_operation)
				row.gross_wt = res.get("gross_wt")
				values.update(res)
			# values["gross_wt"] = get_value("Stock Entry Detail", {'manufacturing_operation': row.manufacturing_operation, "to_employee":self.employee}, 'sum(if(uom="Carat",qty*0.2,qty))', 0)
			frappe.db.set_value("Manufacturing Operation", row.manufacturing_operation, values)

			# timer code
			values["start_time"] = now
			doc = frappe.get_doc("Manufacturing Operation", row.manufacturing_operation)
			add_time_log(doc, values)

	# for receive
	def on_submit_receive(self, cancel=False):
		self.validate_qc("Stop")
		# timer code
		now = frappe.utils.now()

		precision = cint(frappe.db.get_single_value("System Settings", "float_precision"))
		for row in self.employee_ir_operations:
			# frappe.throw(f"{row.received_gross_wt} {row.gross_wt}")
			if self.type == "Receive":
				if row.received_gross_wt == 0 and row.gross_wt != 0:
					frappe.throw("Received Gross Wt Missing")
			res = {"received_gross_wt": row.received_gross_wt}
			# timer code
			res["employee"] = self.employee
			status = "WIP"
			if not cancel:
				status = "Finished"
				new_opration = create_operation_for_next_op(row.manufacturing_operation, employee_ir=self.name)
				res["complete_time"] = now
				frappe.db.set_value(
					"Manufacturing Work Order",
					row.manufacturing_work_order,
					"manufacturing_operation",
					new_opration,
				)

				doc = frappe.get_doc("Manufacturing Operation", row.manufacturing_operation)
				add_time_log(doc, res)

				difference_wt = flt(row.received_gross_wt, precision) - flt(row.gross_wt, precision)

				create_stock_entry(self, row, flt(difference_wt, precision))
				# res = get_material_wt(self, row.manufacturing_operation)
			else:
				op = frappe.db.get_value("Manufacturing Operation", {"employee_ir": self.name})
				if op:
					frappe.delete_doc("Manufacturing Operation", op, ignore_permissions=1)
					if self.is_qc_reqd:
						status = "QC Pending"
				# need to how  handle cancellation
				# mfg_operation = frappe.db.exists("Manufacturing Operation", {"employee_ir": self.name})
			res["status"] = status
			# gross_wt = get_value("Stock Entry Detail", {'manufacturing_operation': row.manufacturing_operation, "employee":self.employee}, 'sum(if(uom="Carat",qty*0.2,qty))', 0)
			frappe.set_value("Manufacturing Operation", row.manufacturing_operation, res)

	def validate_qc(self, action="Warn"):
		if not self.is_qc_reqd or self.type == "Issue":
			return

		qc_list = []
		for row in self.employee_ir_operations:
			operation = frappe.db.get_value(
				"Manufacturing Operation", row.manufacturing_operation, ["status"], as_dict=1
			)
			if operation.get("status") in ["QC Pending", "WIP"]:
				if action == "Warn":
					create_qc_record(row, self.operation, self.name)
				qc_list.append(row.manufacturing_operation)
		if qc_list:
			msg = f"Please complete QC for the following: {', '.join(qc_list)}"
			if action == "Warn":
				frappe.msgprint(_(msg))
			elif action == "Stop":
				frappe.msgprint(_(msg))

	def update_main_slip(self):
		if not self.main_slip or not self.is_main_slip_required:
			return

		main_slip = frappe.get_doc("Main Slip", self.main_slip)
		for row in self.employee_ir_operations:
			if not main_slip.get(
				"main_slip_operation", {"manufacturing_operation": row.manufacturing_operation}
			):
				main_slip.append(
					"main_slip_operation", {"manufacturing_operation": row.manufacturing_operation}
				)
		main_slip.save()

	def validate_main_slip(self):
		dep_opr = frappe.get_value("Department Operation", self.operation, "check_colour_in_main_slip")
		if self.main_slip and dep_opr == 1:
			for row in self.employee_ir_operations:
				mwo = frappe.db.get_value(
					"Manufacturing Work Order",
					row.manufacturing_work_order,
					[
						"metal_type",
						"metal_touch",
						"metal_purity",
						"metal_colour",
						"multicolour",
						"allowed_colours",
					],
					as_dict=1,
				)
				ms = frappe.db.get_value(
					"Main Slip",
					self.main_slip,
					[
						"metal_type",
						"metal_touch",
						"metal_purity",
						"metal_colour",
						"check_color",
						"for_subcontracting",
						"multicolour",
						"allowed_colours",
					],
					as_dict=1,
				)
				if mwo.allowed_colours:
					allowed_colors_mwo = "".join(sorted(map(str.upper, mwo.allowed_colours)))
				else:
					allowed_colors_mwo = "".join(map(str.upper, mwo.metal_colour[0]))
				# frappe.throw(f"{allowed_colors_mwo}")

				color_matched = False  # Flag to check if at least one color matches
				if ms.allowed_colours:
					multi_colors_ms = "".join(sorted(map(str.upper, ms.allowed_colours)))
					if allowed_colors_mwo == multi_colors_ms:
						color_matched = True

				if ms.metal_colour:
					single_colors_ms = "".join(map(str.upper, ms.metal_colour[0]))
					for mwo_char in allowed_colors_mwo:
						for ms_char in single_colors_ms:
							if ms_char == mwo_char:
								color_matched = True

				if color_matched == False:
					frappe.throw(f"Main slip color mismatch, allowed color: <b>{allowed_colors_mwo}</b>")

	@frappe.whitelist()
	def create_subcontracting_order(self):
		service_item = frappe.db.get_value("Department Operation", self.operation, "service_item")
		if not service_item:
			frappe.throw(_(f"Please set service item for {self.operation}"))
		skip_operations = []
		po = frappe.new_doc("Purchase Order")
		po.supplier = self.subcontractor
		po.company = self.company
		po.employee_ir = self.name
		for row in self.employee_ir_operations:
			if not row.gross_wt:
				skip_operations.append(row.manufacturing_operation)
				continue
			po.append(
				"items",
				{
					"item_code": service_item,
					"qty": row.gross_wt,
					"schedule_date": today(),
					"manufacturing_operation": row.manufacturing_operation,
				},
			)
		if skip_operations:
			frappe.throw(
				f"PO creation skipped for following Manufacturing Operations due to zero gross weight: {', '.join(skip_operations)}"
			)
		if not po.items:
			return
		po.flags.ignore_mandatory = True
		po.save()
		po.db_set("schedule_date", None)
		for row in po.items:
			row.db_set("schedule_date", None)


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


@frappe.whitelist()
def get_manufacturing_operations(source_name, target_doc=None):
	if not target_doc:
		target_doc = frappe.new_doc("Employee IR")
	elif isinstance(target_doc, str):
		target_doc = frappe.get_doc(json.loads(target_doc))
	if not target_doc.get("employee_ir_operations", {"manufacturing_operation": source_name}):
		operation = frappe.db.get_value(
			"Manufacturing Operation", source_name, ["gross_wt", "manufacturing_work_order"], as_dict=1
		)
		target_doc.append(
			"employee_ir_operations",
			{
				"manufacturing_operation": source_name,
				"gross_wt": operation["gross_wt"],
				"manufacturing_work_order": operation["manufacturing_work_order"],
			},
		)
	return target_doc


def create_stock_entry(doc, row, difference_wt=0):
	department_wh = frappe.get_value(
		"Warehouse", {"department": doc.department, "warehouse_type": "Manufacturing"}
	)
	if doc.subcontracting == "Yes":
		employee_wh = frappe.get_value("Warehouse", {"subcontractor": doc.subcontractor})
	else:
		employee_wh = frappe.get_value(
			"Warehouse", {"employee": doc.employee, "warehouse_type": "Manufacturing"}
		)
	if not department_wh:
		frappe.throw(_(f"Please set warhouse for department {doc.department}"))
	if not employee_wh:
		frappe.throw(
			_(
				f"Please set warhouse for {'subcontractor' if doc.subcontracting == 'Yes' else 'employee'} {doc.subcontractor if doc.subcontracting == 'Yes' else doc.employee}"
			)
		)
	stock_entries = frappe.db.sql(
		f"""select se.name from `tabStock Entry Detail` sed left join `tabStock Entry` se on sed.parent = se.name
			       where se.auto_created = 1 and se.docstatus=1 and sed.manufacturing_operation = '{row.manufacturing_operation}' and
				   {"sed.t_warehouse" if doc.type == "Issue" else "sed.s_warehouse"} = '{department_wh}'
				   and sed.to_department = '{doc.department}' group by se.name order by se.posting_date""",
		as_dict=1,
		pluck=1,
	)

	manual_se_entries = []
	if doc.type == "Issue":
		manual_se_entries = frappe.db.sql(
			f"""select se.name from `tabStock Entry Detail` sed left join `tabStock Entry` se on sed.parent = se.name
						where se.auto_created = 0 and se.docstatus=1 and sed.manufacturing_operation = '{row.manufacturing_operation}' and
						{"sed.t_warehouse" if doc.type == "Issue" else "sed.s_warehouse"} = '{department_wh}'
						and sed.to_department = '{doc.department}' group by se.name order by se.posting_date""",
			as_dict=1,
			pluck=1,
		)

		if not stock_entries:
			prev_mfg_operation = get_previous_operation(row.manufacturing_operation)
			stock_entries = frappe.db.sql(
				f"""select se.name from `tabStock Entry Detail` sed left join `tabStock Entry` se on sed.parent = se.name
					where se.docstatus=1 and sed.manufacturing_operation = '{prev_mfg_operation}' and
					sed.t_warehouse = '{department_wh}' and (sed.employee is not NULL or sed.subcontractor is not NULL)
					and sed.to_department = '{doc.department}' group by se.name order by se.posting_date""",
				as_dict=1,
				pluck=1,
			)
	item = None
	metal_item = None
	if doc.main_slip:
		item = get_main_slip_item(doc.main_slip)

	existing_items = frappe.get_all(
		"Stock Entry Detail", {"parent": ["in", stock_entries]}, pluck="item_code"
	)

	condition = ""
	if stock_entries:
		condition += "and se.name not in" + "(" + ", ".join(f"'{se}'" for se in stock_entries) + ")"

	non_automated_entries = []
	if doc.type == "Receive":
		non_automated_entries = frappe.db.sql(
			f"""select se.name from `tabStock Entry Detail` sed left join `tabStock Entry` se on sed.parent = se.name
					where se.docstatus=1 and sed.manufacturing_operation = '{row.manufacturing_operation}' and
					sed.t_warehouse = '{employee_wh}' {condition}
					and sed.to_department = '{doc.department}' group by se.name order by se.creation""",
			as_dict=1,
			pluck=1,
		)
	if difference_wt != 0:
		mwo = frappe.db.get_value(
			"Manufacturing Work Order",
			row.manufacturing_work_order,
			["metal_type", "metal_touch", "metal_purity", "metal_colour"],
			as_dict=1,
		)
		metal_item = get_item_from_attribute(
			mwo.metal_type, mwo.metal_touch, mwo.metal_purity, mwo.metal_colour
		)
		if doc.is_main_slip_required == 0:
			manual_loss_items = [
				{
					"item_code": loss_item.item_code,
					"loss_qty": loss_item.proportionally_loss,
					"batch_no": loss_item.batch_no,
					"inventory_type": loss_item.inventory_type,
					"customer": loss_item.customer,
					"pcs": loss_item.pcs,
					"manufacturing_work_order": loss_item.manufacturing_work_order,
				}
				for loss_item in doc.manually_book_loss_details
				if loss_item.manufacturing_work_order == row.manufacturing_work_order
			]

			if (metal_item not in existing_items) and difference_wt < 0:

				if not manual_loss_items:
					frappe.throw(_(f"Please Book Loss in <b>Manually Book Loss Details</b> for Row:{row.idx}"))
				else:
					if (difference_wt * -1) == sum([row.get("loss_qty") for row in manual_loss_items]):
						pass
						# frappe.msgprint("Metal loss booked against MOP")
					else:
						frappe.throw(
							f"Total Loss found: {sum([row.get('loss_qty') for row in manual_loss_items])} Please book Extra loss against MOP to continue"
						)

			elif (metal_item in existing_items) and difference_wt < 0:
				proprtionate_loss_items = [
					{
						"item_code": loss_item.item_code,
						"loss_qty": loss_item.proportionally_loss,
						"batch_no": loss_item.batch_no,
						"inventory_type": loss_item.inventory_type,
						"customer": loss_item.customer,
						"pcs": loss_item.pcs,
						"manufacturing_work_order": loss_item.manufacturing_work_order,
					}
					for loss_item in doc.employee_loss_details
					if loss_item.manufacturing_work_order == row.manufacturing_work_order
				]
				repack_items = manual_loss_items + proprtionate_loss_items

				if (difference_wt * -1) == sum([row.get("loss_qty") for row in manual_loss_items]):
					frappe.msgprint("Metal loss booked against Loss Details Table")
				else:
					process_loss_entry(doc, row, repack_items, employee_wh, department_wh)

		else:
			se_doc = frappe.new_doc("Stock Entry")
			se_doc.stock_entry_type = "Material Transfer (Main Slip)"
			se_doc.purpose = "Material Transfer"
			se_doc.manufacturing_order = frappe.db.get_value(
				"Manufacturing Work Order", row.manufacturing_work_order, "manufacturing_order"
			)
			se_doc.manufacturing_work_order = row.manufacturing_work_order
			se_doc.manufacturing_operation = row.manufacturing_operation
			se_doc.department = doc.department
			se_doc.to_department = doc.department
			se_doc.main_slip = doc.main_slip
			se_doc.employee = doc.employee
			se_doc.subcontractor = doc.subcontractor
			se_doc.inventory_type = "Regular Stock"
			se_doc.auto_created = True
			se_doc.employee_ir = doc.name
			se_doc.branch = "GE-BR-00001"
			warehouse = frappe.db.get_value("Main Slip", doc.main_slip, "raw_material_warehouse")

			ms_batch = frappe.db.sql(
				f"""
							SELECT batch_no, qty, name, consume_qty
							FROM `tabMain Slip SE Details`
							WHERE parent = '{doc.main_slip}'
							and auto_created = 0 and qty != consume_qty and item_code = '{metal_item}'
				""",
				as_dict=1,
			)

			ms_transfer_data = {}
			if ms_batch:
				remaining_wt = difference_wt
				for b_id in ms_batch:
					if abs(remaining_wt) > 0:
						if b_id.qty >= remaining_wt:
							se_qty = remaining_wt
						else:
							se_qty = b_id.qty
						remaining_wt -= b_id.qty

						se_doc.append(
							"items",
							{
								"item_code": metal_item,
								"s_warehouse": warehouse if difference_wt > 0 else employee_wh,
								"t_warehouse": warehouse if difference_wt < 0 else employee_wh,
								"to_employee": None,
								"employee": doc.employee,
								"to_subcontractor": None,
								"use_serial_batch_fields": True,
								"serial_and_batch_bundle": None,
								"subcontractor": doc.subcontractor,
								"to_main_slip": None,
								"main_slip": doc.main_slip,
								"qty": se_qty,
								"manufacturing_operation": row.manufacturing_operation,
								"department": doc.department,
								"to_department": doc.department,
								"manufacturer": doc.manufacturer,
								"material_request": None,
								"material_request_item": None,
								"batch_no": b_id.batch_no,
								"use_serial_batch_fields": 1,
								"inventory_type": "Regular Stock",
							},
						)
						ms_transfer_data.update({(b_id.batch_no, b_id.name): se_qty})
						frappe.db.set_value(
							"Main Slip SE Details", b_id.name, "consume_qty", se_qty + b_id.consume_qty
						)

			if not warehouse:
				frappe.throw("Please set Raw material warehouse for employee")
			se_doc.save()
			se_doc.submit()

	loss = {}
	if doc.type == "Receive":
		loss = get_loss_details(row.manufacturing_operation)
	if loss.get("total_loss"):
		difference_wt = flt(difference_wt + loss.get("total_loss", 0), 3)
	if True:
		row.db_set("gold_loss", difference_wt)

	rejected_qty = {}
	i = 0
	for stock_entry in stock_entries + manual_se_entries + non_automated_entries:
		to_remove = []
		existing_doc = frappe.get_doc("Stock Entry", stock_entry)
		se_doc = frappe.copy_doc(existing_doc)
		se_doc.to_warehouse = ""
		se_doc.outgoing_stock_entry = ""
		se_doc.branch = "GE-BR-00001"
		for child in se_doc.items:
			if not rejected_qty.get(child.item_code):
				temp_qty = (
					frappe.db.get_value(
						"Stock Entry Detail",
						{
							"docstatus": 1,
							"s_warehouse": child.t_warehouse,
							"manufacturing_operation": child.manufacturing_operation,
							"batch_no": child.batch_no,
						},
						"sum(qty) as qty",
					)
					or 0
				)
				rejected_qty[child.item_code] = temp_qty

			if child.item_code in loss.keys():
				loss_qty = loss[child.item_code].get("qty", 0)

				if child.qty == loss_qty or child.qty < loss_qty:
					loss[child.item_code]["qty"] = loss_qty - child.qty
					se_doc.remove(child)
					continue
				else:
					child.qty = child.qty - loss_qty
					loss[child.item_code]["qty"] = 0

			if doc.type == "Issue":
				se_doc.stock_entry_type = (
					"Material Transfer to Subcontractor"
					if doc.subcontracting == "Yes"
					else "Material Transfer to Employee"
				)
				child.s_warehouse = department_wh
				child.t_warehouse = employee_wh
				if doc.subcontracting == "Yes":
					child.to_subcontractor = doc.subcontractor
					child.subcontractor = None
				else:
					child.to_employee = doc.employee
					child.employee = None
				child.department_operation = doc.operation
				child.main_slip = None
				child.to_main_slip = doc.main_slip if item == child.item_code else None
			else:
				se_doc.stock_entry_type = "Material Transfer to Department"
				child.s_warehouse = employee_wh
				child.t_warehouse = department_wh
				if doc.subcontracting == "Yes":
					child.to_subcontractor = None
					child.subcontractor = doc.subcontractor
				else:
					child.to_employee = None
					child.employee = doc.employee
				child.to_main_slip = None
				child.main_slip = doc.main_slip if item == child.item_code else None
			if row.gross_wt > 0:
				actual_loss_qty = flt((child.qty * difference_wt) / row.gross_wt, 3)
			else:
				actual_loss_qty = 0

			child.qty = child.qty + (
				actual_loss_qty if (metal_item == child.item_code) and difference_wt < 0 else 0
			)
			child.use_serial_batch_fields = True
			child.serial_and_batch_bundle = None

			if rejected_qty[child.item_code] > 0:
				if rejected_qty[child.item_code] < child.qty:
					child.qty -= rejected_qty[child.item_code]
					rejected_qty[child.item_code] = 0
				else:
					to_remove.append(child)
					rejected_qty[child.item_code] -= child.qty
			if child.qty < 0:
				frappe.throw("Qty cannot be negative")
			child.manufacturing_operation = row.manufacturing_operation
			child.department = doc.department
			child.to_department = doc.department
			child.manufacturer = doc.manufacturer
			child.material_request = None
			child.material_request_item = None
			if (metal_item == child.item_code) and difference_wt < 0:
				update_existing(
					"Manufacturing Operation",
					row.manufacturing_operation,
					{"gross_wt": f"gross_wt + {difference_wt}", "net_wt": f"net_wt + {difference_wt}"},
				)
		se_doc.department = doc.department
		se_doc.to_department = doc.department
		se_doc.to_employee = doc.employee if doc.type == "Issue" else None
		se_doc.employee = doc.employee if doc.type == "Receive" else None
		se_doc.to_subcontractor = doc.subcontractor if doc.type == "Issue" else None
		se_doc.subcontractor = doc.subcontractor if doc.type == "Receive" else None
		se_doc.auto_created = True
		se_doc.employee_ir = doc.name

		se_doc.manufacturing_operation = row.manufacturing_operation
		for entry in to_remove:
			se_doc.remove(entry)
		i += 1
		if not se_doc.items:
			continue
		se_doc.save()
		se_doc.submit()

	# if difference_wt != 0:
	if difference_wt > 0:
		if not doc.main_slip:
			frappe.throw(_("Cannot add weight without Main Slip"))
		if doc.subcontracting == "Yes":
			convert_pure_metal(
				row.manufacturing_work_order,
				doc.main_slip,
				abs(difference_wt),
				employee_wh,
				employee_wh,
				reverse=(difference_wt < 0),
			)
		se_doc = frappe.new_doc("Stock Entry")
		se_doc.stock_entry_type = "Material Transfer to Department"
		se_doc.purpose = "Material Transfer"
		se_doc.manufacturing_order = frappe.db.get_value(
			"Manufacturing Work Order", row.manufacturing_work_order, "manufacturing_order"
		)
		se_doc.manufacturing_work_order = row.manufacturing_work_order
		se_doc.manufacturing_operation = row.manufacturing_operation
		se_doc.department = doc.department
		se_doc.to_department = doc.department
		se_doc.main_slip = doc.main_slip
		se_doc.employee = doc.employee
		se_doc.subcontractor = doc.subcontractor
		se_doc.inventory_type = "Regular Stock"
		se_doc.auto_created = True
		se_doc.add_to_transit = 0
		se_doc.employee_ir = doc.name
		for ms in ms_transfer_data:
			se_doc.append(
				"items",
				{
					"item_code": metal_item,
					"s_warehouse": employee_wh,
					"t_warehouse": department_wh,
					"to_employee": None,
					"employee": doc.employee,
					"use_serial_batch_fields": True,
					"serial_and_batch_bundle": None,
					"to_subcontractor": None,
					"subcontractor": doc.subcontractor,
					"to_main_slip": None,
					"main_slip": doc.main_slip,
					"qty": ms_transfer_data[ms],
					"manufacturing_operation": row.manufacturing_operation,
					"department": doc.department,
					"to_department": doc.department,
					"manufacturer": doc.manufacturer,
					"material_request": None,
					"material_request_item": None,
					"batch_no": ms[0],
					"use_serial_batch_fields": 1,
					"inventory_type": "Regular Stock",
				},
			)
		se_doc.save()
		se_doc.submit()


def convert_pure_metal(mwo, ms, qty, s_warehouse, t_warehouse, reverse=False):
	from jewellery_erpnext.jewellery_erpnext.doc_events.stock_entry import convert_metal_purity

	# source is ms(main slip) and passed qty is difference qty b/w issue and received gross wt i.e. mwo.qty
	mwo = frappe.db.get_value(
		"Manufacturing Work Order",
		mwo,
		["metal_type", "metal_touch", "metal_purity", "metal_colour"],
		as_dict=1,
	)
	ms = frappe.db.get_value(
		"Main Slip", ms, ["metal_type", "metal_touch", "metal_purity", "metal_colour"], as_dict=1
	)
	mwo.qty = qty
	if reverse:
		ms.qty = qty * flt(mwo.get("metal_purity")) / 100
		convert_metal_purity(mwo, ms, s_warehouse, t_warehouse)
	else:
		ms.qty = qty * flt(mwo.get("metal_purity")) / 100
		convert_metal_purity(ms, mwo, s_warehouse, t_warehouse)


def create_qc_record(row, operation, employee_ir):
	item = frappe.db.get_value("Manufacturing Operation", row.manufacturing_operation, "item_code")
	category = frappe.db.get_value("Item", item, "item_category")
	template_based_on_cat = frappe.db.get_all(
		"Category MultiSelect", {"category": category}, pluck="parent"
	)
	templates = frappe.db.get_all(
		"Operation MultiSelect",
		{
			"operation": operation,
			"parent": ["in", template_based_on_cat],
			"parenttype": "Quality Inspection Template",
		},
		pluck="parent",
	)
	if not templates:
		frappe.msgprint(
			f"No Templates found for given category and operation i.e. {category} and {operation}"
		)
	for template in templates:
		if frappe.db.sql(
			f"""select name from `tabQC` where manufacturing_operation = '{row.manufacturing_operation}' and
					quality_inspection_template = '{template}' and ((docstatus = 1 and status in ('Accepted', 'Force Approved')) or docstatus = 0)"""
		):
			continue
		doc = frappe.new_doc("QC")
		doc.manufacturing_work_order = row.manufacturing_work_order
		doc.manufacturing_operation = row.manufacturing_operation
		doc.received_gross_wt = row.received_gross_wt
		doc.employee_ir = employee_ir
		doc.quality_inspection_template = template
		doc.posting_date = frappe.utils.getdate()
		doc.save(ignore_permissions=True)


@frappe.whitelist()
def book_metal_loss(doc_name, mwo, opt, gwt, r_gwt):
	doc = frappe.get_doc("Employee IR", doc_name)
	mnf_opt = frappe.get_doc("Manufacturing Operation", opt)
	# To get allowed loss percentage value
	allowed_loss_percentage = frappe.get_value(
		"Department Operation",
		{"company": doc.company, "department": doc.department},
		"allowed_loss_percentage",
	)
	# To Check Tollarance which book a loss down side.
	if allowed_loss_percentage:
		cal = round(flt((100 - allowed_loss_percentage) / 100) * flt(gwt), 2)
		if flt(r_gwt) < cal:
			frappe.throw(
				f"Department Operation Standard Process Loss Percentage set by <b>{allowed_loss_percentage}%. </br> Not allowed to book a loss less than {cal}</b>"
			)

	# Fetching Stock Entry based on MNF Work Order
	if gwt != r_gwt:
		mop_balance_table = []
		for row in mnf_opt.mop_balance_table:
			mop_balance_table.append(row.__dict__)
		# Declaration & fetch required value
		data = []  # for final data list
		metal_item = []  # for check metal or not list
		unique = set()  # for Unique Item_Code
		sum_qty = {}  # for sum of qty matched item

		# getting Metal property from MNF Work Order
		mwo_metal_property = frappe.db.get_value(
			"Manufacturing Work Order", mwo, ["metal_type", "metal_touch", "metal_purity"], as_dict=1
		)
		# To Check and pass thgrow Each ITEM metal or not function
		metal_item.append(
			get_item_from_attribute_full(
				mwo_metal_property.metal_type, mwo_metal_property.metal_touch, mwo_metal_property.metal_purity
			)
		)
		# To get Final Metal Item
		flat_metal_item = [item for sublist in metal_item for super_sub in sublist for item in super_sub]

		# To prepare Final Data with all condition's

		for child in mop_balance_table:
			if child["item_code"] in flat_metal_item:
				key = (child["item_code"], child["batch_no"], child["qty"])
				if key not in unique:
					unique.add(key)
					if child["item_code"] in sum_qty:
						sum_qty[child["item_code"], child["batch_no"]]["qty"] += child["qty"]
					else:
						sum_qty[child["item_code"], child["batch_no"]] = {
							"item_code": child["item_code"],
							"qty": child["qty"],
							"stock_uom": child["uom"],
							"batch_no": child["batch_no"],
							"manufacturing_work_order": mnf_opt.manufacturing_work_order,
							"manufacturing_operation": child["parent"],
							"pcs": child["pcs"],
							"customer": child["customer"],
							"inventory_type": child["inventory_type"],
							"sub_setting_type": child["sub_setting_type"],
							"proportionally_loss": 0.0,
							"received_gross_weight": 0.0,
						}
		data = list(sum_qty.values())
		# frappe.throw(f"{sum_qty}{data}")
		mnf_opt_loss_total_qty = 0
		if mnf_opt.loss_details:
			for entry in mnf_opt.loss_details:
				if entry.stock_uom == "Carat":
					mnf_opt_loss_total_qty += entry.stock_qty * 0.2
				else:
					mnf_opt_loss_total_qty += entry.stock_qty
		# -------------------------------------------------------------------------
		# Prepare data and calculation proportionally devide each row based on each qty.
		loss = flt(gwt) - flt(r_gwt)
		ms_consum = 0
		ms_consum_book = 0
		stock_loss = 0
		total_qty = 0
		if loss < 0:
			ms_consum = abs(round(loss, 2))
		if mnf_opt_loss_total_qty != 0:
			loss = flt(loss - mnf_opt_loss_total_qty)
		for entry in data:
			total_qty += entry["qty"]
		for entry in data:
			if total_qty != 0:
				stock_loss = (loss * entry["qty"]) / total_qty
				if stock_loss > 0:
					entry["received_gross_weight"] = entry["qty"] - stock_loss
					entry["proportionally_loss"] = stock_loss
					entry["main_slip_consumption"] = 0
				else:
					ms_consum_book = round((ms_consum * entry["qty"]) / total_qty, 4)
					entry["proportionally_loss"] = 0
					entry["received_gross_weight"] = 0
					entry["main_slip_consumption"] = ms_consum_book
		# -------------------------------------------------------------------------
		return (
			data,
			mnf_opt_loss_total_qty,
		)  # Return Final pripared final Data and total of mnf operation loss table


# timer code
def add_time_log(doc, args):

	last_row = []
	employees = args["employee"]

	# if isinstance(employees, str):
	# 	employees = json.loads(employees)
	if doc.time_logs and len(doc.time_logs) > 0:
		last_row = doc.time_logs[-1]

	doc.reset_timer_value(args)
	if last_row and args.get("complete_time"):
		for row in doc.time_logs:
			if not row.to_time:
				row.update(
					{
						"to_time": get_datetime(args.get("complete_time")),
					}
				)
	elif args.get("start_time"):
		new_args = frappe._dict(
			{
				"from_time": get_datetime(args.get("start_time")),
			}
		)

		if employees:
			new_args.employee = employees
			doc.add_start_time_log(new_args)
		else:
			doc.add_start_time_log(new_args)

	if not doc.employee and employees:
		doc.set_employees(employees)

	# if self.status == "On Hold":
	# 	self.current_time = time_diff_in_seconds(last_row.to_time, last_row.from_time)

	if doc.status == "QC Pending":
		# and self.status == "On Hold":
		doc.current_time = time_diff_in_seconds(last_row.to_time, last_row.from_time)

	elif doc.status == "On Hold":
		doc.current_time = time_diff_in_seconds(last_row.to_time, last_row.from_time)

	# else:
	# 	# a = self
	# 	print(doc)

	doc.save()


def process_loss_entry(doc, row, repack_items, employee_wh, department_wh):
	se_doc = frappe.new_doc("Stock Entry")
	se_doc.stock_entry_type = "Process Loss"
	se_doc.purpose = "Repack"
	se_doc.manufacturing_order = frappe.db.get_value(
		"Manufacturing Work Order", row.get("manufacturing_work_order"), "manufacturing_order"
	)
	se_doc.manufacturing_work_order = row.get("manufacturing_work_order")
	se_doc.manufacturing_operation = row.manufacturing_operation
	se_doc.department = doc.department
	se_doc.to_department = doc.department
	se_doc.employee = doc.employee
	se_doc.subcontractor = doc.subcontractor
	se_doc.inventory_type = ("Regular Stock",)
	se_doc.auto_created = True
	se_doc.employee_ir = doc.name

	if not department_wh:
		frappe.throw("Please set Refining Warehouse in  Manufacturing Settings")
	for loss_item in repack_items:
		if loss_item.get("item_code")[0] == "M":
			dust_item = frappe.db.get_value("Manufacturing Setting", doc.company, "gold_dust")
		elif loss_item.get("item_code")[0] == "D":
			dust_item = frappe.db.get_value("Manufacturing Setting", doc.company, "diamond_dust")
		elif loss_item.get("item_code")[0] == "G":
			dust_item = frappe.db.get_value("Manufacturing Setting", doc.company, "gemstone_dust")
		else:
			dust_item = frappe.db.get_value("Manufacturing Setting", doc.company, "metal_dust")

		se_doc.append(
			"items",
			{
				"item_code": loss_item.get("item_code"),
				"s_warehouse": employee_wh,
				# "t_warehouse": warehouse,
				"to_employee": None,
				"employee": doc.employee,
				"to_subcontractor": None,
				"use_serial_batch_fields": True,
				"serial_and_batch_bundle": None,
				"subcontractor": doc.subcontractor,
				"to_main_slip": None,
				# "main_slip": doc.main_slip,
				"qty": abs(loss_item.get("loss_qty")),
				"manufacturing_operation": loss_item.get("manufacturing_operation"),
				"department": doc.department,
				"to_department": doc.department,
				"manufacturer": doc.manufacturer,
				"material_request": None,
				"material_request_item": None,
				"inventory_type": loss_item.get("inventory_type"),
			},
		)
		se_doc.append(
			"items",
			{
				"item_code": dust_item,
				# "s_warehouse":employee_wh,
				"t_warehouse": department_wh,
				"to_employee": None,
				"employee": doc.employee,
				"to_subcontractor": None,
				"use_serial_batch_fields": True,
				"serial_and_batch_bundle": None,
				"subcontractor": doc.subcontractor,
				"to_main_slip": None,
				# "main_slip": doc.main_slip,
				"qty": abs(loss_item.get("loss_qty")),
				"manufacturing_operation": loss_item.get("manufacturing_operation"),
				"department": doc.department,
				"to_department": doc.department,
				"manufacturer": doc.manufacturer,
				"material_request": None,
				"material_request_item": None,
				"inventory_type": loss_item.get("inventory_type"),
			},
		)
	se_doc.save()
	# se_doc.submit()

	# mop_doc = frappe.get_doc("Manufacturing Operation", row.manufacturing_operation)
	# mop_doc.append(
	# 	"loss_details",
	# 	{"item_code": line_item.item_code, "stock_qty": line_item.proportionally_loss},
	# )
	# mop_doc.save()
	# frappe.msgprint("Extra Metal loss booked against MOP")
