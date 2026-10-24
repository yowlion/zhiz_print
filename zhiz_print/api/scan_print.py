# -*- coding: utf-8 -*-
# Copyright (c) 2026, Zhiz Print and contributors
# For license information, please see license.txt

# Scan silent-print shared trunk (scan-print plan 1, trunk task 4).
# One pipeline, three pluggable channels:
#   scan_receive() -> dedup -> rule match -> doc resolve -> gates ->
#   enqueue render_job() -> artifact -> route -> dispatch_to_channel() -> log
# Channel implementations live in zhiz_print/print_channels/ (A=agent,
# B=IPP, C=cloud). Receipt semantics are deliberately NOT "paper is out":
# A=committed to the agent print queue, B=IPP final job state, C=vendor
# cloud confirmation.

import hashlib
import hmac
import json
import os
import time

import frappe
from frappe import _
from frappe.utils import cint
from frappe.utils.safe_exec import ServerScriptNotEnabled

from zhiz_print.api.license import has_silent_print_entitlement
from zhiz_print.zhiz_print.doctype.zprint_scan_rule.zprint_scan_rule import ZprintScanRule

HMAC_TIMESTAMP_WINDOW = 300
MAX_SCAN_CODE_LENGTH = 500
MAX_JOB_IDS_PER_POLL = 50
MAX_EXTRA_JSON_LENGTH = 10000
DEFAULT_DEDUP_WINDOW = 60

RENDER_LOCK_PREFIX = "zhiz_print:scan_render_lock:"
RENDER_SEM_PREFIX = "zhiz_print:scan_render_sem:"

# Terminal status values of the Zprint Print Job state machine (plan 1).
TERMINAL_STATUSES = (
    "printed_queue_ok",
    "job_state_final",
    "printed_vendor_confirmed",
    "failed",
    "unknown",
    "expired",
)
SUCCESS_STATUSES = ("printed_queue_ok", "job_state_final", "printed_vendor_confirmed")

_RULE_FIELDS = [
    "name", "rule_name", "enabled", "priority", "match_mode", "match_pattern",
    "case_sensitive", "match_length", "digit_only", "station_filter",
    "target_doctype", "docname_mode", "strip_prefix", "code_field",
    "resolve_query", "template_track", "design_name", "use_auto_match",
    "native_print_format", "params_json", "copies", "dedup_seconds",
    "allow_reprint", "block_draft", "success_hint_fields",
]


def _setting(fieldname, default=0):
    """Read a Zprint Setting value with a hard default (pre-migrate safe)."""
    value = frappe.db.get_single_value("Zprint Setting", fieldname)
    if value is None or value == "":
        return default
    return value


# ---------------------------------------------------------------------------
# Authentication (shared with api/agent.py)
# ---------------------------------------------------------------------------

def _canonical_body(extra_exclude=None):
    """Canonical JSON of the current request body, used for HMAC signing.

    Contract for callers (ZhizPrintAgent): send Content-Type application/json
    with ALL values as JSON strings, sign
        sig = hex(HMAC_SHA256(station_api_secret, timestamp + "." + body_json))
    where body_json = json.dumps(body_dict_without_sig, sort_keys=True,
    separators=(",", ":"), ensure_ascii=False). The server rebuilds the exact
    same string from what it received, so any difference (key order, spacing,
    unicode escaping, int vs string) breaks the signature.

    For application/json calls the canonical string is rebuilt from the RAW
    request body, not form_dict: frappe.is_whitelisted sanitizes every
    form_dict string on guest requests, and a sanitized value (a scan code
    containing &, < or >) would no longer match the bytes the agent signed.
    Form-encoded calls (no JSON body) fall back to form_dict.
    """
    exclude = {"sig"}
    if extra_exclude:
        exclude.update(extra_exclude)

    request = getattr(frappe.local, "request", None)
    raw = None
    content_type = ""
    if request is not None:
        try:
            content_type = (request.headers.get("Content-Type") or "").lower()
        except Exception:
            content_type = ""
        if "application/json" in content_type:
            try:
                raw = request.get_data(as_text=True)
            except Exception:
                raw = None
    if raw:
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            params = {
                k: v for k, v in parsed.items()
                if k not in exclude and k != "cmd"
            }
            return json.dumps(params, sort_keys=True, separators=(",", ":"),
                              ensure_ascii=False)

    params = {
        k: v for k, v in frappe.local.form_dict.items()
        if k not in exclude and k != "cmd"
    }
    return json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hmac_station_from_params():
    """Authenticate an agent request via api_key + timestamp + sig."""
    params = frappe.local.form_dict
    api_key = str(params.get("api_key") or "")
    timestamp = str(params.get("timestamp") or "")
    sig = str(params.get("sig") or "")
    if not (api_key and timestamp and sig):
        return None
    if not timestamp.isdigit():
        frappe.throw(_("Invalid timestamp"), frappe.AuthenticationError)
    skew = abs(time.time() - float(timestamp))
    if skew > HMAC_TIMESTAMP_WINDOW:
        frappe.throw(_("Timestamp outside the allowed window"), frappe.AuthenticationError)

    stations = frappe.get_all(
        "Zprint Station",
        filters={"api_key": api_key},
        fields=["name"],
        limit=2,
    )
    if not stations:
        frappe.throw(_("Unknown station credentials"), frappe.AuthenticationError)
    if len(stations) > 1:
        frappe.log_error("Duplicate Zprint Station api_key detected", "scan_print auth")
        frappe.throw(_("Ambiguous station credentials"), frappe.AuthenticationError)

    station = frappe.get_doc("Zprint Station", stations[0].name)
    if not cint(station.enabled):
        frappe.throw(_("Station is disabled"), frappe.AuthenticationError)
    try:
        secret = station.get_password("api_secret", raise_exception=False) or ""
    except Exception:
        secret = ""
    if not secret:
        frappe.throw(_("Station has no API secret configured"), frappe.AuthenticationError)

    body_json = _canonical_body()
    message = timestamp + "." + body_json
    expected = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        frappe.throw(_("Signature verification failed"), frappe.AuthenticationError)
    return station


