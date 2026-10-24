# -*- coding: utf-8 -*-
# Copyright (c) 2026, Zhiz Print and contributors
# For license information, please see license.txt

# Channel B implementation: direct IPP/1.1 to a LAN network printer
# (scan-print plan 3). Contains a minimal self-contained IPP client built on
# the standard library only (struct + urllib): Print-Job with
# document-format=application/pdf and a top-level media, Get-Job-Attributes
# for receipt harvesting, Get-Printer-Attributes for the capability snapshot
# the desk validates against. Deliberately NOT implemented: Digest auth, TLS,
# media-col, IPP/2.x extensions (plan 3 scope cut).
#
# Every network call is bounded by printer.timeout (default 10s); dispatch is
# always enqueued so a dead printer can never block a scan response.

from __future__ import unicode_literals

import base64
import struct
import urllib.request

import frappe
from frappe.utils import cint

IPP_VERSION = b"\x01\x01"
OP_PRINT_JOB = 0x0002
OP_GET_JOB_ATTRIBUTES = 0x0009
OP_GET_PRINTER_ATTRIBUTES = 0x000B

TAG_OPERATION_GROUP = 0x01
TAG_JOB_GROUP = 0x02
TAG_END = 0x03

TAG_INTEGER = 0x21
TAG_BOOLEAN = 0x22
TAG_ENUM = 0x23
TAG_KEYWORD = 0x44
TAG_URI = 0x45
TAG_NAME_WITHOUT_LANG = 0x42
TAG_CHARSET = 0x47
TAG_NATURAL_LANGUAGE = 0x48
TAG_MIME_TYPE = 0x49

JOB_STATE_PENDING = 3
JOB_STATE_PENDING_HELD = 4
JOB_STATE_PROCESSING = 5
JOB_STATE_PROCESSING_STOPPED = 6
JOB_STATE_CANCELED = 7
JOB_STATE_ABORTED = 8
JOB_STATE_COMPLETED = 9

JOB_STATE_NAMES = {
    JOB_STATE_PENDING: "pending",
    JOB_STATE_PENDING_HELD: "pending-held",
    JOB_STATE_PROCESSING: "processing",
    JOB_STATE_PROCESSING_STOPPED: "processing-stopped",
    JOB_STATE_CANCELED: "canceled",
    JOB_STATE_ABORTED: "aborted",
    JOB_STATE_COMPLETED: "completed",
}

TERMINAL_JOB_STATES = (JOB_STATE_CANCELED, JOB_STATE_ABORTED, JOB_STATE_COMPLETED)

HARVEST_MAX_AGE_MINUTES = 30


# ---------------------------------------------------------------------------
# Minimal IPP/1.1 wire client
# ---------------------------------------------------------------------------

def _attr(tag, name, value_bytes):
    out = struct.pack(">B", tag)
    name_bytes = name.encode("utf-8") if name else b""
    out += struct.pack(">H", len(name_bytes)) + name_bytes
    out += struct.pack(">H", len(value_bytes)) + value_bytes
    return out


def _int_value(value):
    return struct.pack(">i", int(value))


def _build_request(operation, request_id, operation_attrs, job_attrs=None,
                   requested_attributes=None, data=b""):
    body = IPP_VERSION + struct.pack(">H", operation) + struct.pack(">I", request_id)
    body += struct.pack(">B", TAG_OPERATION_GROUP)
    for tag, name, value in operation_attrs:
        if isinstance(value, int):
            body += _attr(tag, name, _int_value(value))
        else:
            body += _attr(tag, name, str(value).encode("utf-8"))
    if requested_attributes:
        values = list(requested_attributes)
        body += _attr(TAG_KEYWORD, "requested-attributes", values[0].encode("utf-8"))
        for extra in values[1:]:
            body += _attr(TAG_KEYWORD, "", extra.encode("utf-8"))
    if job_attrs:
        body += struct.pack(">B", TAG_JOB_GROUP)
        for tag, name, value in job_attrs:
            body += _attr(tag, name, str(value).encode("utf-8"))
    body += struct.pack(">B", TAG_END)
    return body + data


