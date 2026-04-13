# -*- coding: utf-8 -*-
# Copyright (c) 2026, Zhiz Super and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe import _
from frappe.model.document import Document


class SuperPrintPaper(Document):
	def validate(self):
		if self.width and self.width <= 0:
			frappe.throw(_("Paper width must be greater than 0"))
		if self.height and self.height <= 0:
			frappe.throw(_("Paper height must be greater than 0"))
		for field in ['margin_top', 'margin_bottom', 'margin_left', 'margin_right']:
			val = getattr(self, field, None)
			if val is not None and val < 0:
				frappe.throw(_("Margins cannot be negative"))
