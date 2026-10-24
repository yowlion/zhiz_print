# -*- coding: utf-8 -*-
# Copyright (c) 2026, Zhiz Print and contributors
# For license information, please see license.txt

# Xpyun (Xinye) thermal label/receipt cloud printer adapter (scan-print
# plan 4). Text/ESCP markup only - the vendor has NO PDF or raster path
# (plan cross-examination finding F1), so payload_kind=text_markup feeds a
# simplified template from Zprint Cloud Device.text_template with
# __scan_code__/__docname__/__date__ placeholders. Content over 12K is split
# into multiple tasks (plan boundary table); any part failing fails the WHOLE
# job so a resubmit never produces a half ticket.
# POC-ADJUST: sign algorithm and status enums follow the vendor's open API
# docs and must be verified on the first real printer.

from __future__ import unicode_literals

import hashlib
import json
import time

import requests

from zhiz_print.zhiz_print.print_channels.cloud import base

BASE_URL = "https://api.xpyun.net/api/open"
REQUEST_TIMEOUT = 10
MAX_CONTENT_BYTES = 12 * 1024


def _sign(user, userkey, timestamp):
    """Xpyun open-API signature: sha1 hex of user+userkey+timestamp."""
    raw = "{0}{1}{2}".format(user, userkey, timestamp)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _auth_body(device, extra=None):
    body = {
        "user": device.get("api_key") or "",
        "userkey": device.get("credential") or "",
        "timestamp": str(int(time.time())),
    }
    body["sign"] = _sign(body["user"], body["userkey"], body["timestamp"])
    if extra:
        body.update(extra)
    return body


def _post(path, body):
    resp = requests.post(
        BASE_URL + path, data=json.dumps(body),
        headers={"Content-Type": "application/json"},
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code != 200:
        raise RuntimeError("Xpyun HTTP {0}: {1}".format(resp.status_code, resp.text[:200]))
    result = resp.json() if resp.content else {}
    # POC-ADJUST: xpyun reports business errors via code/msg on HTTP 200.
    code = result.get("code")
    if code not in (0, "0", None):
        raise RuntimeError("Xpyun error {0}: {1}".format(code, result.get("msg") or ""))
    return result


class XpyunChannel(base.CloudChannel):
    capabilities = {
        "pdf": False,
        "text_markup": True,
    }

    def send(self, device, printer_ctx, job, payload):
        """addPrintTask with split-on-12K all-or-nothing semantics.

        Returns comma-joined vendor task ids (one per part).
        """
        if payload.get("kind") != "text_markup":
            raise RuntimeError("Xpyun accepts text markup payloads only")
        content = payload.get("content") or ""
        if not content:
            raise RuntimeError("Empty text content for Xpyun printer")
        parts = _split_content(content)
        origin_id = self.vendor_origin_id(job)
        task_ids = []
        for index, part in enumerate(parts):
            body = _auth_body(device, {
                "sn": device["name"],
                "content": part,
                "copies": 1,
                "idempotency": "{0}-{1}".format(origin_id[:40], index),
            })
            result = _post("/addPrintTask", body)
            data = result.get("data") or {}
            task_id = data.get("id") or data.get("taskId")
            if not task_id:
                raise RuntimeError(
                    "Xpyun addPrintTask returned no task id: {0}".format(str(result)[:200]))
            task_ids.append(str(task_id))
        return ",".join(task_ids)

    def query_status(self, device, printer_ctx, vendor_job_id):
        """queryPrintState over every part; all printed => printed."""
        states = []
        raws = []
        for task_id in str(vendor_job_id).split(","):
            if not task_id:
                continue
            body = _auth_body(device, {"id": task_id})
            result = _post("/queryPrintState", body)
            data = result.get("data") or {}
            state = cint_safe(data.get("state"))
            raws.append("{0}={1}".format(task_id, state))
            # POC-ADJUST: xpyun device states (1=printing etc.) - treated as
            # printed only on the documented completed value; everything else
            # keeps polling so a wrong guess can never fake paper-out.
            states.append(_normalize_state(state, data))
        if base.STATE_FAILED in states:
            return base.STATE_FAILED, ";".join(raws)
        if states and all(s == base.STATE_PRINTED for s in states):
            return base.STATE_PRINTED, ";".join(raws)
        if base.STATE_PRINTING in states:
            return base.STATE_PRINTING, ";".join(raws)
        return base.STATE_PENDING, ";".join(raws)

    def query_device(self, device, printer_ctx=None):
        body = _auth_body(device, {"sn": device["name"]})
        result = _post("/queryPrinterStatus", body)
        data = result.get("data") or {}
        return {
            "online": bool(data.get("online", True)),
            "raw": str(result)[:500],
        }

    def parse_callback(self, payload, secret):
        """Guest callback (disabled by default): verify state-sign HMAC."""
        import hmac as _hmac
        sign = str(payload.get("sign") or "")
        message = str(payload.get("id") or "") + str(payload.get("state") or "")
        expected = _hmac.new(
            (secret or "").encode("utf-8"), message.encode("utf-8"),
            hashlib.sha256).hexdigest()
        if not sign or not _hmac.compare_digest(expected, sign):
            raise PermissionError("Xpyun callback signature verification failed")
        return (_normalize_state(cint_safe(payload.get("state")), payload),
                str(payload.get("id") or ""))


def _normalize_state(state, data):
    value = cint_safe(state)
    # POC-ADJUST: 9 = printed/completed in xpyun docs; 8/7 error classes.
    if value == 9:
        return base.STATE_PRINTED
    if value in (7, 8):
        return base.STATE_FAILED
    if value in (1, 2):
        return base.STATE_PRINTING
    return base.STATE_PENDING


def cint_safe(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _split_content(content):
    """Split oversized text into <=12K chunks on line boundaries.

    A real ticket cut mid-line prints garbage; split on the last newline
    that keeps the chunk under the byte budget.
    """
    encoded = content.encode("utf-8")
    if len(encoded) <= MAX_CONTENT_BYTES:
        return [content]
    parts = []
    current = ""
    for line in content.splitlines(keepends=True):
        if len((current + line).encode("utf-8")) > MAX_CONTENT_BYTES and current:
            parts.append(current)
            current = line
        else:
            current += line
    if current:
        parts.append(current)
    return parts
