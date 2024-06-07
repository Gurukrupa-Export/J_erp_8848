# Copyright (c) 2023, Nirali and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _, scrub
from frappe.model.document import Document
from frappe.model.mapper import get_mapped_doc

from jewellery_erpnext.jewellery_erpnext.doc_events.stock_entry import (
	update_manufacturing_operation,
)
from jewellery_erpnext.jewellery_erpnext.doctype.department_ir.doc_events.department_ir_utils import (
	valid_reparing_or_next_operation,
)
from jewellery_erpnext.jewellery_erpnext.doctype.manufacturing_operation.manufacturing_operation import (
	get_previous_operation,
)
from jewellery_erpnext.utils import get_value, set_values_in_bulk


class DepartmentIR(Document):
	@frappe.whitelist()
	def get_operations(self):
		dir_status = "In-Transit" if self.type == "Receive" else ["not in", ["In-Transit", "Received"]]
		filters = {"department_ir_status": dir_status}
		if self.type == "Issue":
			filters["status"] = ["in", ["Finished", "Revert"]]
			filters["department"] = self.current_department
		records = frappe.get_list("Manufacturing Operation", filters, ["name", "gross_wt"])
		self.department_ir_operation = []
		if records:
			for row in records:
				self.append("department_ir_operation", {"manufacturing_operation": row.name})

	def on_submit(self):
		if self.type == "Issue":
			self.on_submit_issue()
		else:
			self.on_submit_receive()

	def validate(self):
		valid_reparing_or_next_operation(self)
		for row in self.department_ir_operation:
			save_mop(row.manufacturing_operation)
		if (
			self.current_department or self.next_department
		) and self.current_department == self.next_department:
			frappe.throw(_("Current and Next department cannot be same"))
		if self.type == "Receive" and self.receive_against:
			if existing := frappe.db.exists(
				"Department IR",
				{"receive_against": self.receive_against, "name": ["!=", self.name], "docstatus": ["!=", 2]},
			):
				# frappe.throw(_(f"Department IR: {existing} already exist for Issue: {self.receive_against}"))
				frappe.throw(
					_("Department IR: {0} already exists for Issue: {1}").format(existing, self.receive_against)
				)
		for row in self.department_ir_operation:
			existing = frappe.db.sql(
				f"""select di.name from `tabDepartment IR Operation` dip left join `tabDepartment IR` di on dip.parent = di.name
			    					where di.docstatus != 2 and di.type = '{self.type}' and dip.name != '{row.name}' and dip.manufacturing_operation = '{row.manufacturing_operation}'"""
			)
			if existing:
				# frappe.throw(_(f"Row #{row.idx}: Found duplicate entry in Department IR: {existing[0][0]}"))
				frappe.throw(
					_("Row #{0}: Found duplicate entry in Department IR: {1}").format(row.idx, existing[0][0])
				)

	def on_cancel(self):
		if self.type == "Issue":
			self.on_submit_issue(cancel=True)
		else:
			self.on_submit_receive(cancel=True)
		# self.on_submit_receive(cancel=True)

	# for Receive
	def on_submit_receive(self, cancel=False):
		import copy

		values = {}
		values["department_receive_id"] = self.name
		values["department_ir_status"] = "Received"

		se_item_list = []
		in_transit_wh = frappe.db.get_value(
			"Warehouse",
			{"department": self.current_department, "warehouse_type": "Manufacturing"},
			"default_in_transit_warehouse",
		)

		department_wh = frappe.get_value(
			"Warehouse", {"department": self.current_department, "warehouse_type": "Manufacturing"}
		)
		for row in self.department_ir_operation:

			for se_item in frappe.db.get_all(
				"Stock Entry Detail",
				{
					"manufacturing_operation": row.manufacturing_operation,
					"t_warehouse": in_transit_wh,
					"department": self.previous_department,
					"to_department": self.current_department,
					"docstatus": 1,
				},
				["*"],
			):
				temp_row = copy.deepcopy(se_item)
				temp_row["name"] = None
				temp_row["idx"] = None
				temp_row["s_warehouse"] = in_transit_wh
				temp_row["t_warehouse"] = department_wh
				temp_row["serial_and_batch_bundle"] = None
				temp_row["main_slip"] = None
				temp_row["employee"] = None
				temp_row["to_main_slip"] = None
				temp_row["to_employee"] = None
				se_item_list += [temp_row]

			# changes
			values["gross_wt"] = get_value(
				"Stock Entry Detail",
				{
					"manufacturing_operation": row.manufacturing_operation,
					"s_warehouse": in_transit_wh,
					"docstatus": 1,
				},
				'sum(if(uom="Carat",qty*0.2,qty))',
				0,
			)
			res = get_material_wt(self, row.manufacturing_operation)
			values.update(res)
			if cancel:
				values.update({"department_receive_id": None, "department_ir_status": "In-Transit"})
			frappe.db.set_value("Manufacturing Operation", row.manufacturing_operation, values)
			frappe.db.set_value(
				"Manufacturing Work Order", row.manufacturing_work_order, "department", self.current_department
			)

		if not se_item_list:
			frappe.msgprint(_("No Stock Entries were generated during this Department IR"))
			return
		if not cancel:
			stock_doc = frappe.new_doc("Stock Entry")
			stock_doc.update(
				{
					"stock_entry_type": "Material Transfer to Department",
					"company": self.company,
					"department_ir": self.name,
					"auto_created": True,
					"add_to_transit": 0,
					"inventory_type": None,
				}
			)

			for row in se_item_list:
				stock_doc.append("items", row)
			stock_doc.flags.ignore_permissions = True
			stock_doc.save()
			stock_doc.submit()

		if cancel:
			se_list = frappe.db.get_list("Stock Entry", {"department_ir": self.name})
			for row in se_list:
				se_doc = frappe.get_doc("Stock Entry", row.name)
				se_doc.cancel()

			for row in self.department_ir_operation:
				frappe.db.set_value(
					"Manufacturing Operation", row.manufacturing_operation, "status", "Not Started"
				)

	# for Issue
	def on_submit_issue(self, cancel=False):
		# default_department = frappe.db.get_value(
		# 	"Manufacturing Setting", {"company": self.company}, "default_department"
		# )
		mop_data = frappe._dict()
		for row in self.department_ir_operation:
			if cancel:
				new_operation = frappe.db.get_value(
					"Manufacturing Operation",
					{"department_issue_id": self.name, "manufacturing_work_order": row.manufacturing_work_order},
				)
				se_list = frappe.db.get_list("Stock Entry", {"department_ir": self.name})
				for se in se_list:
					se_doc = frappe.get_doc("Stock Entry", se.name)
					if se_doc.docstatus == 1:
						se_doc.cancel()

					frappe.db.set_value(
						"Stock Entry Detail", {"parent": se.name}, "manufacturing_operation", None
					)

				frappe.db.set_value(
					"Manufacturing Work Order",
					row.manufacturing_work_order,
					"manufacturing_operation",
					row.manufacturing_operation,
				)
				if new_operation:
					frappe.db.set_value(
						"Department IR Operation",
						{"docstatus": 2, "manufacturing_operation": new_operation},
						"manufacturing_operation",
						None,
					)
					frappe.db.set_value(
						"Stock Entry Detail",
						{"docstatus": 2, "manufacturing_operation": new_operation},
						"manufacturing_operation",
						None,
					)
					frappe.delete_doc("Manufacturing Operation", new_operation, ignore_permissions=1)
				frappe.db.set_value(
					"Manufacturing Operation", row.manufacturing_operation, "status", "In Transit"
				)

			# else:
			# 	new_operation = create_operation_for_next_dept(
			# 		self.name, row.manufacturing_work_order, row.manufacturing_operation, self.next_department
			# 	)
			# 	fetch_and_update(self, row, new_operation)
			# 	if not frappe.db.get_value('Stock Entry',stock_entry,"employee"):
			# 		se_doc = end_transit(doc, row, manufacturing_operation, stock_entry)
			# 		if se_doc :
			# 			start_transit(doc, row, manufacturing_operation, se_doc)
			# 		else:
			# 			create_stock_entry_for_issue(doc, row, manufacturing_operation)
			# 	else:
			# 		create_stock_entry_for_issue(doc, row, manufacturing_operation)

			# 	frappe.db.set_value(
			# 		"Manufacturing Operation", row.manufacturing_operation, "status", "Finished"
			# 	)
			else:
				new_operation = create_operation_for_next_dept(
					self.name, row.manufacturing_work_order, row.manufacturing_operation, self.next_department
				)
				update_stock_entry_dimensions(self, row, new_operation)
				mop_data.update(
					{
						row.manufacturing_work_order: {
							"cur_mop": row.manufacturing_operation,
							"new_mop": new_operation,
						}
					}
				)
				# create_stock_entry_for_issue(self, row, new_operation)
				frappe.db.set_value(
					"Manufacturing Operation", row.manufacturing_operation, "status", "Finished"
				)

		add_to_transit = []
		strat_transit = []
		if mop_data and not cancel:
			in_transit_wh = frappe.get_value(
				"Warehouse",
				{"department": self.next_department, "warehouse_type": "Manufacturing"},
				"default_in_transit_warehouse",
			)

			department_wh = frappe.get_value(
				"Warehouse", {"department": self.current_department, "warehouse_type": "Manufacturing"}
			)
			if not department_wh:
				frappe.throw(_("Please set warhouse for department {0}").format(self.current_department))

			send_in_transit_wh = frappe.get_value(
				"Warehouse",
				{"department": self.current_department, "warehouse_type": "Manufacturing"},
				"default_in_transit_warehouse",
			)

			for row in mop_data:
				lst1, lst2 = get_se_items(
					self, row, mop_data[row], in_transit_wh, send_in_transit_wh, department_wh
				)
				add_to_transit += lst1
				strat_transit += lst2

			if add_to_transit:
				stock_doc = frappe.new_doc("Stock Entry")
				stock_doc.stock_entry_type = "Material Transfer to Department"
				stock_doc.company = self.company
				stock_doc.department_ir = self.name
				stock_doc.auto_created = True
				stock_doc.add_to_transit = 1
				stock_doc.inventory_type = None

				for row in add_to_transit:
					stock_doc.append("items", row)
				stock_doc.flags.ignore_permissions = True
				stock_doc.save()
				stock_doc.submit()

				stock_doc = frappe.new_doc("Stock Entry")
				stock_doc.stock_entry_type = "Material Transfer to Department"
				stock_doc.company = self.company
				stock_doc.department_ir = self.name
				stock_doc.auto_created = True
				stock_doc.inventory_type = None

				for row in add_to_transit:
					row["t_warehouse"] = department_wh
					row["s_warehouse"] = in_transit_wh
					stock_doc.append("items", row)
				stock_doc.flags.ignore_permissions = True
				stock_doc.save()
				stock_doc.submit()

			if strat_transit:
				stock_doc = frappe.new_doc("Stock Entry")
				stock_doc.stock_entry_type = "Material Transfer to Department"
				stock_doc.company = self.company
				stock_doc.department_ir = self.name
				stock_doc.auto_created = True
				for row in strat_transit:
					stock_doc.append("items", row)
				stock_doc.flags.ignore_permissions = True
				stock_doc.save()
				stock_doc.submit()


