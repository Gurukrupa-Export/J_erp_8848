import frappe
from frappe import _


def validate_duplication(self):
	existing_mop = []
	for row in self.employee_ir_operations:
		if row.manufacturing_operation in existing_mop:
			frappe.throw(
				_("{0} appeared multiple times in Employee IR").format(row.manufacturing_operation)
			)
		existing_mop.append(row.manufacturing_operation)
		EIR = frappe.qb.DocType("Employee IR")
		EOP = frappe.qb.DocType("Employee IR Operation")
		exists = (
			frappe.qb.from_(EIR)
			.left_join(EOP)
			.on(EOP.parent == EIR.name)
			.select(EIR.name)
			.where(
				(EIR.name != self.name)
				& (EIR.type == self.type)
				& (EOP.manufacturing_operation == row.manufacturing_operation)
				& (EIR.docstatus != 2)
			)
		).run(as_dict=1)
		if exists:
			frappe.throw(_("Employee IR exists for MOP {0}").format(row.manufacturing_operation))
		save_mop(row.manufacturing_operation)


def save_mop(mop_name):
	doc = frappe.get_doc("Manufacturing Operation", mop_name)
	doc.save()
