# -*- coding: utf-8 -*-
# Copyright (c) 2026, Zhiz Print and contributors
# For license information, please see license.txt

# Channel C implementation: vendor cloud printers / print boxes (scan-print
# plan 4). dispatch() only enqueues; submit_job builds the payload per the
# printer's payload_kind (pdf -> LiankenetBox, text_markup -> Xpyun), submits
# with a vendor idempotency id, and harvest_vendor_receipts (scheduler) polls
# vendor state into the job/log receipt levels. Callbacks stay OFF - polling
# is the receipt path.

from __future__ import unicode_literals

import frappe
from frappe.utils import cint

from zhiz_print.zhiz_print.print_channels.cloud.base import (
    STATE_CONFIRMED,
    STATE_FAILED,
    STATE_PENDING,
    STATE_PRINTED,
    STATE_PRINTING,
)
from zhiz_print.zhiz_print.print_channels.cloud.liankenet import LiankenetChannel
from zhiz_print.zhiz_print.print_channels.cloud.xpyun import XpyunChannel

ADAPTERS = {
    "LiankenetBox": LiankenetChannel,
    "Xpyun": XpyunChannel,
}

DEFAULT_TEXT_TEMPLATE = "__scan_code__\n__docname__\n__date__"


def get_channel(device_type):
    """Resolve a Zprint Cloud Device device_type to its adapter instance."""
    cls = ADAPTERS.get(device_type)
    if not cls:
        frappe.throw("Unknown cloud device type: {0}".format(device_type))
    return cls()


# ---------------------------------------------------------------------------
# Dispatch (render worker side)
# ---------------------------------------------------------------------------

def dispatch(job, route_ctx):
    """Enqueue the vendor submit - never block the render worker on a WAN."""
    frappe.enqueue(
        "zhiz_print.zhiz_print.print_channels.cloud.submit_job",
        queue="short",
        timeout=180,
        enqueue_after_commit=True,
        scan_job_name=job.name,
        route_ctx=route_ctx,
    )


def submit_job(scan_job_name, route_ctx=None):
    """Async vendor submit; failure => one fallback hop or job failed."""
    from zhiz_print.api.scan_print import (
        _load_job,
        dispatch_or_fallback,
        update_log_receipt,
    )
    from zhiz_print.zhiz_print.print_channels import update_job

    job = _load_job(scan_job_name)
    if not job or job.status in ("failed", "unknown", "expired"):
        return
    route_ctx = route_ctx or _rebuild_route_ctx(job)
    printer_ctx = (route_ctx or {}).get("printer") or {}
    device = printer_ctx.get("device") or {}
    update_job(scan_job_name, {
        "status": "dispatched",
        "dispatched_on": frappe.utils.now_datetime(),
    }, commit=True)
    try:
        channel = get_channel(device.get("device_type"))
        payload = _build_payload(device, printer_ctx, job)
        _check_capabilities(channel, payload)
        vendor_job_id = channel.send(device, printer_ctx, job, payload)
        update_job(scan_job_name, {
            "status": "submitted_to_vendor",
            "vendor_job_id": str(vendor_job_id or ""),
        }, commit=True)
        update_log_receipt(scan_job_name, "submitted_to_vendor",
                           task_status="submitted", vendor_task_id=str(vendor_job_id or ""))
    except Exception as exc:
        frappe.log_error(
            "Cloud submit failed for job {0}: {1}".format(scan_job_name, exc),
            "scan_print cloud")
        dispatch_or_fallback(job, route_ctx,
                             "Cloud submit failed: {0}".format(exc),
                             params=route_ctx.get("params"))
    frappe.db.commit()


def _build_payload(device, printer_ctx, job):
    """Payload per payload_kind: raw PDF bytes or filled text template."""
    kind = printer_ctx.get("payload_kind") or "pdf"
    if kind == "pdf":
        return {"kind": "pdf", "bytes": _job_pdf_bytes(job)}
    template = frappe.db.get_value(
        "Zprint Cloud Device", device.get("name"), "text_template") \
        or DEFAULT_TEXT_TEMPLATE
    return {"kind": "text_markup",
            "content": build_text_content(template, job)}


def _check_capabilities(channel, payload):
    kind = payload.get("kind")
    if not channel.capabilities.get(kind):
        frappe.throw("Cloud adapter does not support payload kind {0}".format(kind))


def build_text_content(template, job):
    """Fill __scan_code__/__docname__/__date__ placeholders (plan 4)."""
    docname = job.ref_docname or ""
    date = frappe.utils.now_datetime().strftime("%Y-%m-%d %H:%M")
    return (template
            .replace("__scan_code__", job.scan_code or "")
            .replace("__docname__", docname)
            .replace("__date__", date))


def _rebuild_route_ctx(job):
    """Rebuild the routing envelope inside a worker from the job row."""
    return {
        "channel": "C",
        "printer": {"payload_kind": "pdf", "media": "", "device": {}},
        "fallback_channel": None,
        "fallback_printer": None,
        "station": job.station,
        "rule": "",
    }


