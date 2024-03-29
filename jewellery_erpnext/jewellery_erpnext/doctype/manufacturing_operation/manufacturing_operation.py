# Copyright (c) 2023, Nirali and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname
from frappe.query_builder import Criterion
from frappe.utils import (
	flt,
	get_datetime,
	get_timedelta,
	now,
	time_diff,
	time_diff_in_hours,
	time_diff_in_seconds,
)

from jewellery_erpnext.utils import set_values_in_bulk, update_existing


class OperationSequenceError(frappe.ValidationError):
	pass


class OverlapError(frappe.ValidationError):
	pass


class ManufacturingOperation(Document):
	# timer code
	def reset_timer_value(self, args):
		self.started_time = None

		if args.get("status") in ["WIP", "Finished"]:
			self.current_time = 0.0

			if args.get("status") == "WIP":
				self.started_time = get_datetime(args.get("start_time"))

		if args.get("status") == "QC Pending":
			self.started_time = get_datetime(args.get("start_time"))

		if args.get("status") == "Resume Job":
			args["status"] = "WIP"

		if args.get("status"):
			self.status = args.get("status")

	# timer code
	def add_start_time_log(self, args):
		self.append("time_logs", args)

	# timer code
	def add_time_log(self, args):
		last_row = []
		employees = args.employees
		# if isinstance(employees, str):
		# 	employees = json.loads(employees)
		if self.time_logs and len(self.time_logs) > 0:
			last_row = self.time_logs[-1]

		self.reset_timer_value(args)
		if last_row and args.get("complete_time"):
			for row in self.time_logs:
				if not row.to_time:
					row.update(
						{
							"to_time": get_datetime(args.get("complete_time")),
							# "operation": args.get("sub_operation")
							# "completed_qty": args.get("completed_qty") or 0.0,
						}
					)
		elif args.get("start_time"):
			new_args = frappe._dict(
				{
					"from_time": get_datetime(args.get("start_time")),
					# "operation": args.get("sub_operation"),
					# "completed_qty": 0.0,
				}
			)

			if employees:
				# for name in employees:
				new_args.employee = employees
				self.add_start_time_log(new_args)
			else:
				self.add_start_time_log(new_args)

		if not self.employee and employees:
			self.set_employees(employees)

		# if self.status == "On Hold":
		# 	self.current_time = time_diff_in_seconds(last_row.to_time, last_row.from_time)

		if self.status == "QC Pending":
			# and self.status == "On Hold":
			self.current_time = time_diff_in_seconds(last_row.to_time, last_row.from_time)

		elif self.status == "On Hold":
			self.current_time = time_diff_in_seconds(last_row.to_time, last_row.from_time)

		self.save()

	# timer code
	def set_employees(self, employees):
		# for name in employees:
		# self.append("employee", {"employee": employees})
		self.employee = employees

		# , "completed_qty": 0.0

	# def validate_sequence_id(self):
	# 	# if self.is_corrective_job_card:
	# 	# 	return

	# 	# if not (self.work_order and self.sequence_id):
	# 	# 	return

	# 	# current_operation_qty = 0.0
	# 	# data = self.get_current_operation_data()
	# 	# if data and len(data) > 0:
	# 	# 	current_operation_qty = flt(data[0].completed_qty)

	# 	# current_operation_qty += flt(self.total_completed_qty)

	# 	data = frappe.get_all(
	# 		"Work Order Operation",
	# 		fields=["operation", "status", "completed_qty", "sequence_id"],
	# 		filters={"docstatus": 1, "parent": self.work_order, "sequence_id": ("<", self.sequence_id)},
	# 		order_by="sequence_id, idx",
	# 	)

	# 	message = "Job Card {0}: As per the sequence of the operations in the work order {1}".format(
	# 		bold(self.name), bold(get_link_to_form("Work Order", self.work_order))
	# 	)

	# 	for row in data:
	# 		if row.status != "Completed" and row.completed_qty < current_operation_qty:
	# 			frappe.throw(
	# 				_("{0}, complete the operation {1} before the operation {2}.").format(
	# 					message, bold(row.operation), bold(self.operation)
	# 				),
	# 				OperationSequenceError,
	# 			)

	# 		if row.completed_qty < current_operation_qty:
	# 			msg = f"""The completed quantity {bold(current_operation_qty)}
	# 				of an operation {bold(self.operation)} cannot be greater
	# 				than the completed quantity {bold(row.completed_qty)}
	# 				of a previous operation
	# 				{bold(row.operation)}.
	# 			"""

	# 			frappe.throw(_(msg))

	def validate(self):
		self.set_start_finish_time()
		self.validate_time_logs()
		self.update_weights()
		self.validate_loss()
		self.get_previous_se_details()
		self.set_mop_balance_table()  # To Set MOP Bailance Table on update source & target Table.

	def on_update(self):
		self.attach_cad_cam_file_into_item_master()  # To set MOP doctype CAD-CAM Attachment's & respective details into Item Master.
		self.set_wop_weight_details()  # To Set WOP doctype Weight details from MOP Doctype.
		self.set_pmo_weight_details()  # To Set PMO doctype Weight details from MOP Doctype.

	def get_previous_se_details(self):
		d_warehouse = None
		e_warehouse = None
		if self.department:
			d_warehouse = frappe.db.get_value(
				"Warehouse", {"department": self.department, "warehouse_type": "Manufacturing"}
			)
		mop_employee = self.employee
		if mop_employee:
			e_warehouse = frappe.db.get_value(
				"Warehouse", {"employee": mop_employee, "warehouse_type": "Manufacturing"}
			)

		if self.previous_mop:
			se_data = frappe.db.get_all("Stock Entry", {"manufacturing_operation": self.previous_mop})

			existing_data = {
				"department_source_table": [],
				"department_target_table": [],
				"employee_source_table": [],
				"employee_target_table": [],
			}

			for row in existing_data:
				for entry in self.get(row):
					if entry.get("sed_item") and entry.get("sed_item") not in existing_data[row]:
						existing_data[row].append(entry.get("sed_item"))

			if se_data:
				se_list = [row.name for row in se_data]
				department_source_table = frappe.db.get_all(
					"Stock Entry Detail", {"parent": ["in", se_list], "s_warehouse": d_warehouse}, ["*"]
				)
				department_target_table = frappe.db.get_all(
					"Stock Entry Detail", {"parent": ["in", se_list], "t_warehouse": d_warehouse}, ["*"]
				)
				employee_source_table = frappe.db.get_all(
					"Stock Entry Detail", {"parent": ["in", se_list], "s_warehouse": e_warehouse}, ["*"]
				)
				employee_target_table = frappe.db.get_all(
					"Stock Entry Detail", {"parent": ["in", se_list], "t_warehouse": e_warehouse}, ["*"]
				)

				for row in department_source_table:
					row["sed_item"] = row["name"]
					if row["sed_item"] not in existing_data["department_source_table"]:
						row["name"] = None
						row["idx"] = None
						self.append("department_source_table", row)

				for row in department_target_table:
					row["sed_item"] = row["name"]
					if row["sed_item"] not in existing_data["department_target_table"]:
						row["name"] = None
						row["idx"] = None
						self.append("department_target_table", row)

				for row in employee_source_table:
					row["sed_item"] = row["name"]
					if row["sed_item"] not in existing_data["employee_source_table"]:
						row["name"] = None
						row["idx"] = None
						self.append("employee_source_table", row)

				for row in employee_target_table:
					row["sed_item"] = row["name"]
					if row["sed_item"] not in existing_data["employee_target_table"]:
						row["name"] = None
						row["idx"] = None
						self.append("employee_target_table", row)

	# timer code
	def validate_time_logs(self):
		self.total_minutes = 0.0
		# self.total_completed_qty = 0.0

		if self.get("time_logs"):
			# d = self.get("time_logs")[-1]
			# print(self)
			for d in self.get("time_logs")[-1:]:
				# print(d)
				if (
					d.to_time
					and get_datetime(d.from_time) > get_datetime(d.to_time)
					and get_datetime(d.from_time) < get_datetime(d.to_time)
				):
					frappe.throw(_("Row {0}: From time must be less than to time").format(d.idx))

				# data = self.get_overlap_for(d)
				# if data:
				# 	frappe.throw(
				# 		_("Row {0}: From Time and To Time of {1} is overlapping with {2}").format(
				# 			d.idx, self.name, data.name
				# 		),
				# 		OverlapError,
				# 	)

				if d.from_time and d.to_time:
					d.time_in_mins = time_diff_in_hours(d.to_time, d.from_time) * 60
					in_hours = time_diff(d.to_time, d.from_time)
					d.time_in_hour = str(in_hours)[:-3]
					for i in self.get("time_logs"):

						self.total_minutes += i.time_in_mins

					default_shift = frappe.db.get_value("Employee", d.employee, "default_shift")
					if default_shift:
						shift_hours = frappe.db.get_value("Shift Type", default_shift, ["start_time", "end_time"])
						total_shift_hours = time_diff(shift_hours[1], shift_hours[0])

						if in_hours >= total_shift_hours:
							d.time_in_days = in_hours / total_shift_hours

			# frappe.throw('HOLD')

			# if d.completed_qty and not self.sub_operations:
			# 	self.total_completed_qty += d.completed_qty

			# self.total_completed_qty = flt(self.total_completed_qty, self.precision("total_completed_qty"))

		# for row in self.sub_operations:
		# 	self.total_completed_qty += row.completed_qty

	# timer code
	def update_corrective_in_work_order(self, wo):
		wo.corrective_operation_cost = 0.0
		for row in frappe.get_all(
			"Job Card",
			fields=["total_time_in_mins", "hour_rate"],
			filters={"is_corrective_job_card": 1, "docstatus": 1, "work_order": self.work_order},
		):
			wo.corrective_operation_cost += flt(row.total_time_in_mins) * flt(row.hour_rate)

		wo.calculate_operating_cost()
		wo.flags.ignore_validate_update_after_submit = True
		wo.save()

	# timer code
	def get_current_operation_data(self):
		return frappe.get_all(
			"Job Card",
			fields=[
				"sum(total_time_in_mins) as time_in_mins",
				"sum(total_completed_qty) as completed_qty",
				"sum(process_loss_qty) as process_loss_qty",
			],
			filters={
				"docstatus": 1,
				"work_order": self.work_order,
				"operation_id": self.operation_id,
				"is_corrective_job_card": 0,
			},
		)

	# timer code
	def get_overlap_for(self, args, check_next_available_slot=False):
		production_capacity = 1

		jc = frappe.qb.DocType("Manufacturing Operation")
		# jctl = frappe.qb.DocType("Job Card Time Log")
		jctl = frappe.qb.DocType("Manufacturing Operation Time Log")

		time_conditions = [
			((jctl.from_time < args.from_time) & (jctl.to_time > args.from_time)),
			((jctl.from_time < args.to_time) & (jctl.to_time > args.to_time)),
			((jctl.from_time >= args.from_time) & (jctl.to_time <= args.to_time)),
		]

		if check_next_available_slot:
			time_conditions.append(((jctl.from_time >= args.from_time) & (jctl.to_time >= args.to_time)))

		query = (
			frappe.qb.from_(jctl)
			.from_(jc)
			.select(jc.name.as_("name"), jctl.from_time, jctl.to_time)
			#    , jc.workstation, jc.workstation_type
			.where(
				(jctl.parent == jc.name)
				& (Criterion.any(time_conditions))
				& (jctl.name != f"{args.name or 'No Name'}")
				& (jc.name != f"{args.parent or 'No Name'}")
				& (jc.docstatus < 2)
			)
			.orderby(jctl.to_time, order=frappe.qb.desc)
		)

		# if self.workstation_type:
		# 	query = query.where(jc.workstation_type == self.workstation_type)

		# if self.workstation:
		# 	production_capacity = (
		# 		frappe.get_cached_value("Workstation", self.workstation, "production_capacity") or 1
		# 	)
		# 	query = query.where(jc.workstation == self.workstation)

		if args.get("employee"):
			# override capacity for employee
			production_capacity = 1
			query = query.where(jctl.employee == args.get("employee"))

		existing = query.run(as_dict=True)
		if not self.has_overlap(production_capacity, existing):
			return {}
		if existing and production_capacity > len(existing):
			return

		# if self.workstation_type:
		# 	if workstation := self.get_workstation_based_on_available_slot(existing):
		# 		self.workstation = workstation
		# 		return None

		return existing[0] if existing else None

	def has_overlap(self, production_capacity, time_logs):
		overlap = False
		if production_capacity == 1 and len(time_logs) >= 1:
			return True
		if not len(time_logs):
			return False

		# sorting overlapping job cards as per from_time
		time_logs = sorted(time_logs, key=lambda x: x.get("from_time"))
		# alloted_capacity has key number starting from 1. Key number will increment by 1 if non sequential job card found
		# if key number reaches/crosses to production_capacity means capacity is full and overlap error generated
		# this will store last to_time of sequential job cards
		alloted_capacity = {1: time_logs[0]["to_time"]}
		# flag for sequential Job card found
		sequential_job_card_found = False
		for i in range(1, len(time_logs)):
			# scanning for all Existing keys
			for key in alloted_capacity.keys():
				# if current Job Card from time is greater than last to_time in that key means these job card are sequential
				if alloted_capacity[key] <= time_logs[i]["from_time"]:
					# So update key's value with last to_time
					alloted_capacity[key] = time_logs[i]["to_time"]
					# flag is true as we get sequential Job Card for that key
					sequential_job_card_found = True
					# Immediately break so that job card to time is not added with any other key except this
					break
			# if sequential job card not found above means it is overlapping  so increment key number to alloted_capacity
			if not sequential_job_card_found:
				# increment key number
				key = key + 1
				# for that key last to time is assigned.
				alloted_capacity[key] = time_logs[i]["to_time"]
		if len(alloted_capacity) >= production_capacity:
			# if number of keys greater or equal to production caoacity means full capacity is utilized and we should throw overlap error
			return True
		return overlap

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
				self.start_time = self.time_logs[0].from_time
			elif self.status == "Finished":
				if self.time_logs:
					if not self.start_time:
						self.start_time = self.time_logs[0].from_time
					self.finish_time = self.time_logs[-1].to_time
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
					   			ifnull(sum(if(sed.uom='Carat',sed.qty*0.2, sed.qty)),0) as gross_wt
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
					   			ifnull(sum(if(sed.uom='Carat',sed.qty*0.2, sed.qty)),0) as gross_wt
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
			if row.uom == "Carat":
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

	def set_mop_balance_table(self):
		self.mop_balance_table = []
		added_item_codes = set()
		final_balance_row = []
		bal_qty = {}
		# Calculate sum of quantities for department source table
		for row in self.department_source_table:
			bal_qty[row.item_code] = bal_qty.get(row.item_code, 0) + row.qty
		# Calculate sum of quantities for employee source table
		for row in self.employee_source_table:
			bal_qty[row.item_code] = bal_qty.get(row.item_code, 0) + row.qty
		# Subtract sum of quantities for department target table
		for row in self.department_target_table:
			bal_qty[row.item_code] = bal_qty.get(row.item_code, 0) - row.qty
		# Subtract sum of quantities for employee target table
		for row in self.employee_target_table:
			bal_qty[row.item_code] = bal_qty.get(row.item_code, 0) - row.qty

		for item_code, qty in bal_qty.items():
			if qty != 0:
				row_data = None
				# if row_data is None and not self.employee_target_table:
				if self.department_target_table:
					for row_dtt in self.department_target_table:
						if row_dtt.item_code == item_code:
							row_data = row_dtt.__dict__.copy()
							break
				if self.employee_target_table:
					for row_ett in self.employee_target_table:
						if row_ett.item_code == item_code:
							row_data = row_ett.__dict__.copy()
							break
				if row_data:
					row_data["qty"] = abs(qty)
					row_data["name"] = None
					row_data["idx"] = None
					row_data["parentfield"] = None
					row_data["s_warehouse"] = row_data["t_warehouse"] or row_data["s_warehouse"]
					row_data["t_warehouse"] = None
					final_balance_row.append(row_data)

		# To check Item_code already added or not balance table
		for row_balance in self.mop_balance_table:
			added_item_codes.add(row_balance.item_code)
		# frappe.throw(f"{final_balance_row}")
		# Append Final result into Balance Table
		for row in final_balance_row:
			if row.get("item_code") not in added_item_codes:
				self.append("mop_balance_table", row)


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

	get_item_doc = frappe.get_doc("Item", pmo_det.item_code)
	if get_item_doc.has_serial_no == 0:
		frappe.throw(f"The Item {pmo_det.name} does not have Serial No plese check item master")

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
			"manufacturing_operation": doc.manufacturing_operation,
			"custom_serial_number_creator": doc.name,
			"inventory_type": "Regular Stock",
			"auto_created": 1,
		}
	)
	for entry in row_data:
		se.append(
			"items",
			{
				"item_code": entry["item_code"],
				"qty": entry["qty"],
				"uom": entry["uom"],
				"manufacturing_operation": doc.manufacturing_operation,
				"department": doc.department,
				"inventory_type": "Regular Stock",
				"use_serial_batch_fields": 1,
				"to_department": doc.department,
				"s_warehouse": target_wh,
			},
		)
	sr_no = ""
	compose_series = genrate_serial_no(doc)
	sr_no = make_autoname(compose_series)
	new_bom_serial_no = sr_no
	# serial_no_pass_entry(doc,sr_no,to_wh,pmo_det)
	se.append(
		"items",
		{
			"item_code": pmo_det.item_code,
			"qty": 1,
			"t_warehouse": to_wh,
			"department": doc.department,
			"to_department": doc.department,
			"inventory_type": "Regular Stock",
			"manufacturing_operation": doc.manufacturing_operation,
			"use_serial_batch_fields": 1,
			"serial_no": sr_no,
			"is_finished_item": 1,
		},
	)
	se.save()
	se.submit()
	update_produced_qty(pmo_det)
	frappe.msgprint("Finished Good created successfully")

	if doc.for_fg:
		for row in doc.fg_details:
			for entry in row_data:
				if row.id == entry["id"] and row.row_material == entry["item_code"]:
					row.serial_no = get_serial_no(new_bom_serial_no)

	return new_bom_serial_no


