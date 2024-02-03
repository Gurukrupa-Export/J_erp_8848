// Copyright (c) 2024, Nirali and contributors
// For license information, please see license.txt

frappe.ui.form.on("Sales Conversion", {
	// refresh: function(frm) {
	// },
	setup(frm) {
		set_Metal_filter(frm, "source_item");
		set_Metal_filter(frm, "target_item");
		set_alloy_filter(frm, "source_alloy");
		set_alloy_filter(frm, "target_alloy");
	},
	source_item(frm) {
		clear_field(frm);
		frm.set_value("source_qty", null);
	},
	source_qty(frm) {
		clear_field(frm);
	},
	target_item(frm) {
		// Calculate Metal
		calculate_metal(frm);
	},
});
function set_Metal_filter(frm, field_name) {
	frm.set_query(field_name, function () {
		return {
			filters: {
				variant_of: "M",
			},
		};
	});
}
function set_alloy_filter(frm, field_name) {
	frm.set_query(field_name, function () {
		return {
			filters: {
				item_group: "Alloy",
			},
		};
	});
}

function calculate_metal(frm) {
	if (frm.doc.target_item) {
		frappe.call({
			method: "calculate_metal_conversion",
			doc: frm.doc,
			args: {
				docname: frm.doc.name,
			},
			callback: function (r) {
				if (r.message) {
					frm.set_value("target_qty", r.message[0]);
					frm.refresh_field("target_qty");

					if (r.message[1] < 0) {
						frm.set_value("target_alloy_check", 1);
						frm.refresh_field("target_alloy_check");
						frm.set_value("target_alloy_qty", Math.abs(r.message[1]));
						frm.refresh_field("target_alloy_qty");
						frm.save();
					} else if (r.message[1] > 0) {
						frm.set_value("source_alloy_check", 1);
						frm.refresh_field("source_alloy_check");
						frm.set_value("source_alloy_qty", r.message[1]);
						frm.refresh_field("source_alloy_qty");
						frm.save();
					} else {
						frm.set_value("target_alloy_check", 0);
						frm.refresh_field("target_alloy_check");
						frm.set_value("source_alloy_check", 0);
						frm.refresh_field("source_alloy_check");
						frappe.show_alert(
							{
								message: __(
									"Alloy Selection Invisible Due to Calculation is <b>0</b>"
								),
								indicator: "green",
							},
							5
						);
					}
				}
			},
		});
	}
}
function clear_field(frm) {
	frm.set_value("target_item", null);
	frm.set_value("target_qty", null);
	frm.set_value("source_alloy", null);
	frm.set_value("target_alloy", null);
	frm.set_value("source_alloy_qty", null);
	frm.set_value("target_alloy_qty", null);
	frm.set_value("source_alloy_check", "0");
	frm.set_value("target_alloy_check", "0");
	frm.save();
}