def _session_station():
    """Authenticate a PDA/browser request via the logged-in session user."""
    user = frappe.session.user
    if not user or user == "Guest":
        return None
    stations = frappe.get_all(
        "Zprint Station",
        filters={"bound_user": user, "enabled": 1},
        fields=["name"],
        order_by="creation asc",
        limit=1,
    )
    if not stations:
        return None
    return frappe.get_doc("Zprint Station", stations[0].name)


def authenticate_station(allow_session=True):
    """Dual-path authentication for scan/agent endpoints.

    Path 1 (Channel A agent): api_key + timestamp + sig HMAC over the request
    body, keyed by Station.api_secret, +/-300s clock window (server_time in
    every response lets agents persist a clock offset, plan 6-2).
    Path 2 (PDA/browser): the logged-in session user must equal the station's
    bound_user; the browser never holds the API secret.

    Raises frappe.AuthenticationError on any failure.
    """
    station = _hmac_station_from_params()
    if station:
        return station
    if allow_session:
        station = _session_station()
        if station:
            return station
    frappe.throw(_("Authentication failed"), frappe.AuthenticationError)


def effective_user(station):
    """User whose print permission anchors a scan.

    Session scans use the session user (== bound_user by _session_station).
    Agent (HMAC) scans have no user session, so the station's bound_user is
    the permission anchor; a station without one is denied (fail closed).
    """
    user = frappe.session.user
    if user and user != "Guest":
        return user
    return station.bound_user or None


# ---------------------------------------------------------------------------
# Rule matching / doc resolution
# ---------------------------------------------------------------------------

def _match_rule(station_id, scan_code):
    """First match wins; enabled rules ordered by priority ascending.

    Matching semantics (case handling, Exact/Prefix/Regex) are delegated to
    the ZprintScanRule controller so desk validation and trunk matching can
    never diverge. Length/digit constraints are trunk-side hard filters.
    """
    rules = frappe.get_all(
        "Zprint Scan Rule",
        filters={"enabled": 1},
        fields=_RULE_FIELDS,
        order_by="priority asc, creation asc",
        limit=0,
    )
    for rule in rules:
        if rule.station_filter and rule.station_filter != station_id:
            continue
        if _rule_matches(rule, scan_code):
            return rule
    return None


def _rule_matches(rule, value):
    if cint(rule.digit_only) and not str(value).isdigit():
        return False
    want_len = cint(rule.match_length)
    if want_len and len(str(value)) != want_len:
        return False
    return bool(ZprintScanRule._matches(rule, str(value)))


def _resolve_docname(rule, value):
    """Resolve the scanned value to a document name (plan 1 step 3).

    code_field lookups pass the value as a filters dict value (parameterized,
    never string-formatted into SQL). The field name itself is hard-validated
    against the column charset. resolve_query runs under frappe.safe_exec
    (read-only globals); Server Script support must be enabled on the site.
    """
    mode = rule.docname_mode or "code_is_docname"
    doctype = rule.target_doctype
    if mode == "code_is_docname":
        return str(value)
    if mode == "strip_prefix":
        prefix = (rule.strip_prefix or "")
        if prefix:
            if not str(value).startswith(prefix):
                return None
            return str(value)[len(prefix):]
        return str(value)
    if mode == "code_field":
        field = (rule.code_field or "").strip()
        if not field:
            frappe.throw(_("Code Field is required for this Docname Mode"))
        import re as _re
        if not _re.match(r"^[a-z0-9_]+$", field):
            frappe.throw(_("Code Field must be a plain column name"))
        return frappe.db.get_value(doctype, {field: str(value)}, "name")
    if mode == "resolve_query":
        query = (rule.resolve_query or "").strip()
        if not query:
            frappe.throw(_("Resolve Query is required for this Docname Mode"))
        from frappe.utils.safe_exec import safe_exec
        _locals = {
            "scan_code": str(value),
            "station": frappe.local.form_dict.get("station_id") or "",
            "docname": None,
        }
        try:
            safe_exec(query, _globals=None, _locals=_locals, restrict_commit_rollback=True)
        except ServerScriptNotEnabled:
            frappe.throw(
                _("Server Scripts are disabled on this site; enable them to use Resolve Query rules")
            )
        return _locals.get("docname") or None
    frappe.throw(_("Unsupported Docname Mode: {0}").format(mode))


def _resolve_design(rule, doctype, docname):
    """Pin the template for a scan: explicit design, else auto-match."""
    if rule.template_track == "native_format":
        return None
    design_name = rule.design_name
    if not design_name and cint(rule.use_auto_match):
        from zhiz_print.api.batch_print import _auto_match_designs_for_docs
        doc_matches, _unused = _auto_match_designs_for_docs(doctype, [docname])
        matched = doc_matches.get(docname)
        design_name = matched["name"] if matched else None
    if not design_name:
        frappe.throw(_("No print design matched for {0} {1}").format(doctype, docname))
    return design_name


def _resolve_params(rule, scan_code, station_id):
    """Parse Params JSON with __scan_code__/__terminal__ placeholders replaced."""
    raw = (rule.params_json or "").strip() if rule else ""
    if not raw:
        return {}
    raw = raw.replace("__scan_code__", scan_code).replace("__terminal__", station_id or "")
    try:
        params = json.loads(raw)
    except (ValueError, TypeError):
        frappe.throw(_("Params JSON is not valid JSON"))
    return params if isinstance(params, dict) else {}


# ---------------------------------------------------------------------------
# Job helpers
# ---------------------------------------------------------------------------

def _dedup_window(rule):
    seconds = cint(rule.dedup_seconds) if rule else 0
    if seconds > 0:
        return seconds
    return cint(_setting("dedup_window_sec", DEFAULT_DEDUP_WINDOW)) or DEFAULT_DEDUP_WINDOW


