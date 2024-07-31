import frappe


def create_mould(self):
	if self.no_of_moulds > 0 and self.mould_reference:
		for row in self.mould_reference:
			mould_doc = frappe.new_doc("Mould")
			mould_doc.rake = row.rake
			mould_doc.tray_no = row.tray_no
			mould_doc.box_no = row.box_no
			mould_doc.flags.ignore_permission = True
			mould_doc.save()
