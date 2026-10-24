# -*- coding: utf-8 -*-
# Copyright (c) 2026
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class ZprintScanEvent(Document):
    def before_insert(self):
        if not self.event_time:
            self.event_time = frappe.utils.now()
