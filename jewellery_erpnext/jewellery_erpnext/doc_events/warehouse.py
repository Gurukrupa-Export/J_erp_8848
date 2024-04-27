import frappe
from frappe import _


def validate(doc, method=None):
	if doc.department and doc.employee:
		frappe.throw(_("You can fill in either the department or employee field, but not both."))
	check_unique_multifield(
		doc,
		warehouse_type=doc.warehouse_type,
		department=doc.department,
		subcontractor=doc.subcontractor,
		employee=doc.employee,
	)


def check_unique_multifield(doc, **kwargs):
	if doc.department and doc.warehouse_type not in ["Manufacturing", "Raw Material", "Reserve"]:
		frappe.throw(_("Warehouse type must be set one of this <b>(Manufacturing or Raw Material)</b>"))
	if doc.employee and doc.warehouse_type not in ["Manufacturing", "Raw Material", "Reserve"]:
		frappe.throw(_("Warehouse type must be set one of this <b>(Manufacturing or Raw Material)</b>"))
	if not any(kwargs.values()):
		return
	if doc.department or doc.employee:
		existing_fields = [f"{field} = {value}" for field, value in kwargs.items() if value]
		if existing_fields:
			filters = {key: value for key, value in kwargs.items() if value}
			filters["name"] = ["!=", doc.name]
			if existing := frappe.db.exists("Warehouse", filters):
				frappe.throw(
					_(f"Warehouse: <b>{existing}</b> already has set </br><b>{', '.join(existing_fields)}</b>")
				)