def save_mop(mop_name):
	doc = frappe.get_doc("Manufacturing Operation", mop_name)
	doc.save()


def get_se_items(doc, mwo, mop_data, in_transit_wh, send_in_transit_wh, department_wh):
	lst1 = []
	lst2 = []
	import copy

	for row in frappe.db.get_all("MOP Balance Table", {"parent": mop_data["cur_mop"]}, ["*"]):
		temp_row = copy.deepcopy(row)
		temp_row["name"] = None
		temp_row["idx"] = None
		s_warehouse = row.s_warehouse
		temp_row["t_warehouse"] = in_transit_wh
		temp_row["s_warehouse"] = department_wh
		temp_row["manufacturing_operation"] = mop_data["new_mop"]
		temp_row["department"] = doc.current_department
		temp_row["to_department"] = doc.next_department
		temp_row["use_serial_batch_fields"] = True
		temp_row["serial_and_batch_bundle"] = None
		temp_row["main_slip"] = None
		temp_row["to_main_slip"] = None
		temp_row["employee"] = None
		temp_row["to_employee"] = None
		temp_row["custom_manufacturing_work_order"] = mwo

		if s_warehouse == send_in_transit_wh:
			lst1.append(temp_row)
		elif s_warehouse == department_wh:
			lst2.append(temp_row)

	return lst1, lst2