def _build_job_key(scan_code, station_id, window):
    bucket = int(time.time() // window)
    payload = "{0}|{1}|{2}".format(scan_code, station_id, bucket)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_job(job_name):
    return frappe.db.get_value(
        "Zprint Print Job",
        job_name,
        [
            "name", "job_key", "scan_code", "station", "ref_doctype", "ref_docname",
            "design_name", "channel", "copies", "status", "pdf_data", "pdf_file",
            "pdf_sha256", "vendor_job_id", "origin_nonce", "ack_code",
            "retry_count",
            "degraded_from_channel", "receipt_level", "print_user", "scan_time",
            "client_uuid",
        ],
        as_dict=True,
    )


def _create_job(job_key, station_id, scan_code, client_uuid, print_user):
    """Insert the dedup-anchored job row; return (job_name, dup).

    Duplicate job_key (same code + station inside the window) or duplicate
    client_uuid (PDA weak-network replay) both return the earlier job so the
    caller answers with its full current state instead of printing twice.

    A replay whose dedup bucket has already rolled over hits the client_uuid
    UNIQUE index instead of the primary key: frappe raises
    UniqueValidationError for that (DuplicateEntryError is primary-key only),
    so both exception types must be caught here.
    """
    try:
        doc = frappe.get_doc({
            "doctype": "Zprint Print Job",
            "job_key": job_key,
            "scan_code": scan_code,
            "station": station_id,
            "status": "queued",
            "copies": 1,
            "copies_mode": "merged_pages",
            "client_uuid": client_uuid or None,
            "print_user": print_user,
            "scan_time": frappe.utils.now_datetime(),
        })
        doc.insert(ignore_permissions=True)
        return (doc.name, False)
    except (frappe.DuplicateEntryError, frappe.UniqueValidationError):
        existing = frappe.db.get_value("Zprint Print Job", job_key, "name")
        if not existing and client_uuid:
            existing = frappe.db.get_value(
                "Zprint Print Job", {"client_uuid": client_uuid}, "name")
        if not existing:
            raise
        return (existing, True)


def _fail_job(job_name, event_type, message, rule_name=None, station=None, scan_code=None):
    """Mark a job failed and drop a matching Scan Event (full audit)."""
    if job_name:
        frappe.db.set_value("Zprint Print Job", job_name, {
            "status": "failed",
            "receipt_level": "failed",
            "finalized_on": frappe.utils.now_datetime(),
        }, update_modified=True)
    _scan_event(event_type, station=station, scan_value=scan_code,
                rule=rule_name, message=message)
    frappe.log_error("Scan print job {0} failed: {1}".format(job_name, message), "scan_print")


def _scan_event(event_type, station=None, scan_value=None, rule=None, message=None):
    try:
        doc = frappe.get_doc({
            "doctype": "Zprint Scan Event",
            "event_time": frappe.utils.now_datetime(),
            "station": station,
            "scan_value": (scan_value or "")[:140],
            "event_type": event_type,
            "rule": rule,
            "message": (message or "")[:500],
        })
        doc.insert(ignore_permissions=True)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "scan_print event write failed")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def scan_receive(station_id, scan_code, client_uuid=None, extra_json=None):
    """Shared trunk entry: a scan in, one deduplicated print job out.

    Auth: station HMAC (Channel A agent) OR session user == bound_user (PDA).
    allow_guest=True is required because the agent has no Frappe session -
    guest entry is safe: authenticate_station() enforces the station HMAC
    below, and a logged-in PDA session still passes the CSRF check before
    this handler runs.
    Returns {job_id, status, server_time, dup, message}. Business failures
    (no rule, gates) come back in this envelope; only protocol problems
    (auth, malformed input) raise.

    extra_json is reserved for future client metadata; it must be a JSON
    object string when present and is not persisted in v1.
    """
    station = authenticate_station()
    station_id = (station_id or "").strip()
    scan_code = (scan_code or "").strip()
    client_uuid = (client_uuid or "").strip()[:100]
    if not station_id or station_id != station.name:
        frappe.throw(_("Station does not match the authenticated credentials"))
    if not scan_code:
        frappe.throw(_("Scan code is required"))
    if len(scan_code) > MAX_SCAN_CODE_LENGTH:
        frappe.throw(_("Scan code is too long"))
    if extra_json:
        if len(extra_json) > MAX_EXTRA_JSON_LENGTH:
            frappe.throw(_("extra_json is too large"))
        try:
            parsed_extra = json.loads(extra_json)
        except (ValueError, TypeError):
            frappe.throw(_("extra_json is not valid JSON"))
        if not isinstance(parsed_extra, dict):
            frappe.throw(_("extra_json must be a JSON object"))

    if not cint(_setting("scan_print_enabled", 0)):
        frappe.throw(_("Scan print is disabled on this site"))

    print_user = effective_user(station)
    if not print_user:
        _scan_event("EntitlementDenied", station=station_id, scan_value=scan_code,
                    message="Station has no bound user for permission checks")
        frappe.throw(_("Station has no bound user for permission checks"),
                     frappe.PermissionError)

    response = {
        "job_id": None,
        "status": "rejected",
        "server_time": frappe.utils.now_datetime().strftime("%Y-%m-%d %H:%M:%S"),
        "dup": False,
        "message": "",
    }

    # Rule match runs before job insert so a rule-level dedup window
    # (dedup_seconds) can take part in the job key (plan 8: both knobs).
    rule = _match_rule(station_id, scan_code)
    if not rule:
        _scan_event("NoRule", station=station_id, scan_value=scan_code,
                    message="No enabled scan rule matched")
        response["status"] = "no_rule"
        response["message"] = "No scan rule matched this code"
        return response

    job_name, dup = _create_job(
        _build_job_key(scan_code, station_id, _dedup_window(rule)),
        station_id, scan_code, client_uuid, print_user,
    )
    response["job_id"] = job_name
    response["dup"] = dup
    if dup:
        old = _load_job(job_name)
        response["status"] = old.status
        response["message"] = "Duplicate scan inside the dedup window"
        return response

    # Reprint policy: a rule without allow_reprint refuses a scan that already
    # produced a successful job (out-of-window repeats; in-window repeats are
    # answered by the dedup branch above).
    if not cint(rule.allow_reprint):
        prior = frappe.get_all(
            "Zprint Print Job",
            filters={
                "scan_code": scan_code,
                "station": station_id,
                "status": ("in", list(SUCCESS_STATUSES)),
            },
            fields=["name"],
            order_by="scan_time desc",
            limit=1,
        )
        if prior:
            _fail_job(job_name, "DupBlocked",
                      "Reprint is not allowed by rule {0}".format(rule.rule_name),
                      rule_name=rule.name, station=station_id, scan_code=scan_code)
            response["status"] = "dup_blocked"
            response["message"] = "Reprints are not allowed for this rule"
            return response

    # Doc resolution
    try:
        docname = _resolve_docname(rule, scan_code)
    except frappe.ValidationError as exc:
        _fail_job(job_name, "NoDoc", str(exc)[:500],
                  rule_name=rule.name, station=station_id, scan_code=scan_code)
        raise
    if not docname or not frappe.db.exists(rule.target_doctype, docname):
        _fail_job(job_name, "NoDoc",
                  "No {0} found for scanned value".format(rule.target_doctype),
                  rule_name=rule.name, station=station_id, scan_code=scan_code)
        response["status"] = "failed"
        response["message"] = "Document not found"
        return response

    frappe.db.set_value("Zprint Print Job", job_name, {
        "ref_doctype": rule.target_doctype,
        "ref_docname": docname,
    }, update_modified=False)

    # Gate 1: paid-feature entitlement (network silent printing).
    entitled, reason_code = has_silent_print_entitlement()
    if not entitled:
        _fail_job(job_name, "EntitlementDenied",
                  "Silent print entitlement denied: {0}".format(reason_code),
                  rule_name=rule.name, station=station_id, scan_code=scan_code)
        response["status"] = "failed"
        response["message"] = "Silent print is not entitled ({0})".format(reason_code)
        return response

    # Gate 2: print permission anchored on the effective user.
    if not frappe.has_permission(rule.target_doctype, "print", doc=docname, user=print_user):
        _fail_job(job_name, "EntitlementDenied",
                  "User {0} has no print permission on {1} {2}".format(
                      print_user, rule.target_doctype, docname),
                  rule_name=rule.name, station=station_id, scan_code=scan_code)
        response["status"] = "failed"
        response["message"] = "No print permission for this document"
        return response

    # Gate 3: draft block (per rule).
    docstatus = cint(frappe.db.get_value(rule.target_doctype, docname, "docstatus") or 0)
    if docstatus == 0 and cint(rule.block_draft):
        _fail_job(job_name, "DraftBlocked",
                  "{0} {1} is in draft state".format(rule.target_doctype, docname),
                  rule_name=rule.name, station=station_id, scan_code=scan_code)
        response["status"] = "failed"
        response["message"] = "Draft documents are blocked by this rule"
        return response

    # Template pinning (design resolution belongs to the synchronous phase so
    # auto-match errors surface in the scan response, not in a worker log).
    try:
        design_name = _resolve_design(rule, rule.target_doctype, docname)
    except frappe.ValidationError as exc:
        _fail_job(job_name, "RenderFail", str(exc)[:500],
                  rule_name=rule.name, station=station_id, scan_code=scan_code)
        raise
    copies = cint(rule.copies) or 1
    if copies < 1:
        copies = 1
    frappe.db.set_value("Zprint Print Job", job_name, {
        "design_name": design_name or ("native:" + (rule.native_print_format or "")),
        "copies": copies,
    }, update_modified=False)

    message = "Accepted"
    hints = [f.strip() for f in (rule.success_hint_fields or "").split(",") if f.strip()]
    if hints:
        try:
            values = frappe.db.get_value(rule.target_doctype, docname, hints)
            values = values if isinstance(values, (list, tuple)) else [values]
            message = "Accepted: {0} ({1})".format(
                docname, ", ".join(str(v) for v in values if v is not None))
        except Exception:
            message = "Accepted: {0}".format(docname)

    # Full async from here on (plan 5: PDA forced async; agent claims later).
    # NOTE: the kwarg must not be called job_name - frappe.enqueue swallows
    # job_name/job_id as its own (deprecated) naming parameters and never
    # forwards them to the target function.
    budget = cint(_setting("scan_render_budget_s", 30)) or 30
    frappe.enqueue(
        "zhiz_print.api.scan_print.render_job",
        queue="short",
        timeout=budget * 2 + 120,
        enqueue_after_commit=True,
        scan_job_name=job_name,
    )
    response["status"] = "queued"
    response["message"] = message
    return response


