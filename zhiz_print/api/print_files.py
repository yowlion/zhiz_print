# -*- coding: utf-8 -*-
# Copyright (c) 2026, Zhiz Print and contributors
# For license information, please see license.txt

# Private-file foundation for scan silent printing (scan-print plan 7-3).
# Rendered scan PDFs live under the site private area
# (private/files/scan_pdf) - NEVER under public/files, which nginx serves
# unauthenticated. Readers go through fetch_pdf() with an unguessable,
# short-TTL Redis token that renews on every use; Range requests are
# honored so flaky agent/PDA links can resume a partial download inside
# the token TTL (integrity is verified out of band via the job
# pdf_sha256).

import hashlib
import os
import re
import tempfile

import frappe
from frappe import _
from werkzeug.wrappers import Response

PDF_TOKEN_KEY_PREFIX = "zhiz_print:pdf_token:"
PDF_TOKEN_DEFAULT_TTL = 600

# Zprint Print Job primary keys are sha256 hex (scan-print plan 8); the
# accepted charset is kept tight (alnum/underscore/dash only, no dots, no
# separators) so path traversal via job_name is structurally impossible.
_SAFE_JOB_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,256}$")
# Tokens are frappe.utils.random_string(32): exactly 32 alphanumerics.
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9]{32}$")

_RESPONSE_CHUNK_BYTES = 65536

# Sentinel: the Range header was syntactically valid but cannot be satisfied
# (caller answers 416). A malformed/unsupported header is instead ignored and
# the full body is served, per RFC 7233.
_RANGE_UNSATISFIABLE = object()


def scan_pdf_dir(create=True):
    """Absolute path of the site-private scan PDF directory.

    Under private/files (not public): nginx /files/ cannot serve it, so
    fetch_pdf() with a one-time token is the only reader. Set create=False
    for read-only paths (never create the directory on a read request).
    """
    path = os.path.abspath(frappe.get_site_path("private", "files", "scan_pdf"))
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def _safe_job_name(job_name):
    job_name = str(job_name or "")
    if not _SAFE_JOB_NAME_RE.match(job_name):
        frappe.throw(_("Invalid job name"), frappe.ValidationError)
    return job_name


def write_scan_pdf(job_name, pdf_bytes):
    """Write rendered scan PDF bytes to private/files/scan_pdf/{job_name}.pdf.

    The write is atomic (temp file in the same directory, then os.replace)
    so a concurrent token download can never observe a half-written file.

    Returns (absolute_path, sha256_hex, size_bytes).
    """
    if not pdf_bytes:
        frappe.throw(_("Empty PDF payload"), frappe.ValidationError)
    job_name = _safe_job_name(job_name)
    target_dir = scan_pdf_dir()
    path = os.path.join(target_dir, job_name + ".pdf")

    fd, tmp_path = tempfile.mkstemp(prefix=".scan_pdf_", dir=target_dir)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(pdf_bytes)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise

    return (path, hashlib.sha256(pdf_bytes).hexdigest(), len(pdf_bytes))


def make_pdf_token(job_name, ttl=PDF_TOKEN_DEFAULT_TTL):
    """Create a short-TTL download token bound to job_name.

    Redis SET NX EX: the key only lands if it does not already exist, and it
    expires after ttl seconds. Tokens are unguessable (random_string(32)) so
    an NX collision means a 160-bit+ birthday event; a few retries cover it.
    validate_pdf_token() renews the TTL on every fetch so a Range resume of
    the same download keeps working inside the window.

    Returns the token string.
    """
    job_name = _safe_job_name(job_name)
    ttl = frappe.utils.cint(ttl) or PDF_TOKEN_DEFAULT_TTL
    cache = frappe.cache()
    for _attempt in range(3):
        token = frappe.utils.random_string(32)
        key = cache.make_key(PDF_TOKEN_KEY_PREFIX + token)
        if cache.set(key, job_name, ex=ttl, nx=True):
            return token
    frappe.throw(_("Could not allocate download token"), frappe.ValidationError)


