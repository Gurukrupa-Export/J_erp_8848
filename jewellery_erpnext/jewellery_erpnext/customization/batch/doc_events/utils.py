import frappe


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
