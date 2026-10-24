# -*- coding: utf-8 -*-
# Copyright (c) 2026, Zhiz Print and contributors
# For license information, please see license.txt

# Cloud channel contract (scan-print plan 4). Every vendor adapter exposes
# the same surface so the dispatcher and the receipt harvester stay vendor
# agnostic. Vendor wire contracts (field names/status enums) are marked
# POC-ADJUST: the Liankenet endpoint has no versioned official docs (plan
# one-vote-veto point) and Xpyun text/label contracts need the first real
# device pass; parse defensively and log raw payloads on surprises.

from __future__ import unicode_literals

import abc
import hashlib

import frappe

# Normalized vendor states used by cloud/__init__.py harvest logic.
STATE_PENDING = "pending"          # accepted, not yet on the device
STATE_PRINTING = "printing"        # device is printing
STATE_CONFIRMED = "confirmed"      # vendor cloud confirmed the print task
STATE_PRINTED = "printed"          # device reported paper out
STATE_FAILED = "failed"


class CloudChannel(object, metaclass=abc.ABCMeta):
    """Abstract base for vendor cloud-print adapters."""

    # Capability declaration bits (plan 4): what this adapter can carry.
    capabilities = {
        "pdf": False,
        "text_markup": False,
    }

    @abc.abstractmethod
    def send(self, device, printer_ctx, job, payload):
        """Submit one print task; returns the vendor task id (string).

        payload: {"kind": "pdf", "bytes": ...} or
                 {"kind": "text_markup", "content": ...}
        Raises on any vendor rejection / network failure.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def query_status(self, device, printer_ctx, vendor_job_id):
        """Normalize one vendor task to (state, raw_status_string)."""
        raise NotImplementedError

    @abc.abstractmethod
    def query_device(self, device, printer_ctx=None):
        """Device health snapshot dict (online/error-limit/expire)."""
        raise NotImplementedError

    def parse_callback(self, payload, secret):
        """Optional guest callback verification; default rejects. Callback
        mode is off by default - polling is the receipt path (plan 4
        receipt dual-mode)."""
        raise NotImplementedError("Vendor callback is not configured")

    def reset_error_limit(self, device, printer_ctx=None):
        """Optional: clear a vendor-side consecutive-failure lock."""
        return False

    @staticmethod
    def vendor_origin_id(job):
        """Vendor-side idempotency key (plan 4 accident-grade fix).

        NOT scan_code|station: that key would make the vendor cloud silently
        swallow a legit minutes-later reprint. The ERP-side job primary key
        (which already carries the dedup window) plus a fresh nonce is the
        right unit: retries of the SAME submission dedupe, new scans do not.
        """
        nonce = getattr(job, "origin_nonce", None)
        if not nonce:
            nonce = frappe.utils.random_string(16)
            frappe.db.set_value("Zprint Print Job", job.name,
                                "origin_nonce", nonce, update_modified=False)
            job.origin_nonce = nonce
        payload = "{0}|{1}|{2}".format(job.scan_code, job.station, job.name + nonce)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
