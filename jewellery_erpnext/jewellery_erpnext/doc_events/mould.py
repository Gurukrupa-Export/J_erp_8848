import frappe

def autoname(self, method = None):
    company_abbr = frappe.db.get_value('Company',self.company,'abbr')
    self.naming_series = 'M-' + company_abbr + '-.{category_code}.-.#####'
        
def validate(self, method = None):
    rake = self.rake
    rake = rake.capitalize()
    if rake.isnumeric():
        frappe.throw("Rake is Alphabet")
    self.rake = rake

    tray_no = self.tray_no
    if tray_no.isnumeric():
        tray_no = int(self.tray_no)
        tray_no = f"{tray_no:02}"
        self.tray_no = tray_no
    else:
        frappe.throw("Try No must be Numeric")

    box_no = self.box_no
    if box_no.isnumeric():
        box_no = int(self.box_no)
        box_no = f"{box_no:02}"
        self.box_no = box_no
    else:
        frappe.throw("Box No must be Numeric")


    mould_no = rake + '/' + tray_no + '/' + box_no
    self.mould_no = mould_no
    frappe.db.set_value('Item',self.item_code,'mould',self.mould_no)