# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import frappe
from frappe.model.document import Document


class SuperPrintLog(Document):
	def before_insert(self):
		if not self.print_time:
			self.print_time = frappe.utils.now()
		if not self.print_user:
			self.print_user = frappe.session.user
		if not self.print_count:
			existing = frappe.db.count('Super Print Log', filters={
				'reference_doctype': self.reference_doctype,
				'reference_name': self.reference_name,
				'print_design': self.print_design
			})
			self.print_count = existing + 1
