import json

import frappe


@frappe.whitelist()
def get_mwo(source_name, target_doc=None):
	if not target_doc:
		target_doc = frappe.new_doc("Manufacturing Plan")
	elif isinstance(target_doc, str):
		target_doc = frappe.get_doc(json.loads(target_doc))
	if not target_doc.get("manufacturing_work_order", {"manufacturing_work_order": source_name}):
		target_doc.append(
			"manufacturing_work_order",
			{"manufacturing_work_order": source_name},
		)
	return target_doc


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_mwo_details(doctype, txt, searchfield, start, page_len, filters):
	conditions = ""
	MWO = frappe.qb.DocType("Manufacturing Work Order")

	query = (
		frappe.qb.from_(MWO)
		.select(MWO.name, MWO.company, MWO.customer)
		.distinct()
		.where((MWO.docstatus == 1) & (MWO.is_finding_mwo == 1))
		.limit(page_len)
		.offset(start)
	)
	mwo_data = query.run(as_dict=True)

	return mwo_data