# ---------------------------------------------------------------------------
# Render + route + dispatch (background)
# ---------------------------------------------------------------------------

def render_job(scan_job_name):
    """Background worker: render the PDF, then route + dispatch (plan 1: 5-8).

    The parameter name must differ from frappe.enqueue's reserved kwargs
    (job_name/job_id/...): those are consumed as queue metadata and never
    forwarded to this function.
    """
    job = _load_job(scan_job_name)
    # "rendering" re-entry is allowed for scheduler retries of crashed
    # renders; the render dedup lock below serializes concurrent workers.
    if not job or job.status not in ("queued", "rendering"):
        return

    lock_key = RENDER_LOCK_PREFIX + job.job_key
    budget = cint(_setting("scan_render_budget_s", 30)) or 30
    ttl = max(budget * 2, 60)
    cache = frappe.cache()
    # Same-code render dedup: only one worker renders a job_key at a time;
    # losers exit and the scheduler retry covers crashes.
    if not cache.set(cache.make_key(lock_key), 1, ex=ttl, nx=True):
        return
    slot_key = _acquire_render_slot(ttl)
    if not slot_key:
        # Render concurrency saturated: stay queued, scheduler retries.
        cache.delete(cache.make_key(lock_key))
        return

    try:
        if job.print_user:
            frappe.set_user(job.print_user)
        frappe.db.set_value("Zprint Print Job", scan_job_name, {
            "status": "rendering",
            "render_started_on": frappe.utils.now_datetime(),
        }, update_modified=True)
        frappe.db.commit()

        rule = _match_rule(job.station, job.scan_code)
        params = _resolve_params(rule, job.scan_code, job.station)

        pdf_bytes = _render_scan_pdf(job, rule, params)
        _store_artifact(job, pdf_bytes)

        route_ctx = _resolve_route(job)
        _dispatch_with_fallback(job, route_ctx, params)
        frappe.db.commit()
    except Exception as exc:
        frappe.db.rollback()
        _fail_job(scan_job_name, "RenderFail", str(exc)[:500],
                  station=job.station, scan_code=job.scan_code)
        frappe.db.commit()
        raise
    finally:
        cache.delete(cache.make_key(lock_key))
        _release_render_slot(slot_key)


def _render_slot_count():
    """Render slot budget (plan 7-4: Cloud=2, LAN=4).

    An explicitly configured scan_max_concurrent always wins; otherwise the
    Zprint Setting.scan_semaphore_mode topology decides the default, so a
    cloud site does not silently run the LAN slot count into OOM.
    """
    raw = frappe.db.get_single_value("Zprint Setting", "scan_max_concurrent")
    if cint(raw):
        return cint(raw)
    mode = (frappe.db.get_single_value(
        "Zprint Setting", "scan_semaphore_mode") or "").strip().lower()
    return 2 if mode == "cloud" else 4


