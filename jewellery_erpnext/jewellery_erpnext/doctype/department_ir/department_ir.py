# Copyright (c) 2023, Nirali and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _, scrub
from frappe.model.document import Document
from frappe.model.mapper import get_mapped_doc
from frappe.query_builder import CustomFunction
from frappe.query_builder.functions import IfNull, Sum
from frappe.utils import get_datetime

from jewellery_erpnext.jewellery_erpnext.doc_events.stock_entry import (
	update_manufacturing_operation,
)
from jewellery_erpnext.jewellery_erpnext.doctype.department_ir.doc_events.department_ir_utils import (
	get_summary_data,
	update_gross_wt_from_mop,
	valid_reparing_or_next_operation,
)
from jewellery_erpnext.jewellery_erpnext.doctype.manufacturing_operation.manufacturing_operation import (
	get_previous_operation,
)
from jewellery_erpnext.utils import get_value, set_values_in_bulk


class DepartmentIR(Document):
	def before_validate(self):
		update_gross_wt_from_mop(self)

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
		self.validate_operation()
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
			DIP = frappe.qb.DocType("Department IR Operation")
			DI = frappe.qb.DocType("Department IR")
			existing = (
				frappe.qb.from_(DIP)
				.left_join(DI)
				.on(DIP.parent == DI.name)
				.select(DI.name)
				.where(
					(DI.docstatus != 2)
					& (DI.name != self.name)
					& (DI.type == self.type)
					& (DIP.name == row.name)
					& (DIP.manufacturing_operation == row.manufacturing_operation)
				)
			).run()

			if existing:
				# frappe.throw(_(f"Row #{row.idx}: Found duplicate entry in Department IR: {existing[0][0]}"))
				frappe.throw(
					_("Row #{0}: Found duplicate entry in Department IR: {1}").format(row.idx, existing[0][0])
				)

	def validate_operation(self):
		for row in self.department_ir_operation:
			customer = frappe.db.get_value(
				"Manufacturing Work Order", row.manufacturing_work_order, "customer"
			)

			ignored_department = []
			if customer:
				ignored_department = frappe.db.get_all(
					"Ignore Department For MOP", {"parent": customer}, ["department"]
				)

			ignored_department = [row.department for row in ignored_department]
			if self.next_department in ignored_department:
				frappe.throw(_("Customer not requireed this operation"))

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
		dt_string = get_datetime()

		in_transit_wh = frappe.db.get_value(
			"Warehouse",
			{"disabled": 0, "department": self.current_department, "warehouse_type": "Manufacturing"},
			"default_in_transit_warehouse",
		)

		department_wh = frappe.get_value(
			"Warehouse",
			{"disabled": 0, "department": self.current_department, "warehouse_type": "Manufacturing"},
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
			# values["gross_wt"] = get_value(
			# 	"Stock Entry Detail",
			# 	{
			# 		"manufacturing_operation": row.manufacturing_operation,
			# 		"s_warehouse": in_transit_wh,
			# 		"docstatus": 1,
			# 	},
			# 	'sum(if(uom="Carat",qty*0.2,qty))',
			# 	0,
			# )
			# res = get_material_wt(self, row.manufacturing_operation)
			# values.update(res)
			if cancel:
				values.update({"department_receive_id": None, "department_ir_status": "In-Transit"})
			frappe.db.set_value("Manufacturing Operation", row.manufacturing_operation, values)
			frappe.db.set_value(
				"Manufacturing Work Order", row.manufacturing_work_order, "department", self.current_department
			)

			doc = frappe.get_doc("Manufacturing Operation", row.manufacturing_operation)
			doc.set("department_time_logs", [])
			doc.save()
			time_values = copy.deepcopy(values)
			time_values["department_start_time"] = dt_string
			add_time_log(doc, time_values)
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

		# timer code
		dt_string = get_datetime()
		status = "Not Started" if cancel else "Finished"
		values = {"status": status}

		mop_data = frappe._dict({})
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
				values["complete_time"] = dt_string
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
				doc = frappe.get_doc("Manufacturing Operation", row.manufacturing_operation)
				add_time_log(doc, values)

		add_to_transit = []
		strat_transit = []
		if mop_data and not cancel:
			in_transit_wh = frappe.get_value(
				"Warehouse",
				{"department": self.next_department, "warehouse_type": "Manufacturing"},
				"default_in_transit_warehouse",
			)

			department_wh = frappe.get_value(
				"Warehouse",
				{"disabled": 0, "department": self.current_department, "warehouse_type": "Manufacturing"},
			)
			if not department_wh:
				frappe.throw(_("Please set warhouse for department {0}").format(self.current_department))

			send_in_transit_wh = frappe.get_value(
				"Warehouse",
				{"disabled": 0, "department": self.current_department, "warehouse_type": "Manufacturing"},
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
					if row["qty"] > 0:
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
					if row["qty"] > 0:
						stock_doc.append("items", row)
				stock_doc.flags.ignore_permissions = True
				stock_doc.save()
				stock_doc.submit()

	@frappe.whitelist()
	def get_summary_data(self):
		return get_summary_data(self)


def save_mop(mop_name):
	doc = frappe.get_doc("Manufacturing Operation", mop_name)
	doc.save()

	previous_data = frappe.db.get_value(
		"Manufacturing Operation", doc.previous_mop, ["received_gross_wt", "received_net_wt"], as_dict=1
	)

	if previous_data:
		if not previous_data.get("received_net_wt"):
			frappe.db.set_value("Manufacturing Operation", doc.previous_mop, "received_net_wt", doc.net_wt)

		if not previous_data.get("received_gross_wt"):
			frappe.db.set_value(
				"Manufacturing Operation", doc.previous_mop, "received_gross_wt", doc.gross_wt
			)


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
		{"disabled": 0, "department": doc.next_department, "warehouse_type": "Manufacturing"},
		"default_in_transit_warehouse",
	)

	department_wh = frappe.get_value(
		"Warehouse",
		{"disabled": 0, "department": doc.current_department, "warehouse_type": "Manufacturing"},
	)
	if not department_wh:
		# frappe.throw(_(f"Please set warhouse for department {doc.current_department}"))
		frappe.throw(_("Please set warehouse for department {0}").format(doc.current_department))

	send_in_transit_wh = frappe.get_value(
		"Warehouse",
		{"disabled": 0, "department": doc.current_department, "warehouse_type": "Manufacturing"},
		"default_in_transit_warehouse",
	)

	## make filter to fetch the stock entry created against warehouse and operations
	SE = frappe.qb.DocType("Stock Entry")
	SED = frappe.qb.DocType("Stock Entry Detail")

	fetch_manual_stock_entries = (
		frappe.qb.from_(SE)
		.left_join(SED)
		.on(SE.name == SED.parent)
		.select(SE.name)
		.where(
			(SED.t_warehouse == send_in_transit_wh)
			& (SED.manufacturing_operation == row.manufacturing_operation)
			& (SED.to_department == doc.current_department)
			& (SED.docstatus == 1)
			& (SE.auto_created == 0)
		)
		.groupby(SE.name)
	).run(pluck="name")

	stock_entries = (
		frappe.qb.from_(SED)
		.left_join(SE)
		.on(SED.parent == SE.name)
		.select(SE.name)
		.where(
			(SE.auto_created == 1)
			& (SE.docstatus == 1)
			& (SED.manufacturing_operation == row.manufacturing_operation)
			& (SED.t_warehouse == department_wh)
			& (SED.to_department == doc.current_department)
		)
		.groupby(SE.name)
		.orderby(SE.posting_date)
	).run(as_dict=1, pluck=1)

	non_automated_entries = []
	if not stock_entries:
		non_automated_entries = (
			frappe.qb.from_(SED)
			.left_join(SE)
			.on(SED.parent == SE.name)
			.select(SE.name)
			.where(
				(SE.auto_created == 0)
				& (SE.docstatus == 1)
				& (SED.manufacturing_operation == row.manufacturing_operation)
				& (SED.t_warehouse == department_wh)
				& (SED.to_department == doc.current_department)
			)
			.groupby(SE.name)
			.orderby(SE.posting_date)
		).run(as_dict=1, pluck=1)

		prev_mfg_operation = get_previous_operation(row.manufacturing_operation)
		in_transit_wh = frappe.get_value(
			"Warehouse",
			{"disabled": 0, "department": doc.next_department, "warehouse_type": "Manufacturing"},
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
		{"disabled": 0, "department": doc.current_department, "warehouse_type": "Manufacturing"},
		"default_in_transit_warehouse",
	)
	if not in_transit_wh:
		# frappe.throw(_(f"Please set transit warhouse for Current Department {doc.current_department}"))
		frappe.throw(
			_("Please set transit warhouse for Current Department {0}").format(doc.current_department)
		)

	department_wh = frappe.get_value(
		"Warehouse",
		{"disabled": 0, "department": doc.current_department, "warehouse_type": "Manufacturing"},
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
		[
			"name as manufacturing_operation",
			"manufacturing_work_order",
			"prev_gross_wt as gross_wt",
			"previous_mop",
		],
	)


@frappe.whitelist()
def department_receive_query(doctype, txt, searchfield, start, page_len, filters):

	DIR = frappe.qb.DocType("Department IR")
	DP = frappe.qb.DocType("Department IR")
	query = (
		frappe.qb.from_(DIR)
		.select(DIR.name)
		.where(
			(DIR.type == "Issue")
			& (DIR.docstatus == 1)
			& (DIR.name.like("%{0}%".format(txt)))
			& (
				DIR.name.notin(
					frappe.qb.from_(DP)
					.select(DP.receive_against)
					.where((DP.docstatus == 1) & (DP.type == "Receive") & (DP.receive_against.isnotnull()))
				)
			)
		)
	)
	if filters.get("current_department") and filters.get("current_department") != "":
		query = query.where(DIR.current_department == filters.get("current_department"))

	if filters.get("next_department") and filters.get("next_department") != "":
		query = query.where(DIR.next_department == filters.get("next_department"))
	data = query.run()

	return data if data else []


def get_material_wt(doc, manufacturing_operation):
	SED = frappe.qb.DocType("Stock Entry Detail")
	SE = frappe.qb.DocType("Stock Entry")
	Item = frappe.qb.DocType("Item")

	IF = CustomFunction("IF", ["condition", "true_expr", "false_expr"])
	query = (
		frappe.qb.from_(SED)
		.left_join(SE)
		.on(SED.parent == SE.name)
		.left_join(Item)
		.on(Item.name == SED.item_code)
		.select(
			IfNull(Sum(IF(SED.uom == "Carat", SED.qty * 0.2, SED.qty)), 0).as_("gross_wt"),
			IfNull(Sum(IF(Item.variant_of == "M", SED.qty, 0)), 0).as_("net_wt"),
			IfNull(Sum(IF(Item.variant_of == "D", SED.qty, 0)), 0).as_("diamond_wt"),
			IfNull(
				Sum(IF(Item.variant_of == "D", IF(SED.uom == "Carat", SED.qty * 0.2, SED.qty), 0)), 0
			).as_("diamond_wt_in_gram"),
			IfNull(Sum(IF(Item.variant_of == "G", SED.qty, 0)), 0).as_("gemstone_wt"),
			IfNull(
				Sum(IF(Item.variant_of == "G", IF(SED.uom == "Carat", SED.qty * 0.2, SED.qty), 0)), 0
			).as_("gemstone_wt_in_gram"),
			IfNull(Sum(IF(Item.variant_of == "O", SED.qty, 0)), 0).as_("other_wt"),
		)
		.where(
			(SE[scrub(doc.doctype)] == doc.name)
			& (SED.manufacturing_operation == manufacturing_operation)
			& (SE.docstatus == 1)
		)
	)
	res = query.run(as_dict=True)

	if res:
		return res[0]
	return {}


# timer code
def add_time_log(doc, args):
	last_row = []

	if doc.department_time_logs and len(doc.department_time_logs) > 0:
		last_row = doc.department_time_logs[-1]

	doc.reset_timer_value(args)

	# issue - complete_time
	if last_row and args.get("complete_time"):
		for row in doc.department_time_logs:
			if not row.department_to_time:
				row.update(
					{
						"department_to_time": get_datetime(args.get("complete_time")),
					}
				)

	# receive - department_start_time
	elif args.get("department_start_time"):

		new_args = frappe._dict(
			{
				"department_from_time": get_datetime(args.get("department_start_time")),
			}
		)
		doc.add_start_time_log(new_args)

	doc.save()
