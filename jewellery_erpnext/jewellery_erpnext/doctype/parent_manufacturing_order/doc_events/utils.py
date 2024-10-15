import frappe


def update_parent_details(self):
	mfg_plan_details = frappe.db.get_value(
		"Manufacturing Plan Table",
		{"customer_po": self.po_no, "subcontracting": 1},
		["parent", "sales_order", "docname"],
		as_dict=1,
	)

	if not mfg_plan_details:
		return

	if mfg_plan_details.get("docname"):
		quotation = frappe.db.get_value(
			"Sales Order Item", mfg_plan_details["docname"], "prevdoc_docname"
		)
		self.parent_quotation = quotation

	self.parent_sales_order = mfg_plan_details.get("sales_order")
	self.parent_mp = mfg_plan_details.get("parent")
