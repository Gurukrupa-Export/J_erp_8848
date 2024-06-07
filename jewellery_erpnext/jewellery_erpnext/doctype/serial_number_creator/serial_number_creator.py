# Copyright (c) 2024, Nirali and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import date_diff, get_first_day, get_last_day, nowdate, time_diff

from jewellery_erpnext.jewellery_erpnext.doctype.manufacturing_operation.manufacturing_operation import (
	create_finished_goods_bom,
	create_manufacturing_entry,
	set_values_in_bulk,
)


class SerialNumberCreator(Document):
	def validate(self):
		pass

	def on_submit(self):
		validate_qty(self)
		calulate_id_wise_sum_up(self)
		to_prepare_data_for_make_mnf_stock_entry(self)
		update_new_serial_no(self)

	@frappe.whitelist()
	def get_serial_summary(self):
		# Define the tables
		stock_entry = frappe.qb.DocType("Stock Entry")
		serial_no = frappe.qb.DocType("Serial No")
		bom = frappe.qb.DocType("BOM")

		# Build the query
		data = (
			frappe.qb.from_(stock_entry)
			.inner_join(serial_no)
			.on(stock_entry.name == serial_no.purchase_document_no)
			.inner_join(bom)
			.on(serial_no.name == bom.tag_no)
			.select(serial_no.purchase_document_no, serial_no.serial_no, bom.name)
			.where(stock_entry.custom_serial_number_creator == self.name)
		).run(as_dict=True)

		return frappe.render_template(
			"jewellery_erpnext/jewellery_erpnext/doctype/serial_number_creator/serial_summery.html",
			{"data": data},
		)

	@frappe.whitelist()
	def get_bom_summary(self):
		if self.design_id_bom:
			bom_data = frappe.get_doc("BOM", self.design_id_bom)
			item_records = []
			for bom_row in bom_data.items:
				item_record = {"item_code": bom_row.item_code, "qty": bom_row.qty, "uom": bom_row.uom}
				item_records.append(item_record)
			return frappe.render_template(
				"jewellery_erpnext/jewellery_erpnext/doctype/serial_number_creator/bom_summery.html",
				{"data": item_records},
			)


def to_prepare_data_for_make_mnf_stock_entry(self):
	id_wise_data_split = {}
	for row in self.fg_details:
		if row.id:
			key = row.id
			if key not in id_wise_data_split:
				id_wise_data_split[key] = []
				id_wise_data_split[key].append(
					{
						"item_code": row.row_material,
						"qty": row.qty,
						"uom": row.uom,
						"id": row.id,
						"inventory_type": row.inventory_type,
						"batch_no": row.batch_no,
					}
				)
			else:
				id_wise_data_split[key].append(
					{
						"item_code": row.row_material,
						"qty": row.qty,
						"uom": row.uom,
						"id": row.id,
						"inventory_type": row.inventory_type,
						"batch_no": row.batch_no,
					}
				)
	for key, row_data in id_wise_data_split.items():
		se_name = create_manufacturing_entry(self, row_data)

		pmo = frappe.db.get_value(
			"Manufacturing Work Order", self.manufacturing_work_order, "manufacturing_order"
		)

		wo = frappe.get_all("Manufacturing Work Order", {"manufacturing_order": pmo}, pluck="name")
		set_values_in_bulk("Manufacturing Work Order", wo, {"status": "Completed"})

		all_mo = frappe.db.get_all(
			"Manufacturing Operation",
			{"manufacturing_order": pmo},
			["name", "employee", "total_minutes", "creation", "finish_time"],
			order_by="creation",
		)
		mo_data = {}
		total_time = time_diff(all_mo[-1]["finish_time"], all_mo[0]["creation"]).total_seconds() / 60
		for row in all_mo:
			if row.employee:
				operation_time = row.total_minutes or 0
				workstation = frappe.db.get_all(
					"Workstation",
					{"employee": row.employee},
					["name", "hour_rate_electricity", "hour_rate_rent", "hour_rate_consumable"],
				)
				if workstation:
					workstation = workstation[0]
				else:
					frappe.throw(f"Please define Workstation for {row.employee}")
				hour_rate_labour = get_hourly_rate(row.employee)

				total_expense = (
					workstation.hour_rate_electricity
					+ workstation.hour_rate_rent
					+ workstation.hour_rate_consumable
					+ hour_rate_labour
				)
				mo_data[row.name] = {
					"workstation": workstation.name,
					"total_expense": total_expense,
					"operation_time": operation_time,
				}

		create_finished_goods_bom(self, se_name, mo_data, total_time)


