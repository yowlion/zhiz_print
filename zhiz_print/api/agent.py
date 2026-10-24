# -*- coding: utf-8 -*-
# Copyright (c) 2026, Zhiz Print and contributors
# For license information, please see license.txt

# Channel A agent API (scan-print plan 6). The Windows agent (ZhizPrintAgent)
# authenticates with the Station HMAC (api_key + timestamp + sig, shared
# implementation in scan_print.authenticate_station) and uses five calls:
# register / poll / ack / heartbeat / download. Poll claims jobs atomically
# (ready -> sent per-row UPDATE guarded by affected-rows) so two concurrent
# agents can never print the same job. Large PDFs are never inlined - the
# poll response carries a short-TTL download token that renews on every
# fetch so a dropped link can resume with a Range request.

from __future__ import unicode_literals

import os

import frappe
from frappe.utils import cint

from zhiz_print.api.scan_print import (
    _load_job,
    authenticate_station,
    effective_user,
)

DEFAULT_POLL_INTERVAL = 3
MAX_POLL_LIMIT = 20
DEFAULT_INLINE_THRESHOLD = 2097152


def _server_time():
    return frappe.utils.now_datetime().strftime("%Y-%m-%d %H:%M:%S")


def _agent_setting(name, default):
    value = frappe.db.get_single_value("Zprint Setting", name)
    if value in (None, ""):
        return default
    return value


def _min_agent_version():
    return (_agent_setting("min_agent_version", "") or "").strip()


def _version_below(actual, minimum):
    """True when actual < minimum; unparseable versions never block."""
    def _parts(value):
        parts = []
        for chunk in str(value or "").split("."):
            digits = ""
            for ch in chunk.strip():
                if ch.isdigit():
                    digits += ch
                else:
                    break
            if not digits:
                return None
            parts.append(int(digits))
        return tuple(parts) if parts else None
    want = _parts(minimum)
    have = _parts(actual)
    if not want or not have:
        return False
    return have < want


def _upsert_agent(station, agent_id, agent_version, printers_status, queue_depth):
    """Create or refresh the Zprint Agent row for this station."""
    agent_id = (agent_id or "").strip() or ("agent-{0}".format(station.name))
    doc = frappe.db.get_value("Zprint Agent", agent_id,
                              ["name", "station"], as_dict=True)
    minimum = _min_agent_version()
    status = "online"
    if minimum and _version_below(agent_version, minimum):
        status = "version_low"
    payload = {
        "status": status,
        "agent_version": (agent_version or "").strip()[:40],
        "last_heartbeat": frappe.utils.now_datetime(),
        "queue_depth": cint(queue_depth) or 0,
        "station": station.name,
    }
    if printers_status:
        payload["printers_status"] = str(printers_status)[:5000]
    if doc:
        if doc.station and doc.station != station.name:
            frappe.throw(_("Agent ID {0} is bound to another station").format(agent_id),
                         frappe.PermissionError)
        frappe.db.set_value("Zprint Agent", agent_id, payload, update_modified=True)
    else:
        payload.update({"doctype": "Zprint Agent", "agent_id": agent_id})
        doc = frappe.get_doc(payload)
        doc.insert(ignore_permissions=True)
    return agent_id, status, minimum


@frappe.whitelist(allow_guest=True)
def agent_register(agent_id=None, agent_version=None, printers_status=None,
                   queue_depth=None):
    """Agent login/heartbeat bootstrap; returns the operating config."""
    station = authenticate_station(allow_session=False)
    resolved_id, status, minimum = _upsert_agent(
        station, agent_id, agent_version, printers_status, queue_depth)
    frappe.db.commit()
    return {
        "ok": 1,
        "agent_id": resolved_id,
        "status": status,
        "min_agent_version": minimum,
        "poll_interval_sec": cint(_agent_setting("poll_interval_sec", DEFAULT_POLL_INTERVAL))
        or DEFAULT_POLL_INTERVAL,
        "pdf_inline_threshold_bytes": cint(
            _agent_setting("pdf_inline_threshold_bytes", DEFAULT_INLINE_THRESHOLD))
        or DEFAULT_INLINE_THRESHOLD,
        "scan_print_enabled": cint(_agent_setting("scan_print_enabled", 0)),
        "station_user": effective_user(station) or "",
        "server_time": _server_time(),
    }


