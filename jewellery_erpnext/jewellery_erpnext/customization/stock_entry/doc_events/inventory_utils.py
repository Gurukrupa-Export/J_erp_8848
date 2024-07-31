import frappe
from frappe import _


def validate_customer_voucher(self):
	if not self.customer_voucher_type:
		return

	if self.customer_voucher_type == "Customer Subcontracting":
		for row in self.items:
			if frappe.db.get_value("Item", row.item_code, "has_serial_no") == 1:
				frappe.throw(_("Serialized items not allowd in this Customer Voucher Type"))
	elif self.customer_voucher_type in ["Customer Repair"]:
		for row in self.items:
			if frappe.db.get_value("Item", row.item_code, "has_batch_no") == 1:
				frappe.throw(_("Batch items not allowd in this Customer Voucher Type"))

	if self.customer_voucher_type == "Customer Sample Goods":
		for row in self.items:
			if (
				row.manufacturing_operation
				and frappe.db.get_value("Warehouse", row.t_warehouse, "warehouse_type") == "Manufacturing"
			):
				frappe.throw(_("Manufacturing Type warehouse not allowed in this Customer Voucher Type"))