def _acquire_render_slot(ttl):
    """Redis slot semaphore sized by _render_slot_count().

    One NX key per slot with a TTL means a crashed worker leaks a slot only
    until the TTL expires - no INCR/DECR drift.
    """
    max_concurrent = _render_slot_count()
    cache = frappe.cache()
    for i in range(max_concurrent):
        key = RENDER_SEM_PREFIX + str(i)
        if cache.set(cache.make_key(key), 1, ex=ttl, nx=True):
            return key
    return None


def _release_render_slot(slot_key):
    if slot_key:
        frappe.cache().delete(frappe.cache().make_key(slot_key))


def _render_scan_pdf(job, rule, params):
    """Render via the render facade (super design) or the native track."""
    copies = cint(job.copies) or 1
    if rule and rule.template_track == "native_format":
        return _render_native_pdf(
            job.ref_doctype, [job.ref_docname], rule.native_print_format, copies)
    if not job.design_name:
        raise frappe.ValidationError(
            "Job {0} has no design and no native format".format(job.name))
    from zhiz_print.api.render_service import render_docs_to_pdf
    return render_docs_to_pdf(
        job.ref_doctype, [job.ref_docname], job.design_name,
        params=params, copies=copies,
    )


def _render_native_pdf(doctype, docnames, print_format, copies):
    """Native Print Format track: frappe.get_print bodies + configured engine.

    Mirrors batch_generate_native_pdf but returns bytes. Native has no Super
    Print Paper, so Chromium (which needs design.print_paper) is skipped the
    same way the desk batch native line does.
    """
    import re as _re

    if not print_format:
        raise frappe.ValidationError("Native print format is not configured")
    html_parts = []
    styles_seen = set()
    styles_collected = []
    total_blocks = len(docnames) * max(copies, 1)
    emitted = 0
    for docname in docnames:
        html = frappe.get_print(doctype, docname, print_format)
        from zhiz_print.api.render_service import enforce_template_asset_budget
        enforce_template_asset_budget(html)
        body_match = _re.search(r"<body[^>]*>([\s\S]*)</body>", html, _re.IGNORECASE)
        body_content = body_match.group(1) if body_match else html
        for style in _re.findall(r"<style[^>]*>[\s\S]*?</style>", html, _re.IGNORECASE):
            if style not in styles_seen:
                styles_seen.add(style)
                styles_collected.append(style)
        for _copy in range(max(copies, 1)):
            emitted += 1
            if emitted < total_blocks:
                html_parts.append(body_content + '<div style="page-break-after:always"></div>')
            else:
                html_parts.append(body_content)

    combined = (
        '<!DOCTYPE html>\n<html>\n<head>\n<meta charset="utf-8">\n'
        + "\n".join(styles_collected)
        + "\n</head>\n<body>\n"
        + "\n".join(html_parts)
        + "\n</body>\n</html>"
    )

    engine_mode = frappe.db.get_single_value("Zprint Setting", "pdf_engine_mode") or "wkhtmltopdf"
    if engine_mode == "WeasyPrint":
        from zhiz_print.api.print_designer import (
            _fix_merged_cell_borders_for_pdf, _expand_html_iframes,
            _resolve_image_urls_for_pdf,
        )
        from weasyprint import HTML as WeasyHTML
        combined = _fix_merged_cell_borders_for_pdf(combined)
        combined = _expand_html_iframes(combined)
        combined = _resolve_image_urls_for_pdf(combined)
        return WeasyHTML(string=combined).write_pdf()

    import pdfkit
    from zhiz_print.api.print_designer import (
        _prepare_html_for_wkhtmltopdf, _resolve_image_urls_for_pdf,
    )
    combined = _prepare_html_for_wkhtmltopdf(combined)
    combined = _resolve_image_urls_for_pdf(combined)
    options = {
        "quiet": "", "encoding": "UTF-8", "print-media-type": "",
        "background": "", "images": "", "disable-smart-shrinking": "",
        "margin-top": "0", "margin-bottom": "0",
        "margin-left": "0", "margin-right": "0",
    }
    return pdfkit.from_string(combined, False, options=options)


def _store_artifact(job, pdf_bytes):
    """Small PDFs ride inline (base64) in the job row; bigger go to the site
    private area via print_files.write_scan_pdf (never public/files)."""
    from zhiz_print.api.print_files import write_scan_pdf

    if not pdf_bytes:
        raise frappe.ValidationError("Render produced an empty PDF")
    threshold = cint(_setting("pdf_inline_threshold_bytes", 2097152)) or 2097152
    import base64
    if len(pdf_bytes) <= threshold:
        payload = {
            "pdf_data": base64.b64encode(pdf_bytes).decode("ascii"),
            "pdf_file": None,
        }
    else:
        path, sha, _size = write_scan_pdf(job.name, pdf_bytes)
        payload = {"pdf_data": None, "pdf_file": path}
    payload["pdf_sha256"] = hashlib.sha256(pdf_bytes).hexdigest()
    frappe.db.set_value("Zprint Print Job", job.name, payload, update_modified=True)
    job.pdf_data = payload["pdf_data"]
    job.pdf_file = payload["pdf_file"]
    # Backfill the in-memory row too: write_dispatch_log reads the object it
    # was handed, so a stale pdf_sha256 would break the audit hash chain.
    job.pdf_sha256 = payload["pdf_sha256"]


class RouteError(Exception):
    """Raised when no usable route exists (message goes to NoRoute event)."""