def validate_pdf_token(token, ttl=PDF_TOKEN_DEFAULT_TTL):
    """Validate a download token and slide its TTL forward; return job_name.

    NOT single-use by design: a consume-once token contradicts Range resume
    (the first, interrupted request would already have burned the token, so
    a resumed download could never succeed). Instead the token stays bound
    to one job, expires after ttl seconds of inactivity, and download
    integrity is guaranteed by the job's pdf_sha256, which the agent/PDA
    verifies. Raw redis get/expire is used on purpose - the get_value()
    local-cache layer would hand a stale value to a second worker.

    Returns None for unknown / expired tokens.
    """
    token = str(token or "")
    if not _SAFE_TOKEN_RE.match(token):
        return None
    ttl = frappe.utils.cint(ttl) or PDF_TOKEN_DEFAULT_TTL
    cache = frappe.cache()
    key = cache.make_key(PDF_TOKEN_KEY_PREFIX + token)
    pipe = cache.pipeline(True)
    pipe.get(key)
    pipe.expire(key, ttl)
    value = pipe.execute()[0]
    if not value:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    return value


def _parse_range_header(header, total):
    """Parse a bytes Range header against a resource of total bytes.

    Returns an inclusive (start, end) tuple, _RANGE_UNSATISFIABLE when the
    header is syntactically valid but cannot be satisfied (416), or None when
    absent/malformed (caller serves the full body - RFC 7233 says ignore
    what cannot be understood). Supports "bytes=start-end", "bytes=start-"
    and suffix "bytes=-N"; multi-range requests are treated as malformed.
    """
    if not header:
        return (0, total - 1)
    match = re.match(r"^bytes=(\d*)-(\d*)$", str(header).strip())
    if not match:
        return None
    if total <= 0:
        return _RANGE_UNSATISFIABLE
    start_s, end_s = match.group(1), match.group(2)
    if start_s == "" and end_s == "":
        return None
    if start_s == "":
        suffix_length = int(end_s)
        if suffix_length <= 0:
            return _RANGE_UNSATISFIABLE
        return (max(total - suffix_length, 0), total - 1)
    start = int(start_s)
    if start >= total:
        return _RANGE_UNSATISFIABLE
    end = int(end_s) if end_s else total - 1
    if end >= total:
        end = total - 1
    if end < start:
        return None
    return (start, end)


def _file_chunk_iter(path, start, length):
    """Yield length bytes from path starting at start, in bounded chunks,
    so a response body streams instead of being held in one buffer."""
    remaining = length
    with open(path, "rb") as f:
        f.seek(start)
        while remaining > 0:
            chunk = f.read(min(_RESPONSE_CHUNK_BYTES, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


@frappe.whitelist()
def fetch_pdf(token=None):
    """Stream a scan PDF by short-TTL token (scan-print plan 7-3).

    Validates the token and slides its TTL forward (validate_pdf_token),
    then serves private/files/scan_pdf/{job_name}.pdf with Range support
    (206 Partial Content) and Content-Disposition: inline. The token stays
    valid after a fetch, so an interrupted download can resume with a Range
    request inside the TTL; integrity is verified via the job pdf_sha256.
    An invalid or expired token answers 403; a valid-token-but-missing file
    answers 404 without invalidating the token.
    """
    job_name = validate_pdf_token(token)
    if not job_name:
        return Response(
            _("Invalid or expired download token"),
            status=403,
            content_type="text/plain; charset=utf-8",
        )

    job_name = _safe_job_name(job_name)
    path = os.path.join(scan_pdf_dir(create=False), job_name + ".pdf")
    if not os.path.isfile(path):
        return Response(
            _("PDF file not found"),
            status=404,
            content_type="text/plain; charset=utf-8",
        )

    total = os.path.getsize(path)
    span = _parse_range_header(frappe.get_request_header("Range"), total)
    if span is _RANGE_UNSATISFIABLE:
        response = Response(status=416)
        response.headers["Content-Range"] = "bytes */{0}".format(total)
        return response
    if span is None:
        span = (0, total - 1)

    start, end = span
    length = end - start + 1
    status = 200 if (start == 0 and end == total - 1) else 206

    response = Response(_file_chunk_iter(path, start, length), status=status)
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Length"] = str(length)
    response.headers["Accept-Ranges"] = "bytes"
    response.headers["Content-Disposition"] = 'inline; filename="{0}"'.format(
        job_name + ".pdf"
    )
    if status == 206:
        response.headers["Content-Range"] = "bytes {0}-{1}/{2}".format(start, end, total)
    return response
