import frappe
from frappe import _
from frappe.query_builder import DocType


def update_delivery_date(self):
	if self.custom_updated_delivery_date:
		pmo_list = frappe.db.get_list("Parent Manufacturing Order", {"sales_order": self.name})

		for row in pmo_list:
			frappe.db.set_value(
				"Parent Manufacturing Order",
				row.name,
				"custom_updated_delivery_date",
				self.custom_updated_delivery_date,
			)

		frappe.msgprint(_("Update Delivery Date is updated"))


def validate_duplicate_so(self):
	if self.status != "Closed":
		for row in self.items:
			if row.serial_no:
				so = DocType("Sales Order")
				soi = DocType("Sales Order Item")
				values = (
					frappe.qb.from_(so)
					.inner_join(soi)
					.on(soi.parent == so.name)
					.select(soi.name)
					.where(soi.qty > soi.delivered_qty)
					.where(soi.serial_no == row.serial_no)
					.where(soi.parent != self.name)
					.where(so.customer == self.customer)
				).run(as_dict=1)

				if values:
					frappe.throw(_("Sales Order exists with same Serial Number"))
