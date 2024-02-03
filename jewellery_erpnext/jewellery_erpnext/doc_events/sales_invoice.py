import frappe
from datetime import datetime, timedelta
from frappe.utils import get_last_day


def before_validate(self, method):
    # copying Items Table to Invoice Item table
    self.invoice_item = []
    duplicate_row = {}
    for row in self.items:
        duplicate_row = {}
        for key in row.__dict__:
            duplicate_row[key] = row.get(key)
        duplicate_row['name'] = None

    if duplicate_row:
        self.append("invoice_item", duplicate_row)
    # update_si_data(self)
    # update_payment_terms(self)


def update_si_data(self):
    self.is_customer_metal = False
    self.is_customer_diamond = False
    for row in self.items:
        if row.bom:
            bom_doc = frappe.get_doc("BOM", row.bom)

            for i in bom_doc.metal_detail + bom_doc.finding_detail:
                if i.is_customer_item:
                    self.is_customer_metal = True
            for i in bom_doc.diamond_detail:
                if i.is_customer_item:
                    self.is_customer_diamond = True
            row.metal_amount = bom_doc.gold_bom_amount
            row.making_amount = bom_doc.making_charge
            row.finding_amount = bom_doc.finding_bom_amount
            row.diamond_amount = bom_doc.diamond_bom_amount
            row.gemstone_amount = bom_doc.gemstone_bom_amount
            row.custom_certification_amount = bom_doc.certification_amount
            row.custom_freight_amount = bom_doc.freight_amount
            row.custom_hallmarking_amount = bom_doc.hallmarking_amount
            row.custom_custom_duty_amount = bom_doc.custom_duty_amount

            row.rate = (
                row.metal_amount
                + row.making_amount
                + row.finding_amount
                + row.diamond_amount
                + row.gemstone_amount
                + row.custom_certification_amount
                + row.custom_freight_amount
                + row.custom_hallmarking_amount
                + row.custom_custom_duty_amount
            )


def update_payment_terms(self):
    custom_term = None
    try:
        custom_term = frappe.get_doc(
            "Customer Payment Terms", {"customer": self.customer}
        )
    except:
        custom_term = None

    payment_term_dict = {}

    if custom_term:
        for row in custom_term.customer_payment_details:
            if not payment_term_dict.get(row.payment_term):
                payment_term_dict.update(
                    {
                        row.payment_term: {
                            "item_type": [row.item_type],
                            "due_days": row.due_days,
                            "due_date_based_on": row.due_date_based_on,
                        }
                    }
                )
            else:
                payment_term_dict[row.payment_term]["item_type"].append(row.item_type)

    total_metal_amount = 0
    total_making_amount = 0
    total_finding_amount = 0
    total_diamond_amount = 0
    total_gemstone_amount = 0

    if payment_term_dict:
        for row in self.items:
            if row.bom:
                total_metal_amount += (row.metal_amount + row.custom_custom_duty_amount + row.custom_hallmarking_amount + row.custom_freight_amount + row.custom_certification_amount)
                total_making_amount += row.making_amount
                total_finding_amount += row.finding_amount
                total_diamond_amount += row.diamond_amount
                total_gemstone_amount += row.gemstone_amount

    self.payment_schedule = []
    if custom_term:
        due_date = None
        self.payment_terms_template = None
        for row in payment_term_dict:
            payment_amount = 0

            description = ""
            for item_type in payment_term_dict[row]["item_type"]:
                if item_type in ["Making Charges", "Labour Charges"]:
                    if item_type == "Making Charges" and not self.is_customer_metal:
                        payment_amount += total_making_amount
                        payment_amount += self.total_taxes_and_charges
                        description += item_type
                    elif item_type != "Making Charges" and self.is_customer_metal:
                        payment_amount += total_making_amount
                        payment_amount += self.total_taxes_and_charges
                        description += item_type
                elif item_type == "Studded Metal":
                    payment_amount += total_metal_amount
                    description += item_type
                elif item_type in ["Studded Diamond", "Handling Charges"]:
                    if item_type == "Studded Diamond" and not self.is_customer_diamond:
                        payment_amount += total_diamond_amount
                        description += item_type
                    elif self.is_customer_diamond and item_type != "Studded Diamond":
                        payment_amount += total_diamond_amount
                        description += item_type
                elif item_type == "Studded Gemstone":
                    payment_amount += total_gemstone_amount
                    description += item_type

            if (
                payment_term_dict[row]["due_date_based_on"]
                == "Day(s) after invoice date"
            ):
                due_date = datetime.strptime(self.posting_date, "%Y-%m-%d") + timedelta(
                    days=int(payment_term_dict[row]["due_days"])
                )

            elif (
                payment_term_dict[row]["due_date_based_on"]
                == "Day(s) after the end of the invoice month"
            ):
                posting_date = get_last_day(self.posting_date)
                due_date = datetime.strptime(posting_date, "%Y-%m-%d") + timedelta(
                    days=int(payment_term_dict[row]["due_days"])
                )

            if payment_amount > 0:
                self.append(
                    "payment_schedule",
                    {
                        "due_date": due_date,
                        "description": description,
                        "payment_term": row,
                        "payment_amount": payment_amount,
                        "invoice_portion": (payment_amount / self.grand_total) * 100,
                    },
                )