def update_stock_entry_dimensions(doc, row, manufacturing_operation, for_employee=False):
	filters = {}
	if for_employee:
		filters["employee" if doc.type == "Receive" else "to_employee"] = doc.employee
		current_dep = doc.department
		next_dep = doc.department
	else:
		current_dep = doc.current_department
		next_dep = doc.next_department
	filters.update(
		{
			"manufacturing_work_order": row.manufacturing_work_order,
			"docstatus": 1,
			"manufacturing_operation": ["is", "not set"],
			"department": current_dep,
			"to_department": next_dep,
		}
	)
	stock_entries = frappe.get_all("Stock Entry", filters=filters, pluck="name")
	values = {"manufacturing_operation": manufacturing_operation}
	for stock_entry in stock_entries:
		rows = frappe.get_all("Stock Entry Detail", {"parent": stock_entry}, pluck="name")
		set_values_in_bulk("Stock Entry Detail", rows, values)
		values[scrub(doc.doctype)] = doc.name
		frappe.db.set_value("Stock Entry", stock_entry, values)
		update_manufacturing_operation(stock_entry)
		del values[scrub(doc.doctype)]


def create_stock_entry_for_issue(doc, row, manufacturing_operation):

	in_transit_wh = frappe.get_value(
		"Warehouse",
		{"department": doc.next_department, "warehouse_type": "Manufacturing"},
		"default_in_transit_warehouse",
	)

	department_wh = frappe.get_value(
		"Warehouse", {"department": doc.current_department, "warehouse_type": "Manufacturing"}
	)
	if not department_wh:
		# frappe.throw(_(f"Please set warhouse for department {doc.current_department}"))
		frappe.throw(_("Please set warehouse for department {0}").format(doc.current_department))

	send_in_transit_wh = frappe.get_value(
		"Warehouse",
		{"department": doc.current_department, "warehouse_type": "Manufacturing"},
		"default_in_transit_warehouse",
	)

	## make filter to fetch the stock entry created against warehouse and operations
	fetch_manual_stock_entries = frappe.db.sql(
		f""" SELECT se.name FROM `tabStock Entry` se LEFT JOIN `tabStock Entry Detail` sed ON se.name = sed.parent
									 WHERE sed.t_warehouse = '{send_in_transit_wh}'
									 AND sed.manufacturing_operation = '{row.manufacturing_operation}'
									 AND sed.to_department = '{doc.current_department}'
									 AND sed.docstatus = 1
									 AND se.auto_created = 0
									 GROUP BY se.name """,
		pluck="name",
	)

	stock_entries = frappe.db.sql(
		f"""select se.name from `tabStock Entry Detail` sed left join `tabStock Entry` se on sed.parent = se.name
			       where se.auto_created = 1 and se.docstatus=1 and sed.manufacturing_operation = '{row.manufacturing_operation}' and
				   sed.t_warehouse = '{department_wh}'
				   and sed.to_department = '{doc.current_department}' group by se.name order by se.posting_date""",
		as_dict=1,
		pluck=1,
	)

	non_automated_entries = []
	if not stock_entries:
		non_automated_entries = frappe.db.sql(
			f"""select se.name from `tabStock Entry Detail` sed left join `tabStock Entry` se on sed.parent = se.name
				where se.auto_created = 0 and se.docstatus=1 and sed.manufacturing_operation = '{row.manufacturing_operation}' and
				sed.t_warehouse = '{department_wh}'
				and sed.to_department = '{doc.current_department}' group by se.name order by se.posting_date""",
			as_dict=1,
			pluck=1,
		)

		prev_mfg_operation = get_previous_operation(row.manufacturing_operation)
		in_transit_wh = frappe.get_value(
			"Warehouse",
			{"department": doc.next_department, "warehouse_type": "Manufacturing"},
			"default_in_transit_warehouse",
		)
		stock_entries = frappe.get_all(
			"Stock Entry Detail",
			filters={
				"manufacturing_operation": prev_mfg_operation,
				"t_warehouse": department_wh,
				"to_department": doc.current_department,
				"docstatus": 1,
			},
			or_filters={"employee": ["is", "set"], "subcontractor": ["is", "set"]},
			pluck="parent",
			group_by="parent",
		)

	for stock_entry in fetch_manual_stock_entries:
		end_transit(doc, send_in_transit_wh, department_wh, manufacturing_operation, stock_entry)
		start_transit(doc, in_transit_wh, department_wh, manufacturing_operation, stock_entry)

	for stock_entry in stock_entries + non_automated_entries:
		start_transit(doc, in_transit_wh, department_wh, manufacturing_operation, stock_entry)