def _resolve_route(job):
    """First-match route resolution (plan 1 step 6): Route Rule priority DESC
    with nullable station/doctype/paper wildcards, then Station defaults, then
    E_NO_ROUTE. Channel Setting switches hard-gate each channel."""
    channel = None
    printer_name = None
    fallback_channel = None
    fallback_printer_name = None
    rule_label = ""

    routes = frappe.get_all(
        "Zprint Route Rule",
        filters={"enabled": 1},
        fields=[
            "name", "rule_name", "priority", "station", "target_doctype",
            "paper", "channel", "printer", "fallback_channel", "fallback_printer",
        ],
        order_by="priority desc, creation asc",
        limit=0,
    )
    for route in routes:
        if route.station and route.station != job.station:
            continue
        if route.target_doctype and route.target_doctype != job.ref_doctype:
            continue
        # paper matching is v1-wildcard: the job row carries no paper; a rule
        # with paper set simply never matches here.
        if route.paper:
            continue
        channel = route.channel
        printer_name = route.printer
        fallback_channel = route.fallback_channel
        fallback_printer_name = route.fallback_printer
        rule_label = route.name
        break

    if not channel:
        station_row = frappe.db.get_value(
            "Zprint Station", job.station,
            ["default_channel", "default_printer"], as_dict=True)
        if station_row and station_row.default_channel:
            channel = station_row.default_channel
            printer_name = station_row.default_printer

    if not channel:
        _scan_event("NoRoute", station=job.station, scan_value=job.scan_code,
                    message="No route rule or station default channel matched")
        raise RouteError("No print channel routed for job {0}".format(job.name))

    enabled_field = "channel_{0}_enabled".format(channel.lower())
    if not cint(_setting(enabled_field, 0)):
        _scan_event("NoRoute", station=job.station, scan_value=job.scan_code,
                    rule=rule_label,
                    message="Channel {0} is disabled in Zprint Setting".format(channel))
        raise RouteError("Channel {0} is disabled".format(channel))

    printer_ctx = _resolve_printer(channel, printer_name)

    fb_ctx = None
    if fallback_channel and fallback_channel != channel:
        try:
            fb_ctx = {
                "channel": fallback_channel,
                "printer": _resolve_printer(fallback_channel, fallback_printer_name),
                "fallback_channel": None,
                "fallback_printer": None,
                "station": job.station,
                "rule": rule_label,
            }
        except RouteError:
            fb_ctx = None

    return {
        "channel": channel,
        "printer": printer_ctx,
        "fallback_channel": fallback_channel,
        "fallback_printer": fb_ctx["printer"] if fb_ctx else None,
        "station": job.station,
        "rule": rule_label,
    }


def _resolve_printer(channel, printer_name):
    """Load the printer registry row and enforce channel-payload invariants."""
    if not printer_name:
        raise RouteError("Channel {0} route has no printer configured".format(channel))
    printer = frappe.db.get_value(
        "Zprint Printer", printer_name,
        [
            "name", "printer_logical_name", "channel", "enabled", "station",
            "agent", "os_printer_name", "paper", "bin", "monochrome",
            "uri", "auth_mode", "credential", "media", "timeout",
            "capability_snapshot", "cloud_device", "payload_kind",
        ],
        as_dict=True,
    )
    if not printer:
        raise RouteError("Printer {0} does not exist".format(printer_name))
    if not cint(printer.enabled):
        raise RouteError("Printer {0} is disabled".format(printer_name))
    if printer.channel != channel:
        raise RouteError(
            "Printer {0} belongs to channel {1}, not {2}".format(
                printer_name, printer.channel, channel))
    if channel == "A" and not (printer.os_printer_name or "").strip():
        raise RouteError(
            "Channel A printer {0} has no OS printer name".format(printer_name))
    if channel == "B" and not (printer.uri or "").strip():
        raise RouteError("Channel B printer {0} has no URI".format(printer_name))
    ctx = {
        "name": printer.name,
        "logical_name": printer.printer_logical_name,
        "os_printer_name": (printer.os_printer_name or "").strip(),
        "paper": (printer.paper or "").strip(),
        "bin": (printer.bin or "").strip(),
        "monochrome": cint(printer.monochrome),
        "uri": (printer.uri or "").strip(),
        "auth_mode": printer.auth_mode or "none",
        "media": (printer.media or "").strip(),
        "timeout": cint(printer.timeout) or 10,
        "payload_kind": printer.payload_kind or "pdf",
        "cloud_device": printer.cloud_device,
    }
    if channel == "C":
        if not printer.cloud_device:
            raise RouteError(
                "Channel C printer {0} has no cloud device".format(printer_name))
        device = frappe.db.get_value(
            "Zprint Cloud Device", printer.cloud_device,
            ["name", "device_type", "enabled", "api_key", "credential",
             "workstation", "callback_secret"],
            as_dict=True,
        )
        if not device or not cint(device.enabled):
            raise RouteError("Cloud device {0} is missing or disabled".format(printer.cloud_device))
        ctx["device"] = {
            "name": device.name,
            "device_type": device.device_type,
            "api_key": device.api_key or "",
            "workstation": device.workstation or "",
        }
        ctx["device"]["credential"] = _password("Zprint Cloud Device", device.name, "credential")
        ctx["device"]["callback_secret"] = _password("Zprint Cloud Device", device.name, "callback_secret")
    if channel == "B" and printer.auth_mode == "basic":
        ctx["credential"] = _password("Zprint Printer", printer.name, "credential")
    return ctx


def _password(doctype, name, fieldname):
    try:
        doc = frappe.get_doc(doctype, name)
        return doc.get_password(fieldname, raise_exception=False) or ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Dispatch + fallback + audit log
# ---------------------------------------------------------------------------

def _claim_dispatch(job_name, channel):
    """Atomic pre-dispatch claim (double-render / double-paper guard).

    Moves the row out of the claimable states (queued/rendering) into
    'dispatching' with a guarded UPDATE; rowcount decides. A stale render
    retry that lost the race - the winner already dispatched or finalized -
    gets rowcount 0 and must never dispatch again (same job twice on paper).
    InnoDB re-reads the latest committed row when the lock frees, so two
    racing workers can never both claim.
    """
    frappe.db.sql(
        "UPDATE `tabZprint Print Job` SET status = 'dispatching', channel = %s "
        "WHERE name = %s AND status IN ('queued', 'rendering')",
        (channel, job_name),
    )
    return bool(getattr(frappe.db._cursor, "rowcount", 0))


def _write_dispatch_log_quietly(job, route_ctx, params):
    """Audit-log write isolated from the dispatch flow (two failure domains).

    A log write failure must never look like a dispatch failure: the paper
    may already be out, and treating it as one would trigger the fallback
    hop and print the job a second time.
    """
    try:
        write_dispatch_log(job, route_ctx, params)
    except Exception:
        frappe.log_error(frappe.get_traceback(),
                         "scan_print dispatch log write failed for {0}".format(job.name))


