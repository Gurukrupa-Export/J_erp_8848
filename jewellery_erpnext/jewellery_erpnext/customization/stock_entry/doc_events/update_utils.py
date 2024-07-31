import frappe


def update_main_slip_se_details(doc, stock_entry_type, se_row, auto_created=0, is_cancelled=False):
	based_on = "employee"
	based_on_value = doc.employee
	if doc.subcontractor:
		based_on = "subcontractor"
		based_on_value = doc.subcontractor

	m_warehouse = frappe.db.get_value(
		"Warehouse", {based_on: based_on_value, "warehouse_type": "Manufacturing"}
	)
	r_warehouse = frappe.db.get_value(
		"Warehouse", {based_on: based_on_value, "warehouse_type": "Raw Material"}
	)

	qty = "qty"
	consume_qty = "consume_qty"
	if se_row.manufacturing_operation and stock_entry_type not in (
		"Material Transfer (MAIN SLIP)",
		"Manufacture",
	):
		qty = "mop_qty"
		consume_qty = "mop_consume_qty"

	exsting_se_details = [row.se_item for row in doc.stock_details]
	if se_row.s_warehouse == m_warehouse:
		if se_row.name not in exsting_se_details:
			if se_row.manufacturing_operation:
				consume_qty = "mop_consume_qty"
			doc.append(
				"stock_details",
				{
					"batch_no": se_row.batch_no,
					consume_qty: se_row.qty,
					"se_item": se_row.name,
					"auto_created": auto_created,
					"stock_entry": se_row.parent,
				},
			)

	if se_row.t_warehouse == r_warehouse:
		if se_row.name not in exsting_se_details:
			doc.append(
				"stock_details",
				{
					"item_code": se_row.item_code,
					"batch_no": se_row.batch_no,
					qty: se_row.qty,
					"se_item": se_row.name,
					"auto_created": auto_created,
					"stock_entry": se_row.parent,
				},
			)

	if (se_row.s_warehouse == r_warehouse) and (se_row.t_warehouse == m_warehouse):
		if se_row.name not in exsting_se_details:
			doc.append(
				"stock_details",
				{
					"item_code": se_row.item_code,
					"batch_no": se_row.batch_no,
					"consume_qty": se_row.qty,
					"se_item": se_row.name,
					"auto_created": auto_created,
					"stock_entry": se_row.parent,
				},
			)
			doc.append(
				"stock_details",
				{
					"item_code": se_row.item_code,
					"batch_no": se_row.batch_no,
					"mop_qty": se_row.qty,
					"se_item": se_row.name,
					"auto_created": auto_created,
					"stock_entry": se_row.parent,
				},
			)

	elif se_row.s_warehouse == r_warehouse:
		if se_row.name not in exsting_se_details:
			doc.append(
				"stock_details",
				{
					"batch_no": se_row.batch_no,
					consume_qty: se_row.qty,
					"se_item": se_row.name,
					"auto_created": auto_created,
					"stock_entry": se_row.parent,
				},
			)

	elif se_row.t_warehouse == m_warehouse:
		if se_row.name not in exsting_se_details:
			doc.append(
				"stock_details",
				{
					"batch_no": se_row.batch_no,
					qty: se_row.qty,
					"se_item": se_row.name,
					"auto_created": auto_created,
					"stock_entry": se_row.parent,
				},
			)
