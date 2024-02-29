import frappe


def before_validate(self):
	update_expense_account(self)


def update_expense_account(self):
	expense_account = frappe.db.get_value(
		"Account", {"company": self.company, "custom_purchase_type": self.purchase_type}, "name"
	)
	if expense_account:
		for row in self.items:
			row.expense_account = expense_account
