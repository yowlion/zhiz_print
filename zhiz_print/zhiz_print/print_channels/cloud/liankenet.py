# -*- coding: utf-8 -*-
# Copyright (c) 2026, Zhiz Print and contributors
# For license information, please see license.txt

# Liankenet (Lianke) A4 cloud print box adapter (scan-print plan 4).
# PDF multipart upload + task-id polling; errLimitNum consecutive-failure
# lock monitoring with an optional auto reset gated by Zprint Setting.
# POC-ADJUST: the vendor has no versioned public docs (the plan's
# one-vote-veto point) - field names below follow the community Postman
# collection and MUST be re-verified against two real boxes in week one.

from __future__ import unicode_literals

import requests

import frappe
from frappe.utils import cint

from zhiz_print.zhiz_print.print_channels.cloud import base

BASE_URL = "https://cloud.liankenet.com"
JOB_URL = BASE_URL + "/api/print/job"
RESET_URL = BASE_URL + "/api/print/reset_printing_error_limit"
REQUEST_TIMEOUT = 10
ERROR_LIMIT_LOCK = 30

PAPER_SIZE_MAP = {
    "a4": "9",
    "letter": "11",
}


def _paper_size(media):
    """dmPaperSize mapping per task contract: 9=A4, 11=Letter, 0=custom."""
    return PAPER_SIZE_MAP.get((media or "").strip().lower(), "0")


class LiankenetChannel(base.CloudChannel):
    capabilities = {
        "pdf": True,
        "text_markup": False,
    }

    def send(self, device, printer_ctx, job, payload):
        """Multipart POST /api/print/job with the rendered PDF."""
        if payload.get("kind") != "pdf":
            raise RuntimeError("Liankenet box accepts PDF payloads only")
        pdf_bytes = payload.get("bytes") or b""
        if not pdf_bytes:
            raise RuntimeError("Empty PDF payload for Liankenet box")
        data = {
            "deviceId": device["name"],
            "deviceKey": device.get("credential") or "",
            "taskName": (job.ref_docname or job.name)[:60],
            "printNum": "1",
            "dmPaperSize": _paper_size(printer_ctx.get("media")),
            "jpScale": "none",
            "origin_id": self.vendor_origin_id(job),
        }
        files = {"contentFile": ("scan.pdf", pdf_bytes, "application/pdf")}
        headers = {"ApiKey": device.get("api_key") or ""}
        resp = requests.post(
            JOB_URL, data=data, files=files, headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                "Liankenet HTTP {0}: {1}".format(resp.status_code, resp.text[:200]))
        result = resp.json() if resp.content else {}
        # POC-ADJUST: response envelope varies across firmware generations.
        task_id = (
            result.get("taskId")
            or result.get("task_id")
            or (result.get("data") or {}).get("taskId")
            or (result.get("data") or {}).get("task_id")
        )
        if not task_id:
            raise RuntimeError("Liankenet response without task id: {0}".format(str(result)[:200]))
        return str(task_id)

    def query_status(self, device, printer_ctx, vendor_job_id):
        """GET /api/print/job?taskId=... normalized to a base state."""
        resp = requests.get(
            JOB_URL,
            params={"taskId": vendor_job_id},
            headers={"ApiKey": device.get("api_key") or ""},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            raise RuntimeError("Liankenet poll HTTP {0}".format(resp.status_code))
        result = resp.json() if resp.content else {}
        data = result.get("data") if isinstance(result.get("data"), dict) else result
        raw_status = str(
            data.get("taskStatus") or data.get("task_status")
            or data.get("status") or ""
        )
        return self._normalize(raw_status), raw_status

    def _normalize(self, raw_status):
        # Failure flags are tested FIRST: substrings overlap across vendors
        # ("printfail", "cancelprint", "print_error") and a success-first
        # match would report paper that never came out.
        value = (raw_status or "").strip().lower()
        if any(flag in value for flag in ("fail", "error", "cancel", "lock",
                                          "abort")):
            return base.STATE_FAILED
        if any(flag in value for flag in ("finish", "success", "complete",
                                          "done", "printed")):
            return base.STATE_PRINTED
        if any(flag in value for flag in ("print", "processing", "process",
                                          "send")):
            return base.STATE_PRINTING
        if any(flag in value for flag in ("wait", "queue", "pend", "accept",
                                          "submit")):
            return base.STATE_PENDING
        return base.STATE_PENDING

    def query_device(self, device, printer_ctx=None):
        """Device health snapshot; watches the errLimitNum failure lock."""
        resp = requests.get(
            JOB_URL,
            params={"deviceId": device["name"]},
            headers={"ApiKey": device.get("api_key") or ""},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            raise RuntimeError("Liankenet device poll HTTP {0}".format(resp.status_code))
        result = resp.json() if resp.content else {}
        data = result.get("data") if isinstance(result.get("data"), dict) else result
        err_limit = cint(data.get("errLimitNum") or data.get("err_limit_num") or 0)
        snapshot = {
            "online": bool(data.get("online", data.get("state") not in (None, "offline"))),
            "errLimitNum": err_limit,
            "raw": str(result)[:500],
        }
        if err_limit >= ERROR_LIMIT_LOCK:
            frappe.log_error(
                "Liankenet device {0} hit the error lock (errLimitNum={1})".format(
                    device["name"], err_limit),
                "scan_print cloud")
            if cint(frappe.db.get_single_value(
                    "Zprint Setting", "auto_reset_vendor_err_limit")):
                self.reset_error_limit(device)
                snapshot["auto_reset_attempted"] = True
        return snapshot

    def reset_error_limit(self, device, printer_ctx=None):
        """POST /api/print/reset_printing_error_limit (plan 4 closed loop)."""
        try:
            resp = requests.post(
                RESET_URL,
                data={"deviceId": device["name"], "deviceKey": device.get("credential") or ""},
                headers={"ApiKey": device.get("api_key") or ""},
                timeout=REQUEST_TIMEOUT,
            )
            return resp.status_code == 200
        except Exception:
            frappe.log_error(frappe.get_traceback(),
                             "scan_print liankenet reset failed")
            return False
