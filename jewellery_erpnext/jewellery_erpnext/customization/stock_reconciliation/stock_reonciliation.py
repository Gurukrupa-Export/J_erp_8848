import frappe

@frappe.whitelist()
def get_child_reconciliation(doc, method=None):
    child_stock = frappe.db.get_all("Child Stock Reconcilation", {"stock_reconcillation": doc}, ["name"])
    items = []
    for stock in child_stock:
        child_items = frappe.get_all("Child Stock Reconcilation Item", filters={"parent": stock.name}, fields=["*"])
        for item in child_items:
             if item.item_code is not None:
                items.append({
                    "item_code": item.item_code,
                    "warehouse": item.warehouse,
                    "qty": item.qty,
                    "valuation_rate": item.valuation_rate
                })

    return items