def start_transit(doc, in_transit_wh, department_wh, manufacturing_operation, stock_entry):
	existing_doc = frappe.get_doc("Stock Entry", stock_entry)
	se_doc = frappe.copy_doc(existing_doc)
	se_doc.stock_entry_type = "Material Transfer to Department"
	se_doc.from_warehouse = None
	se_doc.to_warehouse = None
	for child in se_doc.items:
		child.t_warehouse = in_transit_wh
		child.s_warehouse = department_wh
		child.material_request = None
		child.material_request_item = None
		child.manufacturing_operation = manufacturing_operation
		child.department = doc.current_department
		child.to_department = doc.next_department
		child.to_main_slip = None
		child.main_slip = None
		child.employee = None
		child.to_employee = None
		child.subcontractor = None
		child.to_subcontractor = None
		child.use_serial_batch_fields = True
		child.serial_and_batch_bundle = None

	se_doc.to_main_slip = None
	se_doc.main_slip = None
	se_doc.employee = None
	se_doc.to_employee = None
	se_doc.subcontractor = None
	se_doc.to_subcontractor = None
	se_doc.department = doc.current_department
	se_doc.to_department = doc.next_department
	se_doc.department_ir = doc.name
	se_doc.manufacturing_operation = manufacturing_operation
	se_doc.auto_created = True
	se_doc.add_to_transit = 1
	se_doc.flags.ignore_permissions = True
	se_doc.save()
	se_doc.submit()


