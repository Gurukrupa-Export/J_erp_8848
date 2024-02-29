import frappe
def update_table(self, method):
    # frappe.throw("HI")
    serial_numbers = frappe.get_all("Serial No",filters={"name": self.name},fields={"*"})
    existing_serial_record = frappe.get_all("Serial No Table", filters={"parent": self.name,"purchase_document_no":self.purchase_document_no})
    if existing_serial_record:
        pass
        # frappe.db.set_value("Serial No Table", existing_serial_record[0].name,"serial_no",self.name)
        # frappe.db.set_value("Serial No Table", existing_serial_record[0].name,"warranty_period",self.warranty_period)
        
    else:
        # frappe.throw(f"{existing_serial_record}")
        for serial_number in serial_numbers:
            new_serial_record = frappe.new_doc("Serial No Table")
            new_serial_record.update({
                "parent": self.name,
                "parenttype":"Serial No",
                "parentfield":"custom_serial_no_table",
                "serial_no": serial_number.get("serial_no"),
                "item_code":serial_number.get("item_code"),
                "company":serial_number.get("company"),
                "purchase_document_no":serial_number.get("purchase_document_no")
            })
            new_serial_record.insert()