import frappe
from frappe import _


def update_inventory_dimentions(self):
	for row in frappe.db.get_all(
		"DocField", {"parent": self.reference_doctype, "fieldtype": "Table"}, ["options"]
	):
		if frappe.db.exists(row.options, self.custom_voucher_detail_no):
			self.custom_inventory_type = frappe.db.get_value(
				row.options, self.custom_voucher_detail_no, "inventory_type"
			)
			self.custom_customer = frappe.db.get_value(
				row.options, self.custom_voucher_detail_no, "customer"
			)
			break

	if not frappe.db.get_value(
		"Item", self.item, "custom_inventory_type_can_be_customer_goods"
	) and self.custom_inventory_type in ["Customer Goods", "Customer Stock"]:
		frappe.throw(_("This item does not allowed as Customer Goods"))

	if self.reference_doctype == "Stock Entry" and self.custom_customer:
		self.custom_customer_voucher_type = frappe.db.get_value(
			"Stock Entry", self.reference_name, "customer_voucher_type"
		)
