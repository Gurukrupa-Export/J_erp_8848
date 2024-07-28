import frappe
from frappe.query_builder import Case
from frappe.query_builder.functions import Locate


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def item_query_filters(doctype, txt, searchfield, start, page_len, filters):

	Item = frappe.qb.DocType("Item")

	loss_variants = frappe.db.get_list("Item", {"custom_is_loss_item": 1}, pluck="name")

	query = (
		frappe.qb.from_(Item)
		.select(Item.name, Item.item_name, Item.item_group)
		.where(Item.is_stock_item == 1)
		.where(Item.has_variants == 0)
		.where(Item.variant_of.notin(loss_variants))
	)
	# Construct the query with search conditions
	query = (
		query.where(
			(Item[searchfield].like(f"%{txt}%"))
			| (Item.item_name.like(f"%{txt}%"))
			| (Item.item_group.like(f"%{txt}%"))
		)
		.orderby(Case().when(Locate(txt, Item.name) > 0, Locate(txt, Item.name)).else_(99999))
		.orderby(Case().when(Locate(txt, Item.item_name) > 0, Locate(txt, Item.item_name)).else_(99999))
		.orderby(
			Case().when(Locate(txt, Item.item_group) > 0, Locate(txt, Item.item_group)).else_(99999)
		)
		.orderby(Item.idx, order=frappe.qb.desc)
		.orderby(Item.name)
		.orderby(Item.item_name)
		.orderby(Item.item_group)
		.limit(page_len)
		.offset(start)
	)
	data = query.run()
	return data
