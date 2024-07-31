import copy

import frappe
from erpnext.stock.doctype.batch.batch import get_batch_qty
from erpnext.stock.doctype.stock_entry.stock_entry import StockEntry
from frappe import _
from frappe.utils import flt

from jewellery_erpnext.jewellery_erpnext.customization.stock_entry.doc_events.inventory_utils import (
	validate_customer_voucher,
)
from jewellery_erpnext.jewellery_erpnext.customization.stock_entry.doc_events.se_utils import (
	get_fifo_batches,
	validate_inventory_dimention,
)
from jewellery_erpnext.jewellery_erpnext.doc_events.stock_entry import (
	custom_get_bom_scrap_material,
	custom_get_scrap_items_from_job_card,
)


def before_validate(self, method):
	validate_customer_voucher(self)


def on_submit(self, method):
	validate_inventory_dimention(self)


class CustomStockEntry(StockEntry):
	@frappe.whitelist()
	def update_batches(self):
		if not self.auto_created:
			rows_to_append = []
			for row in self.items:
				if (
					row.get("department")
					and frappe.db.get_value("Department", row.department, "custom_can_not_make_dg_entry") == 1
				):
					if frappe.db.get_value("Item", row.item_code, "variant_of") in ["D", "G"]:
						frappe.throw(_("{0} not allowed in Operation {1}").format(row.item_code, row.department))
				if frappe.db.get_value("Item", row.item_code, "has_batch_no"):
					if row.s_warehouse:
						if row.get("batch_no") and get_batch_qty(row.batch_no, row.s_warehouse) >= row.qty:
							temp_row = copy.deepcopy(row)
							rows_to_append += [temp_row]
						else:
							rows_to_append += get_fifo_batches(self, row)
					elif row.t_warehouse:
						rows_to_append += [row.__dict__]
				else:
					rows_to_append += [row.__dict__]

			if rows_to_append:
				self.items = []
				for item in rows_to_append:
					self.append("items", item)

			if frappe.db.exists("Stock Entry", self.name):
				self.db_update()

	def validate_with_material_request(self):
		for item in self.get("items"):
			material_request = item.material_request or None
			material_request_item = item.material_request_item or None
			if self.purpose == "Material Transfer" and self.outgoing_stock_entry:
				parent_se = frappe.get_value(
					"Stock Entry Detail",
					item.ste_detail,
					["material_request", "material_request_item"],
					as_dict=True,
				)
				if parent_se:
					material_request = parent_se.material_request
					material_request_item = parent_se.material_request_item

			if material_request:
				mreq_item = frappe.db.get_value(
					"Material Request Item",
					{"name": material_request_item, "parent": material_request},
					["item_code", "custom_alternative_item", "warehouse", "idx"],
					as_dict=True,
				)
				if item.item_code not in [mreq_item.item_code, mreq_item.custom_alternative_item]:
					frappe.throw(
						_("Item for row {0} does not match Material Request").format(item.idx),
						frappe.MappingMismatchError,
					)
				elif self.purpose == "Material Transfer" and self.add_to_transit:
					continue

	@frappe.whitelist()
	def get_html_data(self):
		itemwise_data = {}
		for row in self.items:
			if itemwise_data.get(row.item_code):
				itemwise_data[row.item_code]["qty"] += row.qty
				itemwise_data[row.item_code]["pcs"] += int(row.get("pcs")) if row.get("pcs") else 0
			else:
				itemwise_data[row.item_code] = {
					"qty": row.qty,
					"pcs": int(row.get("pcs")) if row.get("pcs") else 0,
				}

		data = []
		for row in itemwise_data:
			data.append(
				{
					"item_code": row,
					"qty": flt(itemwise_data[row].get("qty"), 3),
					"pcs": itemwise_data[row].get("pcs"),
				}
			)

		return data

	def get_scrap_items_from_job_card(self):
		custom_get_scrap_items_from_job_card(self)

	def get_bom_scrap_material(self, qty):
		custom_get_bom_scrap_material(self, qty)
