# -*- coding: utf-8 -*-
# Copyright (c) 2026
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class ZprintPrinter(Document):
    def validate(self):
        self._validate_channel_b_capability()

    def _validate_channel_b_capability(self):
        # Hard gate from the plan: an IPP printer that does not advertise
        # application/pdf must never be enabled (thermal/GDI printers).
        if self.channel != "B" or not cint(self.enabled):
            return
        snapshot = (self.capability_snapshot or "").strip()
        if not snapshot:
            frappe.throw(
                _("Run Probe Capability before enabling: a channel B printer needs a capability snapshot"),
                frappe.ValidationError,
            )
        try:
            data = json.loads(snapshot)
        except ValueError:
            frappe.throw(_("Capability Snapshot is not valid JSON"), frappe.ValidationError)
        if not isinstance(data, dict):
            frappe.throw(
                _("Run Probe Capability before enabling: a channel B printer needs a capability snapshot"),
                frappe.ValidationError,
            )
        formats = data.get("document-format-supported") or data.get("document_format_supported") or []
        if isinstance(formats, str):
            formats = [formats]
        if formats and "application/pdf" not in formats:
            frappe.throw(
                _("Printer does not advertise application/pdf support and cannot be enabled"),
                frappe.ValidationError,
            )


@frappe.whitelist()
def probe_capability(printer):
    """Queue an IPP Get-Printer-Attributes probe; the snapshot fills in async.

    Works on a disabled printer on purpose: probing is how an operator
    verifies a printer BEFORE enabling it (the enable gate above refuses a
    channel B printer without a snapshot). The printer ctx is built directly
    from the row instead of the route resolver, which rejects disabled
    printers.
    """
    probe_printer = str(printer or "")
    if not probe_printer or not frappe.db.exists("Zprint Printer", probe_printer):
        frappe.throw(_("Printer {0} does not exist").format(probe_printer), frappe.ValidationError)
    if not frappe.has_permission("Zprint Printer", "write", doc=probe_printer):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    frappe.enqueue(
        "zhiz_print.zhiz_print.doctype.zprint_printer.zprint_printer._run_capability_probe",
        queue="short",
        timeout=120,
        enqueue_after_commit=True,
        probe_printer=probe_printer,
    )
    return {"ok": True, "message": _("Capability probe queued")}


def _run_capability_probe(probe_printer):
    """Worker: IPP Get-Printer-Attributes; stores the JSON snapshot."""
    from zhiz_print.zhiz_print.print_channels import ipp

    row = frappe.db.get_value(
        "Zprint Printer", probe_printer,
        ["name", "printer_logical_name", "uri", "auth_mode", "timeout"],
        as_dict=True,
    )
    if not row:
        return
    if not (row.uri or "").strip():
        frappe.throw("Channel B printer {0} has no URI".format(probe_printer))
    printer_ctx = {
        "name": row.name,
        "logical_name": row.printer_logical_name,
        "uri": (row.uri or "").strip(),
        "auth_mode": row.auth_mode or "none",
        "timeout": cint(row.timeout) or 10,
    }
    if printer_ctx["auth_mode"] == "basic":
        doc = frappe.get_doc("Zprint Printer", probe_printer)
        printer_ctx["credential"] = doc.get_password("credential", raise_exception=False) or ""
    try:
        attrs = ipp.get_printer_attributes(printer_ctx)
    except Exception:
        frappe.log_error(frappe.get_traceback(),
                         "scan_print capability probe failed for {0}".format(probe_printer))
        raise
    frappe.db.set_value(
        "Zprint Printer", probe_printer,
        {"capability_snapshot": json.dumps(attrs, indent=1, sort_keys=True, default=str)},
        update_modified=True,
    )
    frappe.db.commit()
