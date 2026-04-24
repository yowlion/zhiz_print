# -*- coding: utf-8 -*-
# Copyright (c) 2026, Zhiz Print
# Batch printing API endpoints

from __future__ import unicode_literals
import frappe
from frappe import _
import json


@frappe.whitelist()
def check_batch_print_enabled(doctype):
    """Check if super print is enabled for a given doctype and return available designs."""
    pd = (frappe.boot.zhiz_print or {}).get("print_designer", {})
    if not pd or not pd.get("enabled"):
        return {"enabled": False, "designs": []}

    enable_mode = pd.get("enable_mode")
    if enable_mode == "Enable for Specific":
        enabled_doctypes = pd.get("enabled_doctypes", [])
        if doctype not in enabled_doctypes:
            return {"enabled": False, "designs": []}

    designs = frappe.get_all(
        "Super Print Design",
        filters={"target_doctype": doctype, "enabled": 1},
        fields=["name", "design_name", "print_paper"],
        order_by="design_name",
    )

    for d in designs:
        if d.print_paper:
            paper = frappe.db.get_value(
                "Super Print Paper",
                d.print_paper,
                ["width", "height", "margin_top", "margin_bottom", "margin_left", "margin_right"],
                as_dict=True,
            )
            if paper:
                d["paper_width"] = paper.width
                d["paper_height"] = paper.height
                d["margin_top"] = paper.margin_top or 0
                d["margin_bottom"] = paper.margin_bottom or 0
                d["margin_left"] = paper.margin_left or 0
                d["margin_right"] = paper.margin_right or 0

        params_count = frappe.db.count("Super Print Design Parameter", {"parent": d.name})
        d["has_parameters"] = params_count > 0
        if d.has_parameters:
            d["parameters"] = frappe.get_all(
                "Super Print Design Parameter",
                filters={"parent": d.name},
                fields=["param_name", "param_label", "param_type", "default_value", "reqd", "options"],
            )

    return {"enabled": True, "designs": designs}


@frappe.whitelist()
def batch_render_preview(doctype, docnames, design_name, params=None):
    """Render preview HTML for multiple documents."""
    from zhiz_print.api.print_designer import _check_license, _render_print_html

    _check_license()

    if isinstance(docnames, str):
        docnames = json.loads(docnames)
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except (json.JSONDecodeError, TypeError):
            params = {}

    design = frappe.get_doc("Super Print Design", design_name)
    paper_width, paper_height = 210, 297
    margin_top = margin_bottom = margin_left = margin_right = 0

    if design.print_paper:
        paper = frappe.get_doc("Super Print Paper", design.print_paper)
        paper_width = paper.width
        paper_height = paper.height
        margin_top = paper.margin_top or 0
        margin_bottom = paper.margin_bottom or 0
        margin_left = paper.margin_left or 0
        margin_right = paper.margin_right or 0

    results = []
    errors = []

    for docname in docnames:
        try:
            html, _ = _render_print_html(doctype, docname, design_name, params, skip_px_scaling=True)
            results.append({
                "docname": docname,
                "html": html,
                "paper_width": paper_width,
                "paper_height": paper_height,
                "margin_top": margin_top,
                "margin_bottom": margin_bottom,
                "margin_left": margin_left,
                "margin_right": margin_right,
            })
        except Exception as e:
            frappe.log_error(f"Batch render failed for {doctype} {docname}: {e}")
            errors.append({"docname": docname, "error": str(e)})

    return {"results": results, "errors": errors}


@frappe.whitelist()
def batch_generate_pdf(doctype, docnames, design_name, params=None):
    """Generate merged PDF for multiple documents."""
    from zhiz_print.api.print_designer import _check_license

    _check_license()

    if isinstance(docnames, str):
        docnames = json.loads(docnames)
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except (json.JSONDecodeError, TypeError):
            params = {}

    engine_mode = frappe.db.get_single_value("Zprint Setting", "pdf_engine_mode") or "wkhtmltopdf"

    pdf_bytes_list = []
    design = frappe.get_doc("Super Print Design", design_name)

    for docname in docnames:
        try:
            pdf_bytes = _generate_single_pdf(engine_mode, doctype, docname, design_name, params)
            if pdf_bytes:
                pdf_bytes_list.append(pdf_bytes)
        except Exception as e:
            frappe.log_error(f"Batch PDF failed for {doctype} {docname}: {e}")

    if not pdf_bytes_list:
        frappe.throw(_("All documents failed to generate PDF"))

    if len(pdf_bytes_list) == 1:
        merged_pdf = pdf_bytes_list[0]
    else:
        from PyPDF2 import PdfWriter, PdfReader
        import io

        writer = PdfWriter()
        for pdf_bytes in pdf_bytes_list:
            reader = PdfReader(io.BytesIO(pdf_bytes))
            for page in reader.pages:
                writer.add_page(page)
        output = io.BytesIO()
        writer.write(output)
        merged_pdf = output.getvalue()

    filename = f"batch-{doctype}-{len(pdf_bytes_list)}docs-{design.design_name}.pdf"
    frappe.local.response.filename = filename
    frappe.local.response.filecontent = merged_pdf
    frappe.local.response.type = "pdf"


