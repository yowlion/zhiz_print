# -*- coding: utf-8 -*-
# Copyright (c) 2026
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class ZprintStation(Document):
    def validate(self):
        self._validate_pda_default_channel()

    def _validate_pda_default_channel(self):
        # Channel A relies on a shop-floor Windows print agent, which a PDA
        # station cannot host. Applies to client_type "pda" and "both".
        if self.client_type in ("pda", "both") and self.default_channel == "A":
            frappe.throw(
                _("Channel A is not allowed as default channel for PDA stations (client type {0})").format(
                    self.client_type
                ),
                frappe.ValidationError,
            )