def _dispatch_with_fallback(job, route_ctx, params):
    """One explicit fallback hop, never an automatic guess (plan 11).

    The audit log row is written by the trunk after a dispatch succeeds so
    every channel (and the fallback path) is logged identically. Dispatch
    and audit-log write are kept in separate failure domains: only a real
    dispatch error reaches the fallback branch.
    """
    from zhiz_print.zhiz_print.print_channels import dispatch_to_channel

    # Async channels re-use the envelope in their worker; carry the render
    # params so their audit log rows match the synchronous path.
    route_ctx["params"] = params or {}

    if not _claim_dispatch(job.name, route_ctx["channel"]):
        frappe.log_error(
            "Job {0} skipped dispatch: another worker moved it past the "
            "pre-dispatch state".format(job.name), "scan_print dispatch race")
        return
    job.status = "dispatching"

    try:
        dispatch_to_channel(job, route_ctx)
    except Exception as primary_error:
        frappe.log_error("Dispatch to channel {0} failed for job {1}: {2}".format(
            route_ctx["channel"], job.name, primary_error), "scan_print dispatch")
        _dispatch_fallback(job, route_ctx, params, primary_error)
        return
    _write_dispatch_log_quietly(job, route_ctx, params)


def _dispatch_fallback(job, route_ctx, params, primary_error):
    """The single explicit fallback hop for a failed primary dispatch."""
    from zhiz_print.zhiz_print.print_channels import dispatch_to_channel

    fallback_channel = route_ctx.get("fallback_channel")
    if not fallback_channel:
        _fail_job(job.name, "PrinterOffline",
                  "Channel {0} dispatch failed: {1}".format(
                      route_ctx["channel"], str(primary_error)[:300]),
                  rule=route_ctx.get("rule"), station=job.station,
                  scan_code=job.scan_code)
        return

    fallback_ctx = {
        "channel": fallback_channel,
        "printer": route_ctx.get("fallback_printer"),
        "fallback_channel": None,
        "fallback_printer": None,
        "station": job.station,
        "rule": route_ctx.get("rule"),
        "params": params or {},
    }
    frappe.db.set_value("Zprint Print Job", job.name, {
        "degraded_from_channel": route_ctx["channel"],
        "channel": fallback_channel,
        "retry_count": cint(job.retry_count) + 1,
    }, update_modified=True)
    job.channel = fallback_channel
    job.degraded_from_channel = route_ctx["channel"]
    try:
        dispatch_to_channel(job, fallback_ctx)
    except Exception as fallback_error:
        _fail_job(job.name, "PrinterOffline",
                  "Fallback channel {0} also failed: {1}".format(
                      fallback_channel, str(fallback_error)[:300]),
                  rule=fallback_ctx.get("rule"), station=job.station,
                  scan_code=job.scan_code)
        return
    _write_dispatch_log_quietly(job, fallback_ctx, params)


def dispatch_or_fallback(job, route_ctx, error_message, params=None):
    """Fallback path for ASYNC vendor submit failures (B/C workers).

    Mirrors _dispatch_with_fallback for errors that surface after the render
    worker already returned: one explicit hop to the configured fallback
    channel, otherwise the job fails.
    """
    fallback_channel = route_ctx.get("fallback_channel")
    if not fallback_channel:
        _fail_job(job.name, "PrinterOffline", error_message[:500],
                  rule=route_ctx.get("rule"), station=job.station,
                  scan_code=job.scan_code)
        return
    frappe.db.rollback()
    job = _load_job(job.name)
    if not job or job.status in TERMINAL_STATUSES:
        return
    fallback_ctx = {
        "channel": fallback_channel,
        "printer": route_ctx.get("fallback_printer"),
        "fallback_channel": None,
        "fallback_printer": None,
        "station": job.station,
        "rule": route_ctx.get("rule"),
    }
    frappe.db.set_value("Zprint Print Job", job.name, {
        "degraded_from_channel": route_ctx["channel"],
        "channel": fallback_channel,
        "status": "queued",
        "retry_count": cint(job.retry_count) + 1,
    }, update_modified=True)
    frappe.db.commit()
    job = _load_job(job.name)
    try:
        dispatch_to_channel(job, fallback_ctx)
    except Exception as fallback_error:
        frappe.db.rollback()
        _fail_job(job.name, "PrinterOffline",
                  "Fallback channel {0} also failed: {1}".format(
                      fallback_channel, str(fallback_error)[:300]),
                  rule=fallback_ctx.get("rule"), station=job.station,
                  scan_code=job.scan_code)
        frappe.db.commit()
        return
    frappe.db.commit()
    # Log write is a separate failure domain: it must not re-trigger a
    # dispatch hop when the paper is already on its way.
    _write_dispatch_log_quietly(job, fallback_ctx, params or {})
    frappe.db.commit()


def build_printer_snapshot(route_ctx):
    """Flattened printer facts stored on the Super Print Log row."""
    printer = route_ctx.get("printer") or {}
    snapshot = {
        "channel": route_ctx.get("channel"),
        "printer": printer.get("name"),
        "logical_name": printer.get("logical_name"),
    }
    for key in ("os_printer_name", "paper", "bin", "monochrome", "uri", "media",
                "payload_kind", "cloud_device"):
        if printer.get(key):
            snapshot[key] = printer.get(key)
    device = printer.get("device") or {}
    if device:
        snapshot["device_type"] = device.get("device_type")
        snapshot["device_sn"] = device.get("name")
    return snapshot


def _artifact_byte_size(job):
    """Delivered PDF size in bytes whether inline (base64) or a private file."""
    if job.pdf_data:
        return len(job.pdf_data) * 3 // 4
    if job.pdf_file:
        try:
            return os.path.getsize(job.pdf_file)
        except OSError:
            return 0
    return 0