def _generate_single_pdf(engine_mode, doctype, docname, design_name, params):
    """Generate PDF for a single document and return bytes (not set response)."""
    if engine_mode == "WeasyPrint":
        return _generate_single_pdf_weasyprint(doctype, docname, design_name, params)
    elif engine_mode == "Chromium":
        return _generate_single_pdf_chromium(doctype, docname, design_name, params)
    else:
        return _generate_single_pdf_wkhtmltopdf(doctype, docname, design_name, params)


def _generate_single_pdf_weasyprint(doctype, docname, design_name, params):
    from weasyprint import HTML as WeasyHTML
    from zhiz_print.api.print_designer import _render_print_html, _fix_merged_cell_borders_for_pdf

    html, design = _render_print_html(doctype, docname, design_name, params)
    html = _fix_merged_cell_borders_for_pdf(html)
    return WeasyHTML(string=html).write_pdf()


def _generate_single_pdf_wkhtmltopdf(doctype, docname, design_name, params):
    import pdfkit
    from zhiz_print.api.print_designer import _render_print_html, _prepare_html_for_wkhtmltopdf

    html, design = _render_print_html(doctype, docname, design_name, params)
    html = _prepare_html_for_wkhtmltopdf(html)

    options = {
        "quiet": "",
        "encoding": "UTF-8",
        "print-media-type": "",
        "background": "",
        "images": "",
        "disable-smart-shrinking": "",
        "margin-top": "0",
        "margin-bottom": "0",
        "margin-left": "0",
        "margin-right": "0",
    }
    if design.print_paper:
        paper = frappe.get_doc("Super Print Paper", design.print_paper)
        options["page-width"] = f"{paper.width}mm"
        options["page-height"] = f"{paper.height}mm"

    return pdfkit.from_string(html, False, options=options)


def _generate_single_pdf_chromium(doctype, docname, design_name, params):
    import os
    import subprocess
    import tempfile
    from zhiz_print.api.print_designer import _render_print_html

    html, design = _render_print_html(doctype, docname, design_name, params)

    chromium_cmd = None
    for cmd in ['/snap/bin/chromium', 'chromium-browser', 'chromium', 'google-chrome', 'google-chrome-stable']:
        try:
            result = subprocess.run(['which', cmd], capture_output=True, text=True, timeout=5)
            if result.returncode == 0 and result.stdout.strip():
                chromium_cmd = result.stdout.strip()
                break
        except Exception:
            continue

    if not chromium_cmd:
        frappe.throw(_("Chromium is not installed"))

    tmpdir = os.path.abspath(os.path.join(frappe.get_site_path(), 'public', 'files', 'pdf_debug'))
    os.makedirs(tmpdir, exist_ok=True)
    html_path = os.path.join(tmpdir, f'chrome_batch_{docname}.html')
    pdf_path = os.path.join(tmpdir, f'chrome_batch_{docname}.pdf')

    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html)

    if os.path.exists(pdf_path):
        os.remove(pdf_path)

    chrome_args = [
        chromium_cmd,
        '--headless=new',
        '--disable-gpu',
        '--no-sandbox',
        '--print-to-pdf=' + pdf_path,
        '--no-pdf-header-footer',
    ]
    if design.print_paper:
        paper = frappe.get_doc('Super Print Paper', design.print_paper)
        chrome_args.append('--print-to-pdf-options=' + json.dumps({
            'paperWidth': round(paper.width / 25.4, 4),
            'paperHeight': round(paper.height / 25.4, 4),
            'marginTop': 0, 'marginBottom': 0, 'marginLeft': 0, 'marginRight': 0,
        }))
    chrome_args.append('file://' + html_path)
    subprocess.run(chrome_args, capture_output=True, text=True, timeout=30)

    if not os.path.exists(pdf_path):
        return None

    with open(pdf_path, 'rb') as f:
        return f.read()


