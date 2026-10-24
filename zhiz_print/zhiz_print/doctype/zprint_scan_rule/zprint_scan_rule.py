# -*- coding: utf-8 -*-
# Copyright (c) 2026
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

MAX_PATTERN_LENGTH = 200


class ZprintScanRule(Document):
    def validate(self):
        self._validate_match_pattern()
        self._validate_sample_scan_value()

    def _validate_match_pattern(self):
        pattern = (self.match_pattern or "").strip()
        if not pattern:
            frappe.throw(_("Match Pattern is required"), frappe.ValidationError)
        # Hard input bound against pathological regex input (ReDoS surface).
        if len(pattern) > MAX_PATTERN_LENGTH:
            frappe.throw(
                _("Match Pattern must not exceed {0} characters").format(MAX_PATTERN_LENGTH),
                frappe.ValidationError,
            )
        if (self.match_mode or "Exact") == "Regex":
            try:
                re.compile(pattern)
            except re.error as exc:
                frappe.throw(
                    _("Invalid regex in Match Pattern: {0}").format(str(exc)),
                    frappe.ValidationError,
                )
        self.match_pattern = pattern

    def _validate_sample_scan_value(self):
        sample = (self.sample_scan_value or "").strip()
        if not sample:
            return
        if not self._matches(sample):
            frappe.throw(
                _("Sample Scan Value does not match this rule"),
                frappe.ValidationError,
            )

    def _matches(self, value):
        pattern = self.match_pattern or ""
        mode = self.match_mode or "Exact"
        if mode == "Regex":
            flags = 0 if cint(self.case_sensitive) else re.IGNORECASE
            return bool(re.fullmatch(pattern, value, flags))
        if not cint(self.case_sensitive):
            value = value.lower()
            pattern = pattern.lower()
        if mode == "Prefix":
            return value.startswith(pattern)
        return value == pattern