def end_transit(doc, in_transit_wh, department_wh, manufacturing_operation, stock_entry):

	existing_doc = frappe.get_doc("Stock Entry", stock_entry)
	se_doc = frappe.copy_doc(existing_doc)
	se_doc.stock_entry_type = "Material Transfer to Department"
	se_doc.from_warehouse = None
	se_doc.to_warehouse = None

	# for child in se_doc.items:
	for i, child in enumerate(se_doc.items):
		child.t_warehouse = department_wh
		child.s_warehouse = in_transit_wh
		child.material_request = None
		child.material_request_item = None
		child.manufacturing_operation = manufacturing_operation
		child.department = doc.current_department
		child.to_department = doc.next_department
		child.to_main_slip = None
		child.main_slip = None
		child.employee = None
		child.to_employee = None
		child.subcontractor = None
		child.to_subcontractor = None
		child.against_stock_entry = stock_entry
		child.stock_entry = stock_entry
		child.ste_detail = existing_doc.items[i].name
		child.use_serial_batch_fields = True
		child.serial_and_batch_bundle = None

	se_doc.to_main_slip = None
	se_doc.outgoing_stock_entry = stock_entry
	se_doc.main_slip = None
	se_doc.employee = None
	se_doc.to_employee = None
	se_doc.subcontractor = None
	se_doc.to_subcontractor = None
	se_doc.department = existing_doc.department
	se_doc.to_department = doc.current_department
	se_doc.department_ir = doc.name
	se_doc.manufacturing_operation = manufacturing_operation
	se_doc.auto_created = True
	se_doc.add_to_transit = 0
	se_doc.flags.ignore_permissions = True
	se_doc.save()
	se_doc.submit()
	return se_doc