def _parse_response(raw):
    """Parse status code + flat attribute dict from an IPP response.

    Returns (status_code, attrs) where attrs maps attribute name to the last
    value (or a list when the printer sent multiple values). IPP repeats an
    attribute value with an EMPTY name; those are folded into the previous
    attribute via the last_name tracker.
    """
    if len(raw) < 8:
        raise ValueError("IPP response too short")
    status_code = struct.unpack(">H", raw[2:4])[0]
    attrs = {}
    last_name = ""
    i = 8
    total = len(raw)
    while i < total:
        tag = raw[i]
        i += 1
        if tag == TAG_END:
            break
        if tag < 0x10:
            continue  # group delimiter
        if i + 2 > total:
            break
        name_len = struct.unpack(">H", raw[i:i + 2])[0]
        i += 2
        name = raw[i:i + name_len].decode("utf-8", "ignore")
        i += name_len
        if i + 2 > total:
            break
        value_len = struct.unpack(">H", raw[i:i + 2])[0]
        i += 2
        value_bytes = raw[i:i + value_len]
        i += value_len
        if tag in (TAG_INTEGER, TAG_ENUM) and value_len == 4:
            value = struct.unpack(">i", value_bytes)[0]
        elif tag == TAG_BOOLEAN and value_len == 1:
            value = bool(value_bytes[0])
        else:
            value = value_bytes.decode("utf-8", "ignore")
        if name:
            last_name = name
        else:
            name = last_name
        if not name:
            continue
        if name in attrs:
            if isinstance(attrs[name], list):
                attrs[name].append(value)
            else:
                attrs[name] = [attrs[name], value]
        else:
            attrs[name] = value
    return status_code, attrs


def _request(printer_ctx, operation, operation_attrs, job_attrs=None,
             requested_attributes=None, data=b""):
    """One IPP round trip over HTTP POST; returns parsed (status, attrs)."""
    uri = printer_ctx["uri"]
    request_id = int(frappe.utils.now_datetime().strftime("%H%M%S%f")) % 2147483646 + 1
    body = _build_request(operation, request_id, operation_attrs, job_attrs,
                          requested_attributes, data)
    headers = {"Content-Type": "application/ipp"}
    if printer_ctx.get("auth_mode") == "basic" and printer_ctx.get("credential"):
        token = base64.b64encode(printer_ctx["credential"].encode("utf-8")).decode("ascii")
        headers["Authorization"] = "Basic " + token
    req = urllib.request.Request(uri, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=printer_ctx.get("timeout") or 10) as resp:
        raw = resp.read()
    return _parse_response(raw)


def _base_operation_attrs(printer_ctx, job_name=None):
    attrs = [
        (TAG_CHARSET, "attributes-charset", "utf-8"),
        (TAG_NATURAL_LANGUAGE, "attributes-natural-language", "en"),
        (TAG_URI, "printer-uri", printer_ctx["uri"]),
        (TAG_NAME_WITHOUT_LANG, "requesting-user-name", "zhiz_print"),
    ]
    if job_name:
        attrs.append((TAG_NAME_WITHOUT_LANG, "job-name", job_name[:120]))
    return attrs


def submit_print_job(printer_ctx, pdf_bytes, job_name, user="zhiz_print"):
    """IPP Print-Job with application/pdf; copies are never sent (device
    copies are banned: merging happens at render, plan 0)."""
    operation_attrs = _base_operation_attrs(printer_ctx, job_name)
    operation_attrs.append((TAG_MIME_TYPE, "document-format", "application/pdf"))
    job_attrs = []
    if printer_ctx.get("media"):
        job_attrs.append((TAG_KEYWORD, "media", printer_ctx["media"]))
    status, attrs = _request(
        printer_ctx, OP_PRINT_JOB, operation_attrs,
        job_attrs=job_attrs, data=pdf_bytes,
    )
    if status >= 0x0400:
        raise RuntimeError("IPP Print-Job failed with status 0x{0:04x}".format(status))
    job_id = attrs.get("job-id")
    job_state = attrs.get("job-state")
    return job_id, job_state


def get_job_state(printer_ctx, ipp_job_id):
    """Get-Job-Attributes; returns the numeric job-state."""
    operation_attrs = _base_operation_attrs(printer_ctx)
    operation_attrs.append((TAG_INTEGER, "job-id", int(ipp_job_id)))
    status, attrs = _request(
        printer_ctx, OP_GET_JOB_ATTRIBUTES, operation_attrs,
        requested_attributes=["job-state"],
    )
    if status >= 0x0400:
        raise RuntimeError("IPP Get-Job-Attributes failed with status 0x{0:04x}".format(status))
    return attrs.get("job-state")


def get_printer_attributes(printer_ctx):
    """Get-Printer-Attributes snapshot (capability probing / doctor)."""
    operation_attrs = _base_operation_attrs(printer_ctx)
    status, attrs = _request(
        printer_ctx, OP_GET_PRINTER_ATTRIBUTES, operation_attrs,
        requested_attributes=[
            "document-format-supported", "printer-state", "printer-make-and-model",
            "media-supported", "printer-is-accepting-jobs",
        ],
    )
    if status >= 0x0400:
        raise RuntimeError("IPP Get-Printer-Attributes failed with status 0x{0:04x}".format(status))
    return attrs


# ---------------------------------------------------------------------------
# Channel implementation
# ---------------------------------------------------------------------------

def dispatch(job, route_ctx):
    """Enqueue the IPP submit - never block the render worker on a printer."""
    frappe.enqueue(
        "zhiz_print.zhiz_print.print_channels.ipp.submit_job",
        queue="short",
        timeout=120,
        enqueue_after_commit=True,
        scan_job_name=job.name,
        route_ctx=route_ctx,
    )