def get_shift(employee, start_date, end_date):
	Attendance = frappe.qb.DocType("Attendance")

	shift = (
		frappe.qb.from_(Attendance)
		.select(Attendance.shift)
		.distinct()
		.where(
			(Attendance.employee == employee)
			& (Attendance.attendance_date.between(start_date, end_date))
			& (Attendance.shift.notnull())
		)
	).run(pluck=True)

	if shift:
		return shift[0]

	return ""


def get_hourly_rate(employee):
	hourly_rate = 0
	start_date, end_date = get_first_day(nowdate()), get_last_day(nowdate())
	shift = get_shift(employee, start_date, end_date)
	shift_hours = frappe.utils.flt(frappe.db.get_value("Shift Type", shift, "shift_hours")) or 10

	base = frappe.db.get_value("Employee", employee, "ctc")

	holidays = get_holidays_for_employee(employee, start_date, end_date)
	working_days = date_diff(end_date, start_date) + 1

	working_days -= len(holidays)

	total_working_days = working_days
	target_working_hours = frappe.utils.flt(shift_hours * total_working_days)

	if target_working_hours:
		hourly_rate = frappe.utils.flt(base / target_working_hours)

	return hourly_rate


def get_holidays_for_employee(employee, start_date, end_date):
	from erpnext.setup.doctype.employee.employee import get_holiday_list_for_employee
	from hrms.utils.holiday_list import get_holiday_dates_between

	HOLIDAYS_BETWEEN_DATES = "holidays_between_dates"

	holiday_list = get_holiday_list_for_employee(employee)
	key = f"{holiday_list}:{start_date}:{end_date}"
	holiday_dates = frappe.cache().hget(HOLIDAYS_BETWEEN_DATES, key)

	if not holiday_dates:
		holiday_dates = get_holiday_dates_between(holiday_list, start_date, end_date)
		frappe.cache().hset(HOLIDAYS_BETWEEN_DATES, key, holiday_dates)

	return holiday_dates


def validate_qty(self):
	for row in self.fg_details:
		if row.qty == 0:
			frappe.throw(_("FG Details Table Quantity Zero Not Allowed"))


@frappe.whitelist()
def get_operation_details(data, docname, mwo, pmo, company, mnf, dpt, for_fg, design_id_bom):
	exist_snc_doc = frappe.get_all(
		"Serial Number Creator",
		filters={"manufacturing_operation": docname, "docstatus": ["!=", 2]},
		fields=["name"],
	)
	if exist_snc_doc:
		frappe.throw(f"Document Already Created...! {exist_snc_doc[0]['name']}")
	snc_doc = frappe.new_doc("Serial Number Creator")
	mnf_op_doc = frappe.get_doc("Manufacturing Operation", docname)
	data_dict = json.loads(data)
	stock_data = data_dict[0]
	bom_id = data_dict[1]
	mnf_qty = data_dict[2]
	total_qty = data_dict[3]

	existing_se_item = []
	for mnf_id in range(1, mnf_qty + 1):
		for data_entry in stock_data:
			_qty = round(data_entry["qty"] / mnf_qty, 4)
			if data_entry["name"] not in existing_se_item:
				existing_se_item.append(data_entry["name"])
				snc_doc.append(
					"fg_details",
					{
						"row_material": data_entry["item_code"],
						"id": mnf_id,
						"batch_no": data_entry["batch_no"],
						"qty": _qty,  # data_entry["qty"],
						"uom": data_entry["uom"],
						"gross_wt": data_entry["gross_wt"],
						"inventory_type": data_entry["inventory_type"],
						"sub_setting_type": data_entry.get("custom_sub_setting_type"),
						"sed_item": data_entry["name"],
					},
				)
	if mnf_qty > 1:
		for data_entry in stock_data:
			snc_doc.append(
				"source_table",
				{
					"row_material": data_entry["item_code"],
					"qty": data_entry["qty"],
					"uom": data_entry["uom"],
					# "id": mnf_id,
					# "batch_no": data_entry["batch_no"],
					# "gross_wt": data_entry["gross_wt"],
				},
			)
	snc_doc.type = "Manufacturing"
	snc_doc.manufacturing_operation = mnf_op_doc.name
	snc_doc.manufacturing_work_order = mwo
	snc_doc.parent_manufacturing_order = pmo
	snc_doc.company = company
	snc_doc.manufacturer = mnf
	snc_doc.department = dpt
	snc_doc.for_fg = for_fg
	snc_doc.design_id_bom = design_id_bom
	snc_doc.total_weight = total_qty
	snc_doc.save()
	mnf_op_doc.status = "Finished"
	mnf_op_doc.save()
	frappe.msgprint(
		f"<b>Serial Number Creator</b> Document Created...! <b>Doc NO:</b> {snc_doc.name}"
	)


