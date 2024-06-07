from jewellery_erpnext.jewellery_erpnext.customization.purchase_receipt.doc_events.utils import (
	update_customer,
)


def before_validate(self, method):
	update_customer(self)
