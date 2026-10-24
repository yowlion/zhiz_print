# -*- coding: utf-8 -*-
# Copyright (c) 2026, Zhiz Print and contributors
# For license information, please see license.txt

# Maintenance helpers for zhiz_print background cleanup. Plain functions so
# they can be registered as scheduler events (hooks.py) or invoked ad hoc via
# bench execute. Every entry point is individually try/except wrapped: one
# broken sweep must never take down the other cron jobs in the same tick.

import os
import time

import frappe
from frappe.utils import cint

from zhiz_print.api.print_designer import _pdf_debug_dir
from zhiz_print.api.scan_print import (
    TERMINAL_STATUSES,
    _scan_event,
    update_log_receipt,
)

# A queued job is stale when nothing moved for 2x the render budget (worker
# died before starting, or render slots stayed saturated). A rendering job
# gets a longer window (native pdfkit has no timeout) before a retry is
# risked; the render dedup lock keeps a live worker exclusive anyway.
QUEUED_STALE_FACTOR = 2
RENDERING_STALE_FACTOR = 3
RENDERING_STALE_MIN = 120

# ready/sent are agent-side states; half an hour without movement means the
# agent died on its side of the spooler commit (printing takes seconds).
AGENT_STALE_SEC = 1800

RETENTION_DELETE_BATCH = 200
PURGE_BATCH = 1000
AGENT_OFFLINE_AFTER_SEC = 90


def _setting_int(name, default):
    return cint(frappe.db.get_single_value("Zprint Setting", name)) or default


def cleanup_pdf_debug_days():
    """Delete debug artifacts under private/files/pdf_debug older than
    Zprint Setting.job_retain_days (cint, default 30).

    Missing directory silently returns 0. Returns the number of removed files.
    """
    retain_days = _setting_int("job_retain_days", 30)
    debug_dir = _pdf_debug_dir(create=False)
    if not os.path.isdir(debug_dir):
        return 0

    cutoff = time.time() - retain_days * 86400
    removed = 0
    for name in os.listdir(debug_dir):
        path = os.path.join(debug_dir, name)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError:
            continue
    return removed


def expire_stale_jobs():
    """Per-minute state-machine janitor (plan 7).

    1. queued/rendering jobs that stopped moving: re-enqueue the render while
       retries remain, else fail the job honestly.
    2. dispatching jobs wedged by a worker death between the atomic claim and
       the channel transition: rewind to queued so the retry machinery can
       pick them up (render_job only enters on queued/rendering).
    3. ready jobs no agent ever claimed within AGENT_STALE_SEC: failed
       honestly (nothing was downloaded, so no paper risk).
    4. sent jobs the agent never acked within AGENT_STALE_SEC: outcome is
       genuinely unknown (the spooler commit may have happened) - no reprint.
    5. retention: terminal jobs past job_retain_days are deleted (bounded
       batch) together with their private PDF artifacts.
    """
    retried = failed = unknown = deleted = rewound = 0
    try:
        budget = _setting_int("scan_render_budget_s", 30)
        max_retry = _setting_int("max_retry", 2)
        now = frappe.utils.now_datetime()

        queued_cutoff = _ago(budget * QUEUED_STALE_FACTOR)
        rendering_cutoff = _ago(max(budget * RENDERING_STALE_FACTOR,
                                    budget + RENDERING_STALE_MIN))
        for status, cutoff in (("queued", queued_cutoff),
                               ("rendering", rendering_cutoff),
                               ("dispatching", rendering_cutoff)):
            rows = frappe.get_all(
                "Zprint Print Job",
                filters={"status": status, "modified": ["<", cutoff]},
                fields=["name", "retry_count"],
                order_by="creation asc",
                limit=50,
            )
            for row in rows:
                if status == "dispatching":
                    # Rewind only; the next tick handles it through the
                    # queued branch (retry accounting stays in one place).
                    frappe.db.set_value(
                        "Zprint Print Job", row.name,
                        {"status": "queued"}, update_modified=True)
                    rewound += 1
                    continue
                if cint(row.retry_count) < max_retry:
                    frappe.db.set_value(
                        "Zprint Print Job", row.name,
                        {"retry_count": cint(row.retry_count) + 1},
                        update_modified=True)
                    frappe.enqueue(
                        "zhiz_print.api.scan_print.render_job",
                        queue="short",
                        timeout=budget * 2 + 120,
                        enqueue_after_commit=True,
                        scan_job_name=row.name,
                    )
                    retried += 1
                else:
                    frappe.db.set_value(
                        "Zprint Print Job", row.name,
                        {"status": "failed", "receipt_level": "failed",
                         "finalized_on": now},
                        update_modified=True)
                    failed += 1

        stale_ready = frappe.get_all(
            "Zprint Print Job",
            filters={"status": "ready",
                     "dispatched_on": ["<", _ago(AGENT_STALE_SEC)]},
            fields=["name", "station", "scan_code", "rule"],
            limit=50,
        )
        for row in stale_ready:
            frappe.db.set_value(
                "Zprint Print Job", row.name,
                {"status": "failed", "receipt_level": "failed",
                 "finalized_on": now},
                update_modified=True)
            update_log_receipt(row.name, "failed", task_status="stale")
            _scan_event("AgentOffline", station=row.station,
                        scan_value=row.scan_code, rule=row.rule,
                        message="Ready job {0} was never claimed by an "
                                "agent".format(row.name))
            frappe.log_error(
                "Scan job {0} was marked ready but never claimed by an "
                "agent".format(row.name), "scan_print stale ready")
            failed += 1

        stale_agent = frappe.get_all(
            "Zprint Print Job",
            filters={"status": "sent",
                     "dispatched_on": ["<", _ago(AGENT_STALE_SEC)]},
            fields=["name"],
            limit=50,
        )
        for row in stale_agent:
            frappe.db.set_value(
                "Zprint Print Job", row.name,
                {"status": "unknown", "receipt_level": "unknown",
                 "finalized_on": now},
                update_modified=True)
            update_log_receipt(row.name, "unknown", task_status="stale")
            frappe.log_error(
                "Scan job {0} was claimed by an agent but never acked".format(row.name),
                "scan_print stale sent")
            unknown += 1
        frappe.db.commit()
    except Exception:
        frappe.log_error(frappe.get_traceback(), "scan_print expire_stale_jobs")

    try:
        deleted = _retention_delete(RETENTION_DELETE_BATCH)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "scan_print retention delete")
    return {"retried": retried, "failed": failed, "unknown": unknown,
            "rewound": rewound, "deleted": deleted}