def fetch_and_update(doc, row, manufacturing_operation):
	filters = {}
	current_dep = doc.current_department
	filters.update(
		{
			"manufacturing_work_order": row.manufacturing_work_order,
			"docstatus": 1,
			# "manufacturing_operation": ["is", "not set"],
			"to_department": current_dep,
			#  "t_warehouse": department_wh
		}
	)
	stock_entries = frappe.get_all("Stock Entry", filters=filters, pluck="name")

	if not stock_entries:
		# update_manufacturing_operation(stock_entry)
		# frappe.msgprint(f"No entries received against MWO : {row.manufacturing_work_order} and Department{doc.current_department}")
		return False
	else:

		values = {"manufacturing_operation": manufacturing_operation}
		for stock_entry in stock_entries:
			rows = frappe.get_all("Stock Entry Detail", {"parent": stock_entry}, pluck="name")
			set_values_in_bulk("Stock Entry Detail", rows, values)
			values[scrub(doc.doctype)] = doc.name
			frappe.db.set_value("Stock Entry", stock_entry, values)
			del values[scrub(doc.doctype)]
			update_manufacturing_operation(stock_entry)


def create_stock_entry(doc, row):

	in_transit_wh = frappe.db.get_value(
		"Warehouse",
		{"department": doc.current_department, "warehouse_type": "Manufacturing"},
		"default_in_transit_warehouse",
	)
	if not in_transit_wh:
		# frappe.throw(_(f"Please set transit warhouse for Current Department {doc.current_department}"))
		frappe.throw(
			_("Please set transit warhouse for Current Department {0}").format(doc.current_department)
		)

	department_wh = frappe.get_value(
		"Warehouse", {"department": doc.current_department, "warehouse_type": "Manufacturing"}
	)
	if not department_wh:
		# frappe.throw(_(f"Please set warhouse for department {doc.current_department}"))
		frappe.throw(_("Please set warhouse for department {0}").format(doc.current_department))

	stock_entries = frappe.get_all(
		"Stock Entry Detail",
		{
			"manufacturing_operation": row.manufacturing_operation,
			"t_warehouse": in_transit_wh,
			"department": doc.previous_department,
			"to_department": doc.current_department,
			"docstatus": 1,
		},
		pluck="parent",
		group_by="parent",
	)

	for stock_entry in stock_entries:
		existing_doc = frappe.get_doc("Stock Entry", stock_entry)
		se_doc = frappe.copy_doc(existing_doc)
		se_doc.stock_entry_type = "Material Transfer to Department"
		se_doc.branch = frappe.db.get_value("Employee", {"user_id": frappe.session.user}, "branch")
		# for child in se_doc.items:
		for i, child in enumerate(se_doc.items):
			child.s_warehouse = in_transit_wh
			child.t_warehouse = department_wh
			child.material_request = None
			child.material_request_item = None
			child.department = doc.previous_department
			child.to_department = doc.current_department
			child.against_stock_entry = stock_entry
			child.stock_entry = stock_entry
			child.ste_detail = existing_doc.items[i].name
			child.use_serial_batch_fields = True
			child.serial_and_batch_bundle = None
		se_doc.department = doc.previous_department
		se_doc.to_department = doc.current_department
		se_doc.auto_created = True
		se_doc.add_to_transit = 0
		se_doc.department_ir = doc.name
		se_doc.flags.ignore_permissions = True
		se_doc.save()
		se_doc.submit()


