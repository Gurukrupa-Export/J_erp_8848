frappe.ui.form.on("Material Request", {
	refresh(frm) {
		frm.trigger("get_items_from_customer_goods");
		if (frm.doc.material_request_type === "Material Transfer") {
			frm.add_custom_button(
				__("Material Transfer (In Transit)"),
				() => frm.events.make_in_transit_stock_entry(frm),
				__("Create")
			);
		}
	},
	make_in_transit_stock_entry(frm) {
		frappe.call({
			method: "jewellery_erpnext.jewellery_erpnext.doc_events.material_request.make_in_transit_stock_entry",
			args: {
				source_name: frm.doc.name,
				to_warehouse: frm.doc.set_warehouse,
				transfer_type: frm.doc.custom_transfer_type,
				pmo: frm.doc.manufacturing_order,
				mnfr: frm.doc.custom_manufacturer,
			},
			callback: function (r) {
				if (r.message) {
					let doc = frappe.model.sync(r.message);
					frappe.set_route("Form", doc[0].doctype, doc[0].name);
				}
			},
		});
	},
	validate(frm) {
		$.each(frm.doc.items || [], function (i, d) {
			d.custom_insurance_amount = flt(d.custom_insurance_rate) * flt(d.qty);
			d.serial_no = d.custom_serial_no;
		});
		frm.refresh_field("items");
	},
	get_items_from_customer_goods(frm) {
		console.log("test");
		if (frm.doc.docstatus === 0) {
			frm.add_custom_button(
				__("Customer Goods Received"),
				function () {
					erpnext.utils.map_current_doc({
						method: "jewellery_erpnext.jewellery_erpnext.doc_events.material_request.make_stock_in_entry",
						source_doctype: "Stock Entry",
						target: frm,
						date_field: "posting_date",
						setters: {
							stock_entry_type: "Customer Goods Received",
							purpose: "Material Receipt",
							_customer: frm.doc._customer,
							inventory_type: frm.doc.inventory_type,
						},
						get_query_filters: {
							docstatus: 1,
							purpose: "Material Receipt",
						},
						size: "extra-large",
					});
				},
				__("Get Items From")
			);
		} else {
			frm.remove_custom_button(__("Customer Goods Received"), __("Get Items From"));
		}
	},
});

frappe.ui.form.on("Material Request Item", {
	item_code(frm, cdt, cdn) {
		frm.trigger("custom_insurance_rate");
		let child = locals[cdt][cdn];
		frappe.db.get_value("Item", child.item_code, "item_group", function (r) {
			if (r.item_group == "Metal - V") {
				child.pcs = 1;
				frm.refresh_field("items");
			}
		});
	},

	custom_insurance_rate(frm, cdt, cdn) {
		var d = locals[cdt][cdn];
		d.custom_insurance_amount = flt(d.custom_insurance_rate) * flt(d.qty);
		console.log(d.custom_insurance_amount);
		frm.refresh_field("items");
	},
});