def mark_offline_agents():
    """Agents silent beyond 90s (30 polls at the 3s cadence) go offline."""
    try:
        cutoff = _ago(AGENT_OFFLINE_AFTER_SEC)
        stale = frappe.get_all(
            "Zprint Agent",
            filters={"status": "online", "last_heartbeat": ["<", cutoff]},
            fields=["name"],
            limit=0,
        )
        for row in stale:
            frappe.db.set_value("Zprint Agent", row.name,
                                {"status": "offline"}, update_modified=True)
        frappe.db.commit()
        return len(stale)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "scan_print mark_offline_agents")
        return 0


def purge_expired_jobs():
    """Daily full retention sweep (03:00 cron).

    Deletes terminal jobs past job_retain_days in larger batches (with their
    private PDFs), then removes orphaned scan_pdf artifacts no live job
    references anymore.
    """
    deleted = 0
    try:
        deleted = _retention_delete(PURGE_BATCH)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "scan_print purge jobs")
    purged_files = 0
    try:
        purged_files = _purge_orphan_pdfs()
    except Exception:
        frappe.log_error(frappe.get_traceback(), "scan_print purge orphan pdfs")
    return {"deleted_jobs": deleted, "purged_files": purged_files}


def _ago(seconds):
    from datetime import timedelta
    return frappe.utils.now_datetime() - timedelta(seconds=seconds)


# Hard ceiling for non-terminal jobs: every sweep above can miss a row (limit
# batches, exotic wedges), and retention only deletes terminal rows, so
# without this finalize step non-terminal rows (and their private PDFs)
# would survive forever. 7 days of no movement is far beyond any live
# render/dispatch/agent window; the honest receipt is "unknown", not a guess
# of printed/failed.
FORCE_FINALIZE_DAYS = 7


def _force_finalize_stale():
    """Finalize non-terminal jobs untouched for FORCE_FINALIZE_DAYS as
    unknown (receipt: unknown) so retention can eventually reclaim them."""
    try:
        cutoff = _ago(FORCE_FINALIZE_DAYS * 86400)
        rows = frappe.get_all(
            "Zprint Print Job",
            filters={"status": ["not in", list(TERMINAL_STATUSES)],
                     "modified": ["<", cutoff]},
            fields=["name"],
            limit=500,
        )
        for row in rows:
            frappe.db.set_value(
                "Zprint Print Job", row.name,
                {"status": "unknown", "receipt_level": "unknown",
                 "finalized_on": frappe.utils.now_datetime()},
                update_modified=True)
            update_log_receipt(row.name, "unknown", task_status="stale")
        if rows:
            frappe.db.commit()
        return len(rows)
    except Exception:
        frappe.log_error(frappe.get_traceback(),
                         "scan_print force finalize stale")
        return 0


def _retention_delete(batch):
    """Delete terminal jobs older than job_retain_days (max `batch` per call)
    together with their private PDF artifacts. Returns the deleted count."""
    _force_finalize_stale()
    retain_days = _setting_int("job_retain_days", 30)
    cutoff = _ago(retain_days * 86400)
    rows = frappe.get_all(
        "Zprint Print Job",
        filters={"status": ["in", list(TERMINAL_STATUSES)],
                 "modified": ["<", cutoff]},
        fields=["name", "pdf_file"],
        order_by="modified asc",
        limit=batch,
    )
    for row in rows:
        if row.pdf_file:
            try:
                if os.path.isfile(row.pdf_file):
                    os.remove(row.pdf_file)
            except OSError:
                pass
        frappe.db.delete("Zprint Print Job", row.name)
    if rows:
        frappe.db.commit()
    return len(rows)


def _purge_orphan_pdfs():
    """Remove scan_pdf files no live (non-terminal) job references."""
    from zhiz_print.api.print_files import scan_pdf_dir

    directory = scan_pdf_dir(create=False)
    if not os.path.isdir(directory):
        return 0
    live = set()
    rows = frappe.get_all(
        "Zprint Print Job",
        filters={"status": ["not in", list(TERMINAL_STATUSES)]},
        fields=["pdf_file"],
        limit=0,
    )
    for row in rows:
        if row.pdf_file:
            live.add(os.path.normpath(row.pdf_file))
    retain_days = _setting_int("job_retain_days", 30)
    cutoff = time.time() - retain_days * 86400
    removed = 0
    for name in os.listdir(directory):
        path = os.path.join(directory, name)
        try:
            if not os.path.isfile(path):
                continue
            if os.path.normpath(path) in live:
                continue
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError:
            continue
    return removed