def create_operation_for_next_dept(ir_name, mwo, docname, next_department, target_doc=None):
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
					"department",
					"start_time",
					"for_subcontracting",
					"subcontractor",
					"finish_time",
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
	# target_doc.time_logs =[]
	target_doc.department_issue_id = ir_name
	target_doc.department_ir_status = "In-Transit"
	target_doc.department = next_department
	target_doc.time_taken = None
	target_doc.save()
	target_doc.db_set("employee", None)
	frappe.db.set_value("Manufacturing Work Order", mwo, "manufacturing_operation", target_doc.name)
	return target_doc.name


@frappe.whitelist()
def get_manufacturing_operations(source_name, target_doc=None):
	if not target_doc:
		target_doc = frappe.new_doc("Department IR")
	elif isinstance(target_doc, str):
		target_doc = frappe.get_doc(json.loads(target_doc))

	operation = frappe.db.get_value(
		"Manufacturing Operation",
		source_name,
		["gross_wt", "manufacturing_work_order", "diamond_wt"],
		as_dict=1,
	)
	if not target_doc.get(
		"department_ir_operation", {"manufacturing_work_order": operation["manufacturing_work_order"]}
	):
		target_doc.append(
			"department_ir_operation",
			{
				"manufacturing_operation": source_name,
				"manufacturing_work_order": operation["manufacturing_work_order"],
				"gross_wt": operation["gross_wt"],
				"diamond_wt": operation["diamond_wt"],
			},
		)
	return target_doc


@frappe.whitelist()
def get_manufacturing_operations_from_department_ir(docname):
	return frappe.get_all(
		"Manufacturing Operation",
		{"department_issue_id": docname, "department_ir_status": "In-Transit"},
		["name as manufacturing_operation", "manufacturing_work_order", "prev_gross_wt as gross_wt"],
	)


@frappe.whitelist()
def department_receive_query(doctype, txt, searchfield, start, page_len, filters):
	args = {
		"txt": "%{0}%".format(txt),
	}
	condition = 'and name not in (select dp.receive_against from `tabDepartment IR` dp where dp.docstatus = 1 and dp.type = "Receive" and dp.receive_against is not NULL) '
	if filters.get("current_department"):
		args["current_department"] = filters.get("current_department")
		condition += """and current_department = %(current_department)s """

	if filters.get("next_department"):
		args["next_department"] = filters.get("next_department")
		condition += "and next_department = %(next_department)s "

	data = frappe.db.sql(
		f"""select name
			from `tabDepartment IR`
				where type = "Issue" and docstatus = 1
				and name like %(txt)s {condition}
			""",
		args,
	)
	return data if data else []


def get_material_wt(doc, manufacturing_operation):
	res = frappe.db.sql(
		f"""select ifnull(sum(if(sed.uom='Carat',sed.qty*0.2, sed.qty)),0) as gross_wt, ifnull(sum(if(i.variant_of = 'M',sed.qty,0)),0) as net_wt,
        ifnull(sum(if(i.variant_of = 'D',sed.qty,0)),0) as diamond_wt, ifnull(sum(if(i.variant_of = 'D',if(sed.uom='Carat',sed.qty*0.2, sed.qty),0)),0) as diamond_wt_in_gram,
        ifnull(sum(if(i.variant_of = 'G',sed.qty,0)),0) as gemstone_wt, ifnull(sum(if(i.variant_of = 'G',if(sed.uom='Carat',sed.qty*0.2, sed.qty),0)),0) as gemstone_wt_in_gram,
        ifnull(sum(if(i.variant_of = 'O',sed.qty,0)),0) as other_wt
        from `tabStock Entry Detail` sed left join `tabStock Entry` se on sed.parent = se.name left join `tabItem` i on i.name = sed.item_code
		    where se.{scrub(doc.doctype)} = "{doc.name}" and sed.manufacturing_operation = "{manufacturing_operation}" and se.docstatus = 1""",
		as_dict=1,
	)
	if res:
		return res[0]
	return {}
