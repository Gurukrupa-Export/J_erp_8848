import frappe


def product_ratio(self):
	gold_wt = 0.0
	for gold in self.metal_detail:
		if gold.metal_type == "Gold":
			gold_wt += gold.quantity
	self.metal_to_diamond_ratio_excl_of_finding = gold_wt


def update_specifications(self):
	if not self.metal_type_:
		self.metal_type_ = self.metal_detail[0].metal_type if self.metal_detail else None

	if not self.metal_touch:
		self.metal_touch = self.metal_detail[0].metal_touch if self.metal_detail else None

	if not self.metal_colour:
		self.metal_colour = self.metal_detail[0].metal_colour if self.metal_detail else None

	if not self.metal_purity:
		self.metal_purity = self.metal_detail[0].metal_purity if self.metal_detail else None

	# if not self.finding_category:
	# 	self.finding_category = self.finding_detail[0].finding_category if self.finding_detail else None

	# if not self.finding_metal_type:
	# 	self.finding_metal_type = self.finding_detail[0].metal_type if self.finding_detail else None

	# if not self.finding_metal_touch:
	# 	self.finding_metal_touch = self.finding_detail[0].metal_touch if self.finding_detail else None

	# if not self.finding_metal_colour:
	# 	self.finding_metal_colour = self.finding_detail[0].metal_colour if self.finding_detail else None

	# if not self.finding_metal_purity:
	# 	self.finding_metal_purity = self.finding_detail[0].metal_purity if self.finding_detail else None

	# if not self.finding_subcategory:
	# 	self.finding_subcategory = self.finding_detail[0].finding_type if self.finding_detail else None

	if not self.diamond_grade:
		self.diamond_grade = self.diamond_detail[0].diamond_grade if self.diamond_detail else None

	# if not self.diamond_type:
	# 	self.diamond_type = self.diamond_detail[0].diamond_type if self.diamond_detail else None

	# if not self.stone_shape:
	# 	self.stone_shape = self.diamond_detail[0].stone_shape if self.diamond_detail else None

	if not self.sub_setting_type1:
		self.sub_setting_type1 = self.diamond_detail[0].sub_setting_type if self.diamond_detail else None

	if not self.diamond_quality:
		self.diamond_quality = self.diamond_detail[0].quality if self.diamond_detail else None

	if not self.qty:
		self.qty = self.diamond_detail[0].pcs if self.diamond_detail else None

	for idx, row in enumerate(self.gemstone_detail):
		if idx == 0:
			if not self.gemstone_type:
				self.gemstone_type = row.gemstone_type
			if not self.gemstone_shape:
				self.gemstone_shape = row.stone_shape
			if not self.gemstone_quality:
				self.gemstone_quality = row.gemstone_quality
			if not self.gemstone_size:
				self.gemstone_size = row.gemstone_size
			if not self.gemstone_grade:
				self.gemstone_grade = row.gemstone_grade
			if not self.cut_or_cab:
				self.cut_or_cab = row.cut_or_cab
		elif not self.get(f"gemstone_type{idx}") and idx <= 8:
			variable = f"gemstone_type{idx}"
			self.db_set(variable, row.gemstone_type)

	for idx, row in enumerate(self.other_detail):
		if idx < 2:
			if not self.get(f"custom_other_item_{idx + 1}"):
				self.db_set(f"custom_other_item_{idx + 1}", row.item_code)
			if not self.get(f"custom_other_wt_{idx + 1}"):
				self.db_set(f"custom_other_wt_{idx + 1}", row.quantity)

	if not self.gemstone_type:
		self.gemstone_type = self.gemstone_detail[0].gemstone_type if self.gemstone_detail else None
