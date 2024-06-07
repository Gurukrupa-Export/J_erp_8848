import frappe
from frappe import _


def update_customer(self):
	if frappe.db.get_value("Supplier", self.supplier, "custom_is_external_supplier") == 1:
		for row in self.items:
			row.inventory_type = "Customer Goods"
	else:
		for row in self.items:
			row.inventory_type = "Regular Stock"

	customer = frappe.db.get_value(
		"Party Link",
		filters={
			"secondary_role": "Supplier",
			"primary_role": "Customer",
			"secondary_party": self.supplier,
		},
		fieldname="primary_party",
	)

	if customer:
		for row in self.items:
			if row.inventory_type == "Customer Goods":
				row.customer = customer
	else:
		for row in self.items:
			if row.inventory_type == "Customer Goods" and not row.customer:
				frappe.throw(_("Customer is mandatory for Customer Goods inventory type"))
