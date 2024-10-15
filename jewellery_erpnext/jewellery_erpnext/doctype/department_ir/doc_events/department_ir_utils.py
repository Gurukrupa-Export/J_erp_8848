import frappe
from frappe.query_builder import DocType
from frappe.utils import flt


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


def get_summary_data(self):
	data = [
		{
			"gross_wt": 0,
			"net_wt": 0,
			"finding_wt": 0,
			"diamond_wt": 0,
			"gemstone_wt": 0,
			"other_wt": 0,
			"diamond_pcs": 0,
			"gemstone_pcs": 0,
		}
	]
	for row in self.department_ir_operation:
		for i in data[0]:
			if row.get(i):
				value = row.get(i)
				if i in ["diamond_pcs", "gemstone_pcs"] and row.get(i):
					value = int(row.get(i))
				data[0][i] += flt(value, 3)

	return data


def update_gross_wt_from_mop(self):
	if not self.department_ir_operation:
		return

	for row in self.department_ir_operation:
		mop_data = frappe.db.get_value(
			"Manufacturing Operation",
			row.manufacturing_operation,
			[
				"gross_wt",
				"diamond_wt",
				"net_wt",
				"finding_wt",
				"diamond_pcs",
				"gemstone_pcs",
				"gemstone_wt",
				"other_wt",
			],
			as_dict=1,
		)
		previous_mop = frappe.db.get_value(
			"Manufacturing Operation", row.manufacturing_operation, "previous_mop"
		)

		previous_mop_data = frappe._dict()

		if previous_mop:
			previous_mop_data = frappe.db.get_value(
				"Manufacturing Operation",
				previous_mop,
				[
					"gross_wt",
					"diamond_wt",
					"net_wt",
					"finding_wt",
					"diamond_pcs",
					"gemstone_pcs",
					"gemstone_wt",
					"other_wt",
				],
				as_dict=1,
			)

		row.net_wt = mop_data.get("net_wt") or previous_mop_data.get("net_wt")
		row.diamond_wt = mop_data.get("diamond_wt") or previous_mop_data.get("diamond_wt")
		row.finding_wt = mop_data.get("finding_wt") or previous_mop_data.get("finding_wt")
		row.diamond_pcs = mop_data.get("diamond_pcs") or previous_mop_data.get("diamond_pcs")
		row.gemstone_pcs = mop_data.get("gemstone_pcs") or previous_mop_data.get("gemstone_pcs")
		row.gemstone_wt = mop_data.get("gemstone_wt") or previous_mop_data.get("gemstone_wt")
		row.other_wt = mop_data.get("other_wt") or previous_mop_data.get("other_wt")