@frappe.whitelist()
def batch_export_excel(doctype, docnames, design_name, params=None):
    """Export multi-sheet Excel for multiple documents."""
    from zhiz_print.api.print_designer import _check_license
    from io import BytesIO
    from bs4 import BeautifulSoup
    import openpyxl
    from zhiz_print.api.print_designer import _extract_css_rules, _write_table_to_excel, _set_column_widths

    _check_license()

    if isinstance(docnames, str):
        docnames = json.loads(docnames)
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except (json.JSONDecodeError, TypeError):
            params = {}

    design = frappe.get_doc("Super Print Design", design_name)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    errors = []

    for idx, docname in enumerate(docnames):
        try:
            if not frappe.has_permission(doctype, "print", docname):
                errors.append({"docname": docname, "error": "No print permission"})
                continue

            html = design.get_preview_for_document(doc_name=docname, params=params)
            soup = BeautifulSoup(html, 'html.parser')
            css_rules = _extract_css_rules(soup)

            sheet_name = str(docname)[:31]
            ws = wb.create_sheet(title=sheet_name)

            pages = []
            for page_div in soup.find_all('div', class_='print-page'):
                content_div = page_div.find('div', class_='print-page-content')
                if not content_div:
                    continue
                table = content_div.find('table', class_='print-form-table')
                if table:
                    pages.append(table)

            if not pages:
                ws.cell(row=1, column=1, value="No Data")
                continue

            header_count = 0
            if len(pages) > 1:
                rows_1 = [tr.get_text(strip=True) for tr in pages[0].find_all('tr')]
                rows_2 = [tr.get_text(strip=True) for tr in pages[1].find_all('tr')]
                for i in range(min(len(rows_1), len(rows_2))):
                    if rows_1[i] == rows_2[i]:
                        header_count = i + 1
                    else:
                        break

            current_row = 1
            for page_idx, table in enumerate(pages):
                skip = header_count if page_idx > 0 else 0
                if page_idx == 0:
                    _set_column_widths(ws, table, css_rules)
                rows_written = _write_table_to_excel(ws, table, current_row, css_rules, skip_rows=skip)
                current_row += rows_written

        except Exception as e:
            frappe.log_error(f"Batch Excel failed for {doctype} {docname}: {e}")
            errors.append({"docname": docname, "error": str(e)})

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    frappe.local.response.filename = f"batch-{doctype}-{len(docnames)}docs-{design.design_name}.xlsx"
    frappe.local.response.filecontent = output.getvalue()
    frappe.local.response.type = "binary"


@frappe.whitelist()
def batch_record_print_log(doctype, docnames, design_name, params=None, export_type='Print'):
    """Record individual print logs for each document in the batch."""
    from zhiz_print.api.print_designer import _check_license

    _check_license()

    if isinstance(docnames, str):
        docnames = json.loads(docnames)
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except (json.JSONDecodeError, TypeError):
            params = {}

    results = []
    for docname in docnames:
        try:
            existing_count = frappe.db.count("Super Print Log", filters={
                "reference_doctype": doctype,
                "reference_name": docname,
            })
            log = frappe.get_doc({
                "doctype": "Super Print Log",
                "reference_doctype": doctype,
                "reference_name": docname,
                "print_design": design_name,
                "export_type": export_type,
                "print_count": existing_count + 1,
                "print_user": frappe.session.user,
            })
            log.insert(ignore_permissions=True)
            results.append({
                "docname": docname,
                "log_name": log.name,
                "print_count": existing_count + 1,
            })
        except Exception as e:
            frappe.log_error(f"Batch log failed for {doctype} {docname}: {e}")

    frappe.db.commit()
    return {"results": results}


@frappe.whitelist()
def batch_get_print_logs(doctype, docnames):
    """Get aggregated print logs for multiple documents."""
    from zhiz_print.api.print_designer import _check_license

    _check_license()

    if isinstance(docnames, str):
        docnames = json.loads(docnames)

    if not docnames:
        return {"logs": [], "total_count": 0}

    filters = {"reference_doctype": doctype, "reference_name": ["in", docnames]}
    total_count = frappe.db.count("Super Print Log", filters=filters)
    logs = frappe.get_all(
        "Super Print Log",
        filters=filters,
        fields=["name", "reference_name", "print_design", "print_time", "print_user", "print_count", "export_type"],
        order_by="print_time desc",
        limit=100,
    )

    for log in logs:
        if log.print_user:
            log.user_fullname = frappe.db.get_value("User", log.print_user, "full_name") or log.print_user
        else:
            log.user_fullname = ''

    return {"logs": logs, "total_count": total_count}