def submit_job(scan_job_name, route_ctx=None):
    """Async IPP Print-Job; printer down => job failed + fallback hop."""
    from zhiz_print.api.scan_print import (
        _load_job, dispatch_or_fallback, update_log_receipt,
    )
    from zhiz_print.zhiz_print.print_channels import update_job

    job = _load_job(scan_job_name)
    if not job or job.status in ("failed", "unknown", "expired"):
        return
    route_ctx = route_ctx or _rebuild_route_ctx(job)
    printer_ctx = (route_ctx or {}).get("printer") or {}
    update_job(scan_job_name, {
        "status": "dispatched",
        "dispatched_on": frappe.utils.now_datetime(),
    }, commit=True)
    try:
        pdf_bytes = _job_pdf_bytes(job)
        vendor_job_id, job_state = submit_print_job(
            printer_ctx, pdf_bytes, job.name)
        # task_status lives on Super Print Log only - the job row has no
        # such column.
        update_job(scan_job_name, {
            "vendor_job_id": str(vendor_job_id or ""),
        }, commit=True)
        update_log_receipt(scan_job_name, "dispatched",
                           task_status=JOB_STATE_NAMES.get(job_state, str(job_state)),
                           vendor_task_id=str(vendor_job_id or ""))
    except Exception as exc:
        frappe.log_error("IPP submit failed for job {0}: {1}".format(scan_job_name, exc),
                         "scan_print ipp")
        dispatch_or_fallback(job, route_ctx,
                             "IPP submit failed: {0}".format(exc))
    frappe.db.commit()


def _rebuild_route_ctx(job):
    """Rebuild the routing envelope inside a worker from the job row."""
    return {
        "channel": job.channel,
        "printer": {"uri": "", "timeout": 10, "auth_mode": "none", "media": ""},
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


def harvest_ipp_states():
    """Scheduler tick (every minute): poll B jobs awaiting an IPP terminal
    state; jobs older than 30 minutes are abandoned to unknown."""
    from zhiz_print.api.scan_print import (
        _load_job, update_log_receipt, _scan_event,
    )
    from zhiz_print.zhiz_print.print_channels import update_job

    now = frappe.utils.now_datetime()
    rows = frappe.get_all(
        "Zprint Print Job",
        filters={
            "channel": "B",
            "status": "dispatched",
        },
        fields=["name", "dispatched_on"],
        limit=0,
    )
    for row in rows:
        dispatched_on = frappe.utils.get_datetime(row.dispatched_on) if row.dispatched_on else None
        if dispatched_on and (now - dispatched_on).total_seconds() > HARVEST_MAX_AGE_MINUTES * 60:
            update_job(row.name, {
                "status": "unknown",
                "receipt_level": "unknown",
                "finalized_on": now,
            }, commit=True)
            update_log_receipt(row.name, "unknown")
            frappe.log_error("IPP job {0} never reached a terminal state".format(row.name),
                             "scan_print ipp harvest")
            continue
        try:
            _harvest_one(row.name)
        except Exception:
            frappe.log_error(frappe.get_traceback(),
                             "scan_print ipp harvest failed for {0}".format(row.name))
    frappe.db.commit()


def _harvest_one(job_name):
    from zhiz_print.api.scan_print import _load_job, update_log_receipt
    from zhiz_print.zhiz_print.print_channels import update_job

    job = _load_job(job_name)
    if not job or job.status != "dispatched" or not job.vendor_job_id:
        return
    printer_ctx = _printer_ctx_for_job(job)
    state = get_job_state(printer_ctx, int(job.vendor_job_id))
    state_name = JOB_STATE_NAMES.get(state, str(state))
    if state == JOB_STATE_COMPLETED:
        update_job(job_name, {
            "status": "job_state_final",
            "receipt_level": "job_state_final",
            "finalized_on": frappe.utils.now_datetime(),
        }, commit=True)
        update_log_receipt(job_name, "job_state_final", task_status=state_name)
    elif state in (JOB_STATE_CANCELED, JOB_STATE_ABORTED):
        update_job(job_name, {
            "status": "failed",
            "receipt_level": "failed",
            "finalized_on": frappe.utils.now_datetime(),
        }, commit=True)
        update_log_receipt(job_name, "failed", task_status=state_name)
        frappe.log_error("IPP job {0} ended as {1}".format(job_name, state_name),
                         "scan_print ipp harvest")


def _printer_ctx_for_job(job):
    """Resolve the routed printer for a B job at harvest time."""
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
            "Zprint Printer", {"channel": "B", "enabled": 1}, "name", order_by="creation asc")
    if not printer_name:
        raise RuntimeError("No channel B printer resolvable for job {0}".format(job.name))
    from zhiz_print.api.scan_print import _resolve_printer
    return _resolve_printer("B", printer_name)