def _job_pdf_bytes(job):
    import base64
    import os

    if job.pdf_data:
        return base64.b64decode(job.pdf_data)
    if job.pdf_file and os.path.isfile(job.pdf_file):
        with open(job.pdf_file, "rb") as f:
            return f.read()
    raise RuntimeError("Job {0} has no PDF artifact".format(job.name))


# ---------------------------------------------------------------------------
# Receipt harvesting (scheduler, every minute)
# ---------------------------------------------------------------------------

def harvest_vendor_receipts():
    """Poll channel C jobs awaiting vendor confirmation.

    "dispatched" rows are included so a worker death between the dispatched
    flip and submitted_to_vendor cannot strand the job forever: _harvest_one
    still skips them (no vendor_job_id yet) but the timeout branch reaps
    them. printed => printed_vendor_confirmed; vendor failure => failed;
    past vendor_confirm_timeout_sec without a terminal state => unknown +
    Error Log (paper state honestly unknown, never faked).
    """
    from zhiz_print.api.scan_print import _load_job, update_log_receipt

    now = frappe.utils.now_datetime()
    timeout = cint(frappe.db.get_single_value(
        "Zprint Setting", "vendor_confirm_timeout_sec")) or 600
    rows = frappe.get_all(
        "Zprint Print Job",
        filters={
            "channel": "C",
            "status": ["in", ("dispatched", "submitted_to_vendor",
                              "vendor_confirmed")],
        },
        fields=["name", "dispatched_on"],
        limit=0,
    )
    for row in rows:
        dispatched_on = frappe.utils.get_datetime(row.dispatched_on) if row.dispatched_on else None
        if dispatched_on and (now - dispatched_on).total_seconds() > timeout:
            _abandon_to_unknown(row.name)
            continue
        try:
            _harvest_one(row.name)
        except Exception:
            frappe.log_error(frappe.get_traceback(),
                             "scan_print cloud harvest failed for {0}".format(row.name))
    frappe.db.commit()


def _harvest_one(job_name):
    from zhiz_print.api.scan_print import _load_job, update_log_receipt

    job = _load_job(job_name)
    if not job or job.status not in ("submitted_to_vendor", "vendor_confirmed") \
            or not job.vendor_job_id:
        return
    channel, device, printer_ctx = _channel_for_job(job)
    state, raw_status = channel.query_status(device, printer_ctx, job.vendor_job_id)
    # task_status lives on Super Print Log only - the job row has no column.
    task_status = (raw_status or "")[:140]
    updates = {}
    if state == STATE_PRINTED:
        updates.update({
            "status": "printed_vendor_confirmed",
            "receipt_level": "printed_vendor_confirmed",
            "finalized_on": frappe.utils.now_datetime(),
        })
    elif state == STATE_FAILED:
        updates.update({
            "status": "failed",
            "receipt_level": "failed",
            "finalized_on": frappe.utils.now_datetime(),
        })
    elif state == STATE_CONFIRMED:
        updates.update({"status": "vendor_confirmed",
                        "receipt_level": "vendor_confirmed"})
    elif state == STATE_PRINTING and job.status == "submitted_to_vendor":
        updates.update({"status": "vendor_confirmed",
                        "receipt_level": "vendor_confirmed"})
    if updates:
        _update_job(job_name, updates)
        update_log_receipt(job_name,
                           updates.get("receipt_level", "submitted_to_vendor"),
                           task_status=task_status)
    if state == STATE_FAILED:
        frappe.log_error("Cloud job {0} reported failure ({1})".format(
            job_name, raw_status), "scan_print cloud harvest")


def _abandon_to_unknown(job_name):
    from zhiz_print.api.scan_print import update_log_receipt

    _update_job(job_name, {
        "status": "unknown",
        "receipt_level": "unknown",
        "finalized_on": frappe.utils.now_datetime(),
    })
    update_log_receipt(job_name, "unknown")
    frappe.log_error(
        "Cloud job {0} passed vendor_confirm_timeout without a terminal state"
        .format(job_name), "scan_print cloud harvest")


def _channel_for_job(job):
    """Resolve (adapter, device, printer_ctx) for a C job at harvest time."""
    from zhiz_print.api.scan_print import _resolve_printer

    log_row = frappe.db.get_value(
        "Super Print Log", {"job_name": job.name}, "printer_snapshot", as_dict=True)
    printer_name = None
    if log_row and log_row.printer_snapshot:
        import json as _json
        try:
            printer_name = (_json.loads(log_row.printer_snapshot) or {}).get("printer")
        except (ValueError, TypeError):
            printer_name = None
    if not printer_name:
        printer_name = frappe.db.get_value(
            "Zprint Printer", {"channel": "C", "enabled": 1}, "name",
            order_by="creation asc")
    if not printer_name:
        raise RuntimeError("No channel C printer resolvable for job {0}".format(job.name))
    printer_ctx = _resolve_printer("C", printer_name)
    device = printer_ctx.get("device") or {}
    return get_channel(device.get("device_type")), device, printer_ctx


def _update_job(job_name, updates):
    from zhiz_print.zhiz_print.print_channels import update_job

    update_job(job_name, updates, commit=True)