def write_dispatch_log(job, route_ctx, params, task_status=None):
    """Create the Super Print Log row for a dispatched scan job.

    export_type=ScanPrint; print_count stays the user-intended copies
    (merged-pages rendering multiplies pages, not the count). Degraded jobs
    note the original channel. The row is upsert-by-job_name so ack/harvest
    updates find it.
    """
    existing = frappe.db.get_value("Super Print Log", {"job_name": job.name}, "name")
    # receipt_level must reflect the row state at log time, not the stale
    # in-memory copy (channel A's dispatch already advanced it to 'ready').
    current_status = frappe.db.get_value("Zprint Print Job", job.name, "status")
    payload = {
        "doctype": "Super Print Log",
        "reference_doctype": job.ref_doctype,
        "reference_name": job.ref_docname,
        "print_design": job.design_name or "",
        "export_type": "ScanPrint",
        "print_time": frappe.utils.now_datetime(),
        "print_user": job.print_user,
        "print_count": cint(job.copies) or 1,
        "parameters_used": json.dumps(params or {}, ensure_ascii=False),
        "print_preview_html": "",
        "channel": route_ctx.get("channel"),
        "job_name": job.name,
        "station": job.station,
        "copies_mode": "merged_pages",
        "receipt_level": current_status or job.status,
        "task_status": task_status or "",
        "source_terminal": job.station,
        "executor_terminal": route_ctx.get("channel"),
        "degraded_from_channel": job.degraded_from_channel,
        "pdf_path": job.pdf_file or "",
        "pdf_sha256": job.pdf_sha256,
        "pdf_bytes": _artifact_byte_size(job),
        "printer_snapshot": json.dumps(build_printer_snapshot(route_ctx), ensure_ascii=False),
    }
    if existing:
        frappe.db.set_value("Super Print Log", existing, {
            k: v for k, v in payload.items() if k != "doctype"
        }, update_modified=True)
        return existing
    doc = frappe.get_doc(payload)
    doc.insert(ignore_permissions=True)
    return doc.name


def update_log_receipt(job_name, receipt_level, task_status=None, vendor_task_id=None):
    """Advance the log row as ack/harvest moves the job toward its terminal."""
    existing = frappe.db.get_value("Super Print Log", {"job_name": job_name}, "name")
    if not existing:
        return
    updates = {"receipt_level": receipt_level}
    if task_status is not None:
        updates["task_status"] = task_status
    if vendor_task_id is not None:
        updates["vendor_task_id"] = vendor_task_id
    frappe.db.set_value("Super Print Log", existing, updates, update_modified=True)


# ---------------------------------------------------------------------------
# Polling endpoints
# ---------------------------------------------------------------------------

def _job_view(job_name):
    """One job flattened for the four-state banner (plan 5)."""
    job = _load_job(job_name)
    if not job:
        return None
    view = {
        "job_id": job.name,
        "status": job.status,
        "channel": job.channel,
        "receipt_level": job.receipt_level,
        "scan_time": str(job.scan_time or ""),
        "printer_name": "",
        "fail_reason": "",
        "agent_heartbeat_age": None,
    }
    log_row = frappe.db.get_value(
        "Super Print Log", {"job_name": job.name},
        ["printer_snapshot", "task_status"], as_dict=True)
    if log_row and log_row.printer_snapshot:
        try:
            snapshot = json.loads(log_row.printer_snapshot)
            view["printer_name"] = snapshot.get("os_printer_name") \
                or snapshot.get("logical_name") or snapshot.get("printer") or ""
        except (ValueError, TypeError):
            pass
    if job.status == "failed":
        view["fail_reason"] = _fail_reason(job)
    if job.station:
        agent = frappe.db.get_value(
            "Zprint Agent", {"station": job.station},
            ["last_heartbeat", "status"], as_dict=True)
        if agent and agent.last_heartbeat:
            delta = (frappe.utils.now_datetime()
                     - frappe.utils.get_datetime(agent.last_heartbeat)).total_seconds()
            view["agent_heartbeat_age"] = int(max(delta, 0))
            view["agent_status"] = agent.status
    return view


def _fail_reason(job):
    if job.ack_code:
        return job.ack_code
    events = frappe.get_all(
        "Zprint Scan Event",
        filters={
            "station": job.station,
            "scan_value": (job.scan_code or "")[:140],
        },
        order_by="event_time desc",
        limit=1,
        pluck="message",
    )
    return events[0] if events else ""


@frappe.whitelist()
def get_job_status(job_ids_json=None):
    """Batch status poll for the scan page (session user, station-scoped)."""
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Authentication failed"), frappe.AuthenticationError)
    try:
        job_ids = json.loads(job_ids_json or "[]")
    except (ValueError, TypeError):
        frappe.throw(_("job_ids_json is not valid JSON"))
    if not isinstance(job_ids, list):
        frappe.throw(_("job_ids_json must be a JSON array"))
    job_ids = [str(j) for j in job_ids if j][:MAX_JOB_IDS_PER_POLL]
    if not job_ids:
        return {"jobs": [], "server_time": frappe.utils.now_datetime().strftime("%Y-%m-%d %H:%M:%S")}

    is_manager = "System Manager" in frappe.get_roles(user)
    filters = {"name": ("in", job_ids)}
    if not is_manager:
        stations = frappe.get_all(
            "Zprint Station", filters={"bound_user": user}, pluck="name")
        if not stations:
            return {"jobs": [], "server_time": frappe.utils.now_datetime().strftime("%Y-%m-%d %H:%M:%S")}
        filters["station"] = ("in", stations)
    rows = frappe.get_all("Zprint Print Job", filters=filters, pluck="name", limit=0)
    jobs = [_job_view(name) for name in rows]
    return {
        "jobs": [j for j in jobs if j],
        "server_time": frappe.utils.now_datetime().strftime("%Y-%m-%d %H:%M:%S"),
    }


@frappe.whitelist()
def page_bootstrap():
    """Scan page startup: station identity + feature switches (session only)."""
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Authentication failed"), frappe.AuthenticationError)
    station = _session_station()
    entitled, reason_code = has_silent_print_entitlement()
    return {
        "server_time": frappe.utils.now_datetime().strftime("%Y-%m-%d %H:%M:%S"),
        "scan_print_enabled": cint(_setting("scan_print_enabled", 0)),
        "entitled": entitled,
        "reason_code": reason_code,
        "poll_interval_sec": cint(_setting("poll_interval_sec", 3)) or 3,
        "station": None if not station else {
            "station_id": station.name,
            "station_name": station.station_name or station.name,
            "client_type": station.client_type,
            "wedge_suffix": station.wedge_suffix or "enter",
            "default_channel": station.default_channel,
        },
    }
