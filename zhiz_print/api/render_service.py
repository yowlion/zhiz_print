# -*- coding: utf-8 -*-
# Copyright (c) 2026, Zhiz Print and contributors
# For license information, please see license.txt

# Thin render facade for the scan silent-print trunk (scan-print plan 1-5).
# Wraps the existing desk pipeline - print_designer._render_print_html per
# document plus batch_print._generate_chromium_pdf (per-request unique temp
# files under the site private tmp) - so callers get raw PDF bytes without
# duplicating any render logic.

import json
import re

import frappe
from frappe.utils import cint


def enforce_template_asset_budget(html):
    """Hard gate on rendered template size (plan 1-5: template_asset_budget_bytes).

    The budget exists so a huge Super Print Design (inline base64 images,
    generated tables) cannot be pushed into headless Chromium on a small
    cloud box and OOM it. 0 / unset disables the check. Raises
    frappe.ValidationError so the scan trunk records RenderFail.
    """
    budget = cint(frappe.db.get_single_value(
        "Zprint Setting", "template_asset_budget_bytes")) or 0
    if not budget or not html:
        return
    size = len(html)
    if size > budget:
        frappe.throw(
            "Rendered template size {0} bytes exceeds the configured template "
            "asset budget of {1} bytes".format(size, budget),
            frappe.ValidationError,
        )


def render_docs_to_pdf(doctype, docnames, design_name, params=None, copies=1, timeout_s=None):
    """Render one or more documents into a single merged PDF; return bytes.

    Reuses the exact pipeline the desk batch print uses:
    - print_designer._render_print_html per document (mm-true paper size,
      px scaling, rotation untouched),
    - the Chromium headless PDF pipeline batch_print._generate_chromium_pdf
      (mkstemp input/output files, finally-removed, site private tmp).

    Copies semantics (plan 0, copies_mode=merged_pages): DEVMODE is always
    1. Headless Chromium --print-to-pdf has no device copies dialog, so N
    copies are produced by concatenating each document's rendered
    page-wrapper body N times with explicit page-break separators - one
    Chromium pass emits len(docnames) x copies pages, doc-major order
    (each document's copies stay contiguous).

    Policy gates are intentionally NOT applied here; the scan trunk owns
    them in its own step order: has_silent_print_entitlement, per-doc draft
    block (block_draft is per Scan Rule), template asset budget, render
    semaphore/budget. Note _render_print_html does enforce
    frappe.has_permission(doctype, "print", docname) for the current user.

    :param doctype: target DocType
    :param docnames: list of doc names (a JSON-encoded list string is accepted)
    :param design_name: Super Print Design name
    :param params: design parameters dict (a JSON string is accepted)
    :param copies: >= 1 merged-page copies
    :param timeout_s: Chromium subprocess budget override in seconds; None
        falls back to Zprint Setting.scan_render_budget_s via
        print_designer.get_render_timeout()
    :returns: PDF bytes (raises on empty render output)
    """
    if isinstance(docnames, str):
        try:
            docnames = json.loads(docnames)
        except (json.JSONDecodeError, TypeError):
            docnames = []
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except (json.JSONDecodeError, TypeError):
            params = {}

    docnames = [d for d in (docnames or []) if d]
    copies = cint(copies) or 1
    if copies < 1:
        copies = 1
    if not docnames:
        frappe.throw("render_docs_to_pdf: no document names given")
    if not design_name:
        frappe.throw("render_docs_to_pdf: design name is required")

    from zhiz_print.api.print_designer import _render_print_html
    from zhiz_print.api.batch_print import _generate_chromium_pdf

    html_parts = []
    styles_collected = []
    styles_seen = set()

    total_blocks = len(docnames) * copies
    emitted = 0
    design = None
    for docname in docnames:
        html, design = _render_print_html(doctype, docname, design_name, params)
        # Fail fast before the (much larger) merged payload reaches Chromium;
        # the check is per rendered document, so merged-page copies and
        # multi-doc batches do not skew the budget.
        enforce_template_asset_budget(html)
        body_match = re.search(r"<body[^>]*>([\s\S]*)</body>", html, re.IGNORECASE)
        body_content = body_match.group(1) if body_match else html
        for style in re.findall(r"<style[^>]*>[\s\S]*?</style>", html, re.IGNORECASE):
            if style not in styles_seen:
                styles_seen.add(style)
                styles_collected.append(style)
        for _copy in range(copies):
            emitted += 1
            if emitted < total_blocks:
                html_parts.append(body_content + '<div style="page-break-after:always"></div>')
            else:
                html_parts.append(body_content)

    combined_html = (
        '<!DOCTYPE html>\n<html>\n<head>\n<meta charset="utf-8">\n'
        + "\n".join(styles_collected)
        + "\n</head>\n<body>\n"
        + "\n".join(html_parts)
        + "\n</body>\n</html>"
    )

    return _generate_chromium_pdf(combined_html, design, timeout_s=timeout_s)
