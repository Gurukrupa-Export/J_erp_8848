// Copyright (c) 2024, Nirali and contributors
// For license information, please see license.txt

frappe.ui.form.on("Serial Number Creator", {
	refresh: function (frm) {
		set_html(frm);
	},
});

frappe.ui.form.on("SNC FG Details", {
	refresh: function (frm, cdt, cdn) {
		var child = locals[cdt][cdn];
	},
});

function set_html(frm) {
	frappe.call({
		method: "get_serial_summary",
		doc: frm.doc,
		args: {
			docname: frm.doc.name,
		},
		callback: function (r) {
			frm.get_field("serial_summery").$wrapper.html(r.message);
		},
	});
	frappe.call({
		method: "get_bom_summary",
		doc: frm.doc,
		args: {
			docname: frm.doc.name,
		},
		callback: function (r) {
			frm.get_field("bom_summery").$wrapper.html(r.message);
		},
	});
}
