# -*- coding: utf-8 -*-
# Copyright (c) 2026
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class ZprintRouteRule(Document):
    def validate(self):
        self._check_channel_printer_pair(self.channel, self.printer, "Printer")
        if self.fallback_printer:
            if not self.fallback_channel:
                frappe.throw(
                    _("Fallback Channel is required when a Fallback Printer is set"),
                    frappe.ValidationError,
                )
            self._check_channel_printer_pair(self.fallback_channel, self.fallback_printer, "Fallback Printer")

    def _check_channel_printer_pair(self, channel, printer, printer_label):
        if not printer:
            return
        printer_channel = frappe.db.get_value("Zprint Printer", printer, "channel")
        if printer_channel and printer_channel != channel:
            frappe.throw(
                _("{0} does not belong to channel {1}").format(printer_label, channel),
                frappe.ValidationError,
            )
