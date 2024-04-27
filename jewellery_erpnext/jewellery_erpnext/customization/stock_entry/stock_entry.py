import copy

import frappe
from erpnext.stock.doctype.batch.batch import get_batch_qty
from erpnext.stock.doctype.stock_entry.stock_entry import StockEntry

from jewellery_erpnext.jewellery_erpnext.customization.stock_entry.doc_events.se_utils import (
	get_fifo_batches,
)


class CustomStockEntry(StockEntry):
	@frappe.whitelist()
	def update_batches(self):
		if not self.auto_created:
			rows_to_append = []
			for row in self.items:
				if frappe.db.get_value("Item", row.item_code, "has_batch_no") and row.s_warehouse:
					if row.get("batch_no") and get_batch_qty(row.batch_no, row.s_warehouse) >= row.qty:
						temp_row = copy.deepcopy(row)
						rows_to_append += [temp_row]
					else:
						rows_to_append += get_fifo_batches(self, row)

			if rows_to_append:
				self.items = []
				for item in rows_to_append:
					self.append("items", item)

			if frappe.db.exists("Stock Entry", self.name):
				self.db_update()