from decimal import ROUND_HALF_UP, Decimal


def calulate_id_wise_sum_up(self):
	id_qty_sum = {}  # Dictionary to store the sum of 'qty' for each 'id'
	item_wise_total = []
	for row in self.fg_details:
		if row.id and row.row_material:
			key = row.row_material
			if key not in id_qty_sum:
				id_qty_sum[key] = float(Decimal("0.000"))  # round(0,3)

			# if row.uom == "cts":
			# 	id_qty_sum[key] += round(row.qty * 0.2,3)
			# else:
			# id_qty_sum[key] += round(row.qty,3)
			id_qty_sum[key] += float(
				Decimal(str(row.qty)).quantize(Decimal("0.000"), rounding=ROUND_HALF_UP)
			)
	id_qty_sum = {key: round(float(value), 3) for key, value in id_qty_sum.items()}

	for (row_material), qty_sum in id_qty_sum.items():
		for row in self.source_table:
			if row_material == row.get("row_material") and round(qty_sum, 3) != round(row.get("qty"), 3):
				frappe.throw(
					f"Row Material in FG Details <b>{row_material}</b> does not match </br></br>ID Wise Row Material SUM: <b>{round(qty_sum, 3)}</b></br>Must be equal of row <b>#{row.get('idx')}</b> in source table<b>: {row.get('qty')}</b>"
				)


import copy


def update_new_serial_no(self):
	if self.serial_no and self.fg_details:
		serial_doc = frappe.get_doc("Serial No", self.fg_details[0].serial_no)
		previos_sr = frappe.db.get_value(
			"Serial No",
			self.serial_no,
			["purchase_document_no", "item_code", "custom_repair_type", "custom_product_type"],
			as_dict=1,
		)

		huid_details = ""
		certificate_details = ""
		for row in frappe.db.get_all("HUID Detail", {"parent": self.serial_no}, ["*"]):
			if row.huid:
				huid_details += """
								{0} - {1}""".format(
					row.huid, row.date
				)
			if row.certification_no:
				certificate_details += """
								{0} - {1}""".format(
					row.certification_no, row.certification_date
				)

		for row in frappe.db.get_all("Serial No Table", {"parent": self.serial_no}, ["*"]):
			temp_row = copy.deepcopy(row)
			temp_row["name"] = None
			serial_doc.append("custom_serial_no_table", temp_row)

		serial_doc.append(
			"custom_serial_no_table",
			{
				"serial_no": self.serial_no,
				"item_code": previos_sr.item_code,
				"purchase_document_no": previos_sr.purchase_document_no,
				"pmo": self.parent_manufacturing_order,
				"mwo": self.manufacturing_work_order,
				"bom": self.design_id_bom,
				"huid_details": huid_details,
				"certification_details": certificate_details,
				"repair_type": previos_sr.get("repair_type"),
				"product_type": previos_sr.get("product_type"),
			},
		)
		serial_doc.save()