def genrate_serial_no(doc):
	errors = []
	mwo_no = doc.manufacturing_work_order
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


def serial_no_pass_entry(doc, sr_no, to_wh, pmo_det):
	serial_nos_details = []
	serial_nos_details.append(
		(
			sr_no,
			sr_no,
			now(),
			now(),
			frappe.session.user,
			frappe.session.user,
			to_wh,
			doc.company,
			pmo_det.item_code,
			# self.item_name,
			# self.description,
			"Active",
			# self.batch_no,
		)
	)

	if serial_nos_details:
		fields = [
			"name",
			"serial_no",
			"creation",
			"modified",
			"owner",
			"modified_by",
			"warehouse",
			"company",
			"item_code",
			# "item_name",
			# "description",
			"status",
			# "batch_no",
		]

		frappe.db.bulk_insert("Serial No", fields=fields, values=set(serial_nos_details))


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
	wh = frappe.db.get_value(
		"Warehouse", {"department": doc.department, "warehouse_type": "Manufacturing"}, "name"
	)
	if doc.employee:
		wh = frappe.db.get_value(
			"Warehouse", {"employee": doc.employee, "warehouse_type": "Manufacturing"}, "name"
		)
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
		total_loss += row.qty * 0.2 if row.uom == "Carat" else row.qty
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
			filters["warehouse_type"] = "Manufacturing"
	if not filters:
		filters["department"] = doc.department
	t_warehouse = frappe.db.get_value("Warehouse", filters, "name")
	res = frappe.db.sql(
		f"""select ifnull(sum(if(sed.uom='Carat',sed.qty*0.2, sed.qty)),0) as gross_wt, ifnull(sum(if(i.variant_of = 'M',sed.qty,0)),0) as net_wt,
		ifnull(sum(if(i.variant_of = 'D',sed.qty,0)),0) as diamond_wt, ifnull(sum(if(i.variant_of = 'D',if(sed.uom='Carat',sed.qty*0.2, sed.qty),0)),0) as diamond_wt_in_gram,
		ifnull(sum(if(i.variant_of = 'G',sed.qty,0)),0) as gemstone_wt, ifnull(sum(if(i.variant_of = 'G',if(sed.uom='Carat',sed.qty*0.2, sed.qty),0)),0) as gemstone_wt_in_gram,
		ifnull(sum(if(i.variant_of = 'O',sed.qty,0)),0) as other_wt
		from `tabStock Entry Detail` sed left join `tabStock Entry` se on sed.parent = se.name left join `tabItem` i on i.name = sed.item_code
			where sed.t_warehouse = "{t_warehouse}" and sed.manufacturing_operation = "{doc.name}" and se.docstatus = 1""",
		as_dict=1,
	)
	los = frappe.db.sql(
		f"""select ifnull(sum(if(sed.uom='Carat',sed.qty*0.2, sed.qty)),0) as gross_wt, ifnull(sum(if(i.variant_of = 'M',sed.qty,0)),0) as net_wt,
		ifnull(sum(if(i.variant_of = 'D',sed.qty,0)),0) as diamond_wt, ifnull(sum(if(i.variant_of = 'D',if(sed.uom='Carat',sed.qty*0.2, sed.qty),0)),0) as diamond_wt_in_gram,
		ifnull(sum(if(i.variant_of = 'G',sed.qty,0)),0) as gemstone_wt, ifnull(sum(if(i.variant_of = 'G',if(sed.uom='Carat',sed.qty*0.2, sed.qty),0)),0) as gemstone_wt_in_gram,
		ifnull(sum(if(i.variant_of = 'O',sed.qty,0)),0) as other_wt
		from `tabStock Entry Detail` sed left join `tabStock Entry` se on sed.parent = se.name left join `tabItem` i on i.name = sed.item_code
			where sed.s_warehouse = "{t_warehouse}" and sed.manufacturing_operation = "{doc.name}" and se.docstatus = 1""",
		as_dict=1,
	)
	result = {}
	for key in res[0].keys():
		result[key] = res[0][key] - los[0][key]

	if result:
		return result
	return {}


def create_finished_goods_bom(self, se_name, mo_data, total_time=0):
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
	new_bom.total_operation_time = total_time
	# new_bom.items = []
	new_bom.actual_operation_time = 0

	if mo_data:
		new_bom.with_operations = 1
		new_bom.transfer_material_against = None
		for row in mo_data:
			new_bom.actual_operation_time += mo_data[row]["operation_time"]
			new_bom.append(
				"operations",
				{
					"manufacturing_operation": row,
					"workstation": mo_data[row]["workstation"],
					"time_in_mins": mo_data[row]["operation_time"],
					"hour_rate": mo_data[row]["total_expense"],
				},
			)

	new_bom.operation_time_diff = new_bom.total_operation_time - new_bom.actual_operation_time

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
				row["item_code"] = item_row.name
				row["quantity"] = item["qty"]
				row["qty"] = item["qty"]
				row["uom"] = "Gram"
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


# timer code
@frappe.whitelist()
def make_time_log(args):
	if isinstance(args, str):
		args = json.loads(args)
	args = frappe._dict(args)
	doc = frappe.get_doc("Manufacturing Operation", args.job_card_id)
	# doc.validate_sequence_id()
	doc.add_time_log(args)
