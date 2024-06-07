import frappe

def product_ratio(self):
	gold_wt=0.0
	for gold in self.metal_detail:
		if gold.metal_type == "Gold":
			gold_wt+=gold.quantity
	self.metal_to_diamond_ratio_excl_of_finding = gold_wt