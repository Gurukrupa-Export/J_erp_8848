from jewellery_erpnext.jewellery_erpnext.customization.batch.doc_events.utils import (
	update_inventory_dimentions,
)


def validate(self, method):
	update_inventory_dimentions(self)