@frappe.whitelist(allow_guest=True)
def agent_poll(limit=None):
    """Claim and return printable jobs (atomic ready -> sent).

    Jobs above the inline threshold are delivered as a pdf_url with a
    short-TTL token (print_files.make_pdf_token); the token stays valid
    for its TTL so a flaky link can resume with a Range request.
    """
    station = authenticate_station(allow_session=False)
    limit = min(max(cint(limit) or 10, 1), MAX_POLL_LIMIT)
    threshold = cint(_agent_setting("pdf_inline_threshold_bytes", DEFAULT_INLINE_THRESHOLD)) \
        or DEFAULT_INLINE_THRESHOLD

    rows = frappe.get_all(
        "Zprint Print Job",
        filters={"station": station.name, "channel": "A", "status": "ready"},
        fields=["name"],
        order_by="creation asc",
        limit=limit,
    )
    jobs = []
    from zhiz_print.zhiz_print.print_channels import update_job

    for row in rows:
        # Atomic claim: the UPDATE only lands while the row is still ready;
        # a racing poller's claim flips the rowcount to 0 and we skip.
        frappe.db.sql(
            "UPDATE `tabZprint Print Job` SET status = 'sent', dispatched_on = %s "
            "WHERE name = %s AND status = 'ready'",
            (frappe.utils.now_datetime(), row.name),
        )
        claimed = getattr(frappe.db._cursor, "rowcount", 0)
        if not claimed:
            continue
        job = _load_job(row.name)
        if not job:
            continue
        update_job(row.name, {"ack_code": None}, commit=False)
        entry = {
            "job_id": job.name,
            "ref_doctype": job.ref_doctype or "",
            "ref_docname": job.ref_docname or "",
            "design_name": job.design_name or "",
            "copies": 1,
            "pdf_sha256": job.pdf_sha256 or "",
            "file_name": "{0}.pdf".format(job.name),
        }
        size = _pdf_size(job)
        entry["pdf_bytes"] = size
        if job.pdf_data and size <= threshold:
            entry["pdf_b64"] = job.pdf_data
        else:
            from zhiz_print.api.print_files import make_pdf_token
            token = make_pdf_token(job.name)
            entry["pdf_url"] = "{0}/api/method/zhiz_print.api.print_files.fetch_pdf?token={1}".format(
                frappe.utils.get_url().rstrip("/"), token)
        jobs.append(entry)
    frappe.db.commit()
    return {
        "jobs": jobs,
        "poll_interval_sec": cint(_agent_setting("poll_interval_sec", DEFAULT_POLL_INTERVAL))
        or DEFAULT_POLL_INTERVAL,
        "min_agent_version": _min_agent_version(),
        "server_time": _server_time(),
    }


def _pdf_size(job):
    """Artifact size in bytes whether inline (base64) or a private file."""
    if job.pdf_data:
        return len(job.pdf_data) * 3 // 4
    if job.pdf_file:
        try:
            return os.path.getsize(job.pdf_file)
        except OSError:
            return 0
    return 0


@frappe.whitelist(allow_guest=True)
def agent_ack(job_id=None, exit_code=None, detail=None):
    """Print result report; idempotent for already-terminal jobs."""
    station = authenticate_station(allow_session=False)
    job = _load_job((job_id or "").strip())
    if not job:
        frappe.throw(_("Unknown job"), frappe.DoesNotExistError)
    if job.station != station.name:
        frappe.throw(_("Job belongs to another station"), frappe.PermissionError)
    code = str(exit_code if exit_code is not None else "").strip()
    if not code.lstrip("-").isdigit():
        frappe.throw(_("Exit code must be an integer"))
    terminal = ("printed_queue_ok", "job_state_final", "printed_vendor_confirmed",
                "failed", "unknown", "expired")
    if job.status in terminal:
        # Idempotent exit: a retried ack must not rewrite the outcome.
        return {"ok": 1, "already_final": 1, "status": job.status,
                "server_time": _server_time()}
    from zhiz_print.zhiz_print.print_channels.agent import apply_ack
    apply_ack(job, int(code), (detail or "").strip()[:500])
    return {"ok": 1, "server_time": _server_time()}


@frappe.whitelist(allow_guest=True)
def agent_heartbeat(agent_id=None, agent_version=None, printers_status=None,
                    queue_depth=None):
    """Keepalive; returns poll cadence and any pending commands (v1: none)."""
    station = authenticate_station(allow_session=False)
    _upsert_agent(station, agent_id, agent_version, printers_status, queue_depth)
    frappe.db.commit()
    return {
        "commands": [],
        "poll_interval_sec": cint(_agent_setting("poll_interval_sec", DEFAULT_POLL_INTERVAL))
        or DEFAULT_POLL_INTERVAL,
        "min_agent_version": _min_agent_version(),
        "scan_print_enabled": cint(_agent_setting("scan_print_enabled", 0)),
        "server_time": _server_time(),
    }


@frappe.whitelist(allow_guest=True)
def agent_download(token=None):
    """Agent large-PDF fetch; delegates to the short-TTL token endpoint."""
    from zhiz_print.api.print_files import fetch_pdf
    return fetch_pdf(token=token)
