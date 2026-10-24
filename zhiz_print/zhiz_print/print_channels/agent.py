# -*- coding: utf-8 -*-
# Copyright (c) 2026, Zhiz Print and contributors
# For license information, please see license.txt

# Channel A implementation: the Windows print agent (ZhizPrintAgent).
# dispatch() only flips the job to "ready" - the agent claims it via
# api/agent.py agent_poll (atomic ready->sent), prints on the shop-floor
# PC, and reports the spooler exit code back through agent_ack.
# The trunk writes the Super Print Log row after dispatch succeeds.

from __future__ import unicode_literals

import frappe


def dispatch(job, route_ctx):
    """Mark the job ready for agent claim (receipt ceiling: spooler commit).

    Belt-and-suspenders on top of the trunk's _claim_dispatch gate: the
    guarded UPDATE only fires while the row is still in a pre-dispatch
    state, so a stale retry that lost the render race can never flip an
    already-finalized job back to ready (double paper).
    """
    frappe.db.sql(
        "UPDATE `tabZprint Print Job` SET channel = 'A', status = 'ready', "
        "dispatched_on = %s WHERE name = %s "
        "AND status IN ('queued', 'rendering', 'dispatching')",
        (frappe.utils.now_datetime(), job.name),
    )
    if not getattr(frappe.db._cursor, "rowcount", 0):
        frappe.log_error(
            "Job {0} skipped channel A dispatch: status moved past the "
            "pre-dispatch states".format(job.name), "scan_print dispatch race")
        return
    frappe.db.commit()


def apply_ack(job, exit_code, detail):
    """Agent exit-code mapping (api/agent.py agent_ack): 0 = committed to the
    OS spooler; 2-6 = concrete failure classes."""
    from zhiz_print.zhiz_print.print_channels import update_job
    from zhiz_print.api.scan_print import update_log_receipt, _scan_event

    code = int(exit_code)
    if code == 0:
        update_job(job.name, {
            "status": "printed_queue_ok",
            "ack_code": "ok",
            "receipt_level": "printed_queue_ok",
            "finalized_on": frappe.utils.now_datetime(),
        }, commit=True)
        update_log_receipt(job.name, "printed_queue_ok", task_status="ok")
        return

    messages = {
        2: "Printer not found on the print PC",
        3: "Printer offline or in error state",
        4: "Print spooler rejected the job",
        5: "Rendered file could not be opened by the printer engine",
        6: "Unknown agent error",
    }
    message = detail or messages.get(code, "Agent error code {0}".format(code))
    event_type = "PrinterOffline" if code in (2, 3, 4) else "NetTimeout"
    update_job(job.name, {
        "status": "failed",
        "ack_code": "exit_{0}: {1}".format(code, message)[:140],
        "receipt_level": "failed",
        "finalized_on": frappe.utils.now_datetime(),
    }, commit=True)
    update_log_receipt(job.name, "failed", task_status="exit_{0}".format(code))
    _scan_event(event_type, station=job.station, scan_value=job.scan_code,
                message=message[:500])
    frappe.db.commit()
