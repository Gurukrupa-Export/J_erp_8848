import frappe
from frappe.query_builder import DocType


def valid_reparing_or_next_operation(self):
	if self.type == "Issue":
		mwo_list = [row.manufacturing_work_order for row in self.department_ir_operation]
		department = self.next_department

		DepartmentIR = DocType("Department IR")
		DepartmentIROperation = DocType("Department IR Operation")

		query = (
			frappe.qb.from_(DepartmentIR)
			.join(DepartmentIROperation)
			.on(DepartmentIROperation.parent == DepartmentIR.name)
			.select(DepartmentIR.name)
			.where(
				(DepartmentIR.name != self.name)
				& (DepartmentIROperation.manufacturing_work_order.isin(mwo_list))
				& (DepartmentIR.next_department == department)
			)
		)

		test = query.run(as_dict=True)
		if test:
			self.transfer_type = "Repairing"
