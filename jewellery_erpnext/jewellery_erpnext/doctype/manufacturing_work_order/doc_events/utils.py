import frappe
from frappe import _
from frappe.utils import get_datetime
from frappe.query_builder.functions import IfNull, Sum
from frappe.query_builder import CustomFunction

def create_se_entry(self):
	employee = frappe.db.get_value(
		"Manufacturing Operation", self.manufacturing_operation, "employee"
	)
	if employee:
		target_wh = frappe.db.get_value(
			"Warehouse", {"employee": employee, "warehouse_type": "Manufacturing"}
		)
	else:
		target_wh = frappe.db.get_value(
			"Warehouse", {"department": self.department, "warehouse_type": "Manufacturing"}
		)

	raw_warehouse = frappe.db.get_value(
		"Warehouse", {"department": self.department, "warehouse_type": "Raw Material"}
	)

	SED = frappe.qb.DocType("Stock Entry Detail")
	SE = frappe.qb.DocType("Stock Entry")
	IF = CustomFunction("IF", ["condition", "true_expr", "false_expr"])
	query = (
		frappe.qb.from_(SED)
		.left_join(SE)
		.on(SED.parent == SE.name)
		.select(
			SE.manufacturing_work_order,
			SE.manufacturing_operation,
			SED.parent,
			SED.item_code,
			SED.item_name,
			SED.batch_no,
			SED.qty,
			SED.uom,
			IfNull(
				Sum(
					IF(SED.uom == "Carat", SED.qty * 0.2, SED.qty)
				),
				0
			).as_("gross_wt")
		)
		.where(
			(SE.docstatus == 1) &
			(SED.manufacturing_operation == self.manufacturing_operation) &
			(SED.t_warehouse == target_wh)
		)
		.groupby(SED.manufacturing_operation, SED.item_code, SED.qty, SED.uom)
	)
	data = query.run(as_dict=True)

	if data:
		se = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"stock_entry_type": "Material Transfer to Department",
				"purpose": "Material Transfer",
				"company": self.company,
				"auto_created": 1,
				"branch": self.branch,
			}
		)

		se.set_posting_time = 1
		se.posting_date = frappe.utils.today()
		se.posting_time = frappe.utils.nowtime()

		for row in data:
			se.append(
				"items",
				{
					"item_code": row["item_code"],
					"qty": row["qty"],
					"inventory_type": row.get("inventory_type"),
					"s_warehouse": target_wh,
					"t_warehouse": raw_warehouse,
					"use_serial_batch_fields": 1,
					"set_basic_rate_manually": 1,
					"batch_no": row["batch_no"],
					"uom": row["uom"],
				},
			)

		se.flags.ignore_permissions = True
		se.save()
		se.submit()

		self.db_set("final_transfer_entry", se.name)
	frappe.db.set_value("Manufacturing Operation", self.manufacturing_operation, "status", "Finished")


def add_time_log(doc, args):
	doc.reset_timer_value(args)

	operation = args.get("operation")
	if doc.operation == operation:
		if args.get("department_start_time"):
			new_args = frappe._dict(
				{
					"department_from_time": get_datetime(args.get("department_start_time")),
				}
			)
			doc.append("department_time_logs", new_args)

	doc.save()


def create_stock_transfer_entry(self):
	transfer_mop, department = frappe.db.get_value(
		"Manufacturing Work Order", self.transfer_mwo, ["manufacturing_operation", "department"]
	)
	target_warehouse = frappe.db.get_value(
		"Warehouse", {"department": department, "warehouse_type": "Raw Material"}
	)

	mop_warehouse = frappe.db.get_value(
		"Warehouse", {"department": self.department, "warehouse_type": "Manufacturing"}
	)

	if not transfer_mop:
		frappe.throw(_("Can not tranfer to Manufacturing Work Order"))

	if not target_warehouse:
		frappe.throw(_("Raw Material type Warehouse is not set for {0}").format(department))

	stock_entry_data = frappe.db.get_all(
		"Stock Entry Detail",
		{
			"manufacturing_operation": self.manufacturing_operation,
			"t_warehouse": mop_warehouse,
			"docstatus": 1,
		},
		[
			"item_code",
			"t_warehouse as warehouse",
			"qty",
			"basic_rate",
			"inventory_type",
			"customer",
			"batch_no",
		],
	)

	se_doc = frappe.new_doc("Stock Entry")

	se_doc.stock_entry_type = "Material Transfer to Department"
	se_doc.manufacturing_order = self.manufacturing_order
	se_doc.manufacturing_work_order = self.transfer_mwo
	se_doc.manufacturing_work_order = transfer_mop
	for row in stock_entry_data:
		se_doc.append(
			"items",
			{
				"item_code": row.item_code,
				"qty": row.qty,
				"s_warehouse": row.warehouse,
				"t_warehouse": target_warehouse,
				"basic_rate": row.basic_rate,
				"batch_no": row.batch_no,
				"inventory_type": row.inventory_type,
				"customer": row.get("customer"),
			},
		)

	se_doc.save()
	se_doc.submit()
