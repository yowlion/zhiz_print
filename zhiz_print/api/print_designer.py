# -*- coding: utf-8 -*-
# Copyright (c) 2026, Zhiz Print
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe import _
from frappe.utils import cint
import json
import re


def _check_license():
    """Check license validity before allowing print operations.

    Uses check_license_valid() which has 24h cache — only validates remotely
    once per day, uses cached result otherwise. Allows 7-day offline grace.
    """
    from zhiz_print.api.license import check_license_valid
    valid, info = check_license_valid()
    if not valid:
        frappe.throw(_("License expired: {0}").format(
            info.get("message", "Please activate a license.")
        ))


@frappe.whitelist()
def get_available_designs(doctype, docname=None):
    """Get list of available print designs for a DocType"""
    if not doctype:
        return []

    # Get current document instance for enable condition evaluation
    doc = None
    if docname:
        try:
            doc = frappe.get_doc(doctype, docname)
        except Exception:
            pass

    designs = frappe.get_all(
        "Super Print Design",
        filters={
            "target_doctype": doctype,
            "enabled": 1,
        },
        fields=["name", "design_name", "print_paper", "priority"],
        order_by="priority asc, design_name"
    )

    result = []
    for d in designs:
        # Check enable conditions
        if not frappe.get_cached_doc("Super Print Design", d.name).check_enable_conditions(d.name, doc=doc):
            continue

        # Get paper info (with margins)
        paper_info = {}
        if d.print_paper:
            paper_info = frappe.db.get_value("Super Print Paper", d.print_paper,
                                             ["width", "height",
                                              "margin_top", "margin_bottom",
                                              "margin_left", "margin_right"], as_dict=True) or {}

        # Check if parameters exist
        has_params = frappe.db.count("Super Print Design Parameter",
                                      filters={"parent": d.name, "parenttype": "Super Print Design"})

        result.append({
            "name": d.name,
            "design_name": d.design_name,
            "print_paper": d.print_paper,
            "paper_width": paper_info.get("width"),
            "paper_height": paper_info.get("height"),
            "has_parameters": has_params > 0,
            "parameters": get_design_parameters(d.name),
        })

    return result


def get_design_parameters(design_name):
    """Get design parameter definitions"""
    params = frappe.get_all(
        "Super Print Design Parameter",
        filters={"parent": design_name, "parenttype": "Super Print Design"},
        fields=["param_name", "param_label", "param_type", "default_value", "reqd", "options"],
        order_by="idx"
    )
    return params


@frappe.whitelist()
def render_print_preview(doctype, docname, design_name, params=None):
    """Render print preview HTML"""
    _check_license()
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except (json.JSONDecodeError, TypeError):
            params = {}

    design = frappe.get_doc("Super Print Design", design_name)

    # Permission check
    if not frappe.has_permission(doctype, "print", docname):
        frappe.throw(_("No print permission"), frappe.PermissionError)

    # Get paper info (with margins)
    paper = frappe.get_doc("Super Print Paper", design.print_paper)

    # Render HTML
    html = design.get_preview_for_document(doc_name=docname, params=params)

    return {
        "html": html,
        "paper_width": paper.width,
        "paper_height": paper.height,
        "margin_top": paper.margin_top or 0,
        "margin_bottom": paper.margin_bottom or 0,
        "margin_left": paper.margin_left or 0,
        "margin_right": paper.margin_right or 0,
    }


@frappe.whitelist()
def record_print_log(doctype, docname, design_name, params=None, preview_html=None, export_type=None):
    """Record print log"""
    _check_license()
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except (json.JSONDecodeError, TypeError):
            params = {}

    log = frappe.get_doc({
        "doctype": "Super Print Log",
        "reference_doctype": doctype,
        "reference_name": docname,
        "print_design": design_name,
        "parameters_used": json.dumps(params, ensure_ascii=False) if params else "{}",
        "print_preview_html": preview_html or "",
        "export_type": export_type or "Print",
    })
    log.insert(ignore_permissions=True)

    return {
        "log_name": log.name,
        "print_count": log.print_count,
    }


def _fix_merged_cell_borders_for_pdf(html):
    """PDF-specific: fix ghost borders of merged cells.
    1. transparent borders -> remove property so CSS border:none takes effect
    2. merged cells with black borders -> add background:white to cover internal ghost column lines"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, 'html.parser')
    for td in soup.find_all('td'):
        style = td.get('style', '')
        if not style:
            continue
        # Fix 1: transparent borders -> remove border property
        if 'solid transparent' in style:
            style = re.sub(r'\s*border-([a-z]+):\s*[\d.]+\s*px\s+solid\s+transparent;?', '', style)
            style = re.sub(r'\s*border:\s*[\d.]+\s*px\s+solid\s+transparent;?', '', style)
            style = re.sub(r';\s*;', ';', style).strip(';').strip()
        # Fix 2: merged cells with black borders get background:white
        colspan = int(td.get('colspan', 1))
        rowspan = int(td.get('rowspan', 1))
        if (colspan > 1 or rowspan > 1) and 'solid black' in style:
            if 'background' not in style:
                style = style.rstrip(';') + ';background:white'
        td['style'] = style
    # Also fix transparent borders on inner divs
    for div in soup.find_all('div'):
        style = div.get('style', '')
        if not style:
            continue
        if 'solid transparent' in style:
            style = re.sub(r'\s*border-([a-z]+):\s*[\d.]+\s*px\s+solid\s+transparent;?', '', style)
            style = re.sub(r'\s*border:\s*[\d.]+\s*px\s+solid\s+transparent;?', '', style)
            style = re.sub(r';\s*;', ';', style).strip(';').strip()
            div['style'] = style
    return str(soup)


def _resolve_image_urls_for_pdf(html):
    """Convert relative image URLs to file:// absolute paths for PDF engines.
    Handles /private/files/... and /files/... paths."""
    import os

    site_path = frappe.get_site_path()
    public_path = os.path.join(site_path, 'public')

    def _resolve(match):
        url = match.group(1) or match.group(2) or match.group(3) or ''
        if not url or url.startswith(('http://', 'https://', 'data:', 'file://')):
            return match.group(0)
        if url.startswith('/private/files/'):
            local = os.path.join(site_path, url.lstrip('/'))
            if os.path.exists(local):
                return match.group(0).replace(url, 'file://' + os.path.abspath(local))
        elif url.startswith('/files/'):
            local = os.path.join(public_path, url.lstrip('/'))
            if os.path.exists(local):
                return match.group(0).replace(url, 'file://' + os.path.abspath(local))
        return match.group(0)

    # Match src="..." in <img> tags and url(...) in CSS
    html = re.sub(r'src="(/[^"]*)"', _resolve, html)
    html = re.sub(r"url\(['\"]?(/[^)'\"]*)['\"]?\)", _resolve, html)
    return html


def _render_print_html(doctype, docname, design_name, params=None, skip_px_scaling=False):
    """Common function: render print HTML and apply px scaling, shared by PDF engines.
    Returns (html, design) tuple."""
    import os

    if isinstance(params, str):
        try:
            params = json.loads(params)
        except (json.JSONDecodeError, TypeError):
            params = {}

    design = frappe.get_doc("Super Print Design", design_name)

    if not frappe.has_permission(doctype, "print", docname):
        frappe.throw(_("No print permission"), frappe.PermissionError)

    html = design.get_preview_for_document(doc_name=docname, params=params)

    if not skip_px_scaling:
        # WeasyPrint px->mm conversion rate: 25.4/96 ~ 0.264583 mm/px
        if design.print_paper:
            wp_scale = (96 / 25.4) / 4  # ≈ 0.9449

            def _scale_px_value(match):
                value = float(match.group(1))
                scaled = round(value * wp_scale, 2)
                if scaled == int(scaled):
                    return f'{int(scaled)}px'
                return f'{scaled}px'

            def _scale_style_content(text):
                urls = []
                def _save_url(m):
                    urls.append(m.group(0))
                    return f'__URL_{len(urls)-1}__'
                text = re.sub(r"url\([^)]*\)", _save_url, text)
                text = re.sub(r'([\d.]+)\s*px', _scale_px_value, text)
                for i, url in enumerate(urls):
                    text = text.replace(f'__URL_{i}__', url)
                return text

            def _scale_style_attr(m):
                return m.group(1) + _scale_style_content(m.group(2)) + m.group(3)
            html = re.sub(r'(style=")([^"]*)(")', _scale_style_attr, html)

            def _scale_style_tag(m):
                return m.group(1) + _scale_style_content(m.group(2)) + m.group(3)
            html = re.sub(r'(<style[^>]*>)(.*?)(</style>)', _scale_style_tag, html, flags=re.DOTALL)

    return html, design


def _pdf_response(pdf_bytes, filename):
    """Construct PDF inline preview response"""
    frappe.local.response.filename = filename
    frappe.local.response.filecontent = pdf_bytes
    frappe.local.response.type = "pdf"


@frappe.whitelist()
def generate_print_pdf(doctype, docname, design_name, params=None):
    """Unified PDF generation endpoint. Auto-selects engine based on Zprint Setting."""
    _check_license()
    engine_mode = frappe.db.get_single_value("Zprint Setting", "pdf_engine_mode") or "wkhtmltopdf"
    if engine_mode == "WeasyPrint":
        return _generate_print_pdf_weasyprint(doctype, docname, design_name, params)
    elif engine_mode == "Chromium":
        return _generate_print_pdf_chromium(doctype, docname, design_name, params)
    else:
        return _generate_print_pdf_wkhtmltopdf(doctype, docname, design_name, params)


def _generate_print_pdf_weasyprint(doctype, docname, design_name, params=None):
    """Generate PDF using WeasyPrint, browser inline preview"""
    from weasyprint import HTML as WeasyHTML

    html, design = _render_print_html(doctype, docname, design_name, params)

    # PDF-specific: fix ghost borders of merged cells
    html = _fix_merged_cell_borders_for_pdf(html)

    # PDF-specific: convert relative image URLs to file:// paths
    html = _resolve_image_urls_for_pdf(html)

    # DEBUG: save final HTML
    import os
    debug_dir = os.path.join(frappe.get_site_path(), 'public', 'files', 'pdf_debug')
    os.makedirs(debug_dir, exist_ok=True)
    with open(os.path.join(debug_dir, '03_weasyprint_final.html'), 'w', encoding='utf-8') as f:
        f.write(html)

    try:
        pdf_bytes = WeasyHTML(string=html).write_pdf()
        _pdf_response(pdf_bytes, f"{docname}-{design.design_name}-WeasyPrint.pdf")
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), 'WeasyPrint PDF generation failed')
        frappe.throw(_("PDF generation failed: {0}").format(str(e)))


def _prepare_html_for_wkhtmltopdf(html):
    """Preprocess HTML for wkhtmltopdf:
    1. SVG data URL -> PNG data URL
    2. background shorthand /size -> split background-size
    3. footer flex -> table
    4. border < 1px -> 1px"""
    import base64
    import cairosvg

    # 1. SVG -> PNG
    def _replace_svg(m):
        b64_data = m.group(1)
        try:
            svg_bytes = base64.b64decode(b64_data)
            png_bytes = cairosvg.svg2png(bytestring=svg_bytes, scale=2)
            png_b64 = base64.b64encode(png_bytes).decode('ascii')
            return "url('data:image/png;base64," + png_b64 + "')"
        except Exception:
            return m.group(0)

    html = re.sub(
        r"url\(['\"]?data:image/svg\+xml;base64,([A-Za-z0-9+/=]+)['\"]?\)",
        _replace_svg, html
    )

    # 2. Fix background shorthand with /size
    def _fix_bg_shorthand(m):
        full_style = m.group(1)
        bg_match = re.search(
            r'background:\s*url\([^)]+\)\s+no-repeat\s+([\w\s]+?)/(contain|cover|[\d.]+(?:px|mm|%)?)',
            full_style
        )
        if bg_match:
            position = bg_match.group(1).strip()
            size = bg_match.group(2).strip()
            old_bg = bg_match.group(0)
            fixed = old_bg.replace(f"/{size}", "").rstrip()
            full_style = full_style.replace(old_bg, fixed)
            full_style = full_style.rstrip(";") + f";background-size:{size}"
        return 'style="' + full_style + '"'

    html = re.sub(r'style="([^"]*background:[^"]*)"', _fix_bg_shorthand, html)

    # 3. Fix footer: flex -> absolute positioned (wkhtmltopdf compatible)
    def _fix_footer(m):
        outer_style = m.group(1)
        inner_html = m.group(2)
        h_match = re.search(r'height:([\d.]+)px', outer_style)
        h_val = h_match.group(1) if h_match else '19'
        # Remove flex, keep position:absolute on outer
        outer_style = outer_style.replace('display:flex;align-items:center;', '')
        # Replace inner flex divs with absolute positioned divs
        def _fix_inner(dm):
            align = dm.group(1)
            pad_side = dm.group(2)
            pad_val = dm.group(3)
            if align == 'left':
                pos = 'position:absolute;left:0;top:0;bottom:0;'
            elif align == 'right':
                pos = 'position:absolute;right:0;top:0;bottom:0;'
            else:
                pos = 'position:absolute;left:0;right:0;top:0;bottom:0;'
            return '<div style="' + pos + 'text-align:' + align + ';padding-' + pad_side + ':' + pad_val + 'px;line-height:' + h_val + 'px;">'
        inner_html = re.sub(
            r'<div style="flex:1;text-align:(left|center|right);padding-(left|right):([\d.]+)px;">',
            _fix_inner, inner_html
        )
        return '<div class="print-page-footer" style="' + outer_style + '">' + inner_html + '</div>'

    html = re.sub(
        r'<div class="print-page-footer" style="([^"]*)">((?:<div[^>]*>[^<]*</div>\s*)+)</div>',
        _fix_footer, html, flags=re.DOTALL
    )

    # 4. Border values < 1px rounded up to 1px
    def _round_border_px(m):
        prefix = m.group(1)
        val = float(m.group(2))
        suffix = m.group(3)
        if 0 < val < 1:
            return prefix + '1' + suffix
        return m.group(0)
    html = re.sub(r'(border-[a-z]+:\s*)([\d.]+)(px)', _round_border_px, html)
    html = re.sub(r'(border:\s*)([\d.]+)(px)', _round_border_px, html)

    # 5. Ceil rounding for font-size, width and height
    import math
    def _ceil_prop_px(m):
        val = float(m.group(2))
        rounded = math.ceil(val)
        if rounded != val:
            return m.group(1) + str(rounded) + m.group(3)
        return m.group(0)
    html = re.sub(r'(font-size:\s*)([\d.]+)(px)', _ceil_prop_px, html)
    html = re.sub(r'(line-height:\s*)([\d.]+)(px)', _ceil_prop_px, html)
    html = re.sub(r'(width:\s*)([\d.]+)(px)', _ceil_prop_px, html)
    html = re.sub(r'(height:\s*)([\d.]+)(px)', _ceil_prop_px, html)


    return html


def _generate_print_pdf_wkhtmltopdf(doctype, docname, design_name, params=None):
    """Generate PDF using wkhtmltopdf, browser inline preview"""
    import pdfkit

    html, design = _render_print_html(doctype, docname, design_name, params)

    # wkhtmltopdf preprocessing: SVG->PNG, background shorthand fix, flex->table
    html = _prepare_html_for_wkhtmltopdf(html)

    # PDF-specific: convert relative image URLs to file:// paths
    html = _resolve_image_urls_for_pdf(html)

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

    try:
        pdf_bytes = pdfkit.from_string(html, False, options=options)
        _pdf_response(pdf_bytes, f"{docname}-{design.design_name}-wkhtmltopdf.pdf")
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), 'wkhtmltopdf PDF generation failed')
        frappe.throw(_("PDF generation failed: {0}").format(str(e)))


def _generate_print_pdf_chromium(doctype, docname, design_name, params=None):
    """Generate PDF using Chromium headless, browser inline preview"""
    import os
    import subprocess
    import tempfile

    html, design = _render_print_html(doctype, docname, design_name, params)

    # PDF-specific: convert relative image URLs to file:// paths
    html = _resolve_image_urls_for_pdf(html)

    # Find available Chromium executable
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
        frappe.throw(_("Chromium is not installed. Please run: apt-get install -y chromium-browser"))

    # Use site files dir (snap Chromium cannot write to /tmp)
    tmpdir = os.path.abspath(os.path.join(frappe.get_site_path(), 'public', 'files', 'pdf_debug'))
    os.makedirs(tmpdir, exist_ok=True)
    html_path = os.path.join(tmpdir, 'chrome_input.html')
    pdf_path = os.path.join(tmpdir, 'chrome_output.pdf')

    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html)

    try:
        # Clean up old PDF
        if os.path.exists(pdf_path):
            os.remove(pdf_path)

        # Build Chromium command
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
        result = subprocess.run(chrome_args, capture_output=True, text=True, timeout=30)

        if not os.path.exists(pdf_path):
            frappe.throw(_("Chromium PDF generation failed"))

        with open(pdf_path, 'rb') as f:
            pdf_bytes = f.read()

        _pdf_response(pdf_bytes, f"{docname}-{design.design_name}-Chromium.pdf")
    except subprocess.TimeoutExpired:
        frappe.throw(_("Chromium PDF generation timed out"))
    except frappe.exceptions.ValidationError:
        raise
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), 'Chromium PDF generation failed')
        frappe.throw(_("PDF generation failed: {0}").format(str(e)))


@frappe.whitelist()
def get_print_log_count(doctype, docname, design_name=None):
    """Get print count"""
    _check_license()
    filters = {
        "reference_doctype": doctype,
        "reference_name": docname,
    }
    if design_name:
        filters["print_design"] = design_name

    return frappe.db.count("Super Print Log", filters=filters)


@frappe.whitelist()
def get_print_log_list(doctype, docname):
    """Get print log list"""
    _check_license()
    filters = {"reference_doctype": doctype, "reference_name": docname}
    total_count = frappe.db.count("Super Print Log", filters=filters)
    logs = frappe.get_all("Super Print Log",
        filters=filters,
        fields=["name", "print_design", "print_time", "print_user", "print_count", "export_type"],
        order_by="print_time desc",
        limit=50
    )
    # Inject user full name
    for log in logs:
        if log.print_user:
            log.user_fullname = frappe.db.get_value("User", log.print_user, "full_name") or log.print_user
        else:
            log.user_fullname = ''

    return {"logs": logs, "total_count": total_count}


# ==================== Design Save/Load API ====================


@frappe.whitelist()
def save_design(design_name, rows, columns, row_styles, col_styles, font_family,
                font_size, page_header_left, page_header_center, page_header_right,
                page_footer_left, page_footer_center, page_footer_right,
                cells=None, design_queries=None):
    """Save design grid data from frontend.

    Frontend sends only non-merged cells. Backend validate_cells() will
    automatically generate merge markers for covered positions.
    """
    _check_license()
    if isinstance(row_styles, str):
        row_styles = json.loads(row_styles)
    if isinstance(col_styles, str):
        col_styles = json.loads(col_styles)
    if isinstance(cells, str):
        cells = json.loads(cells)
    if isinstance(design_queries, str):
        design_queries = json.loads(design_queries)

    doc = frappe.get_doc("Super Print Design", design_name)

    doc.rows = cint(rows)
    doc.columns = cint(columns)
    doc.row_styles = json.dumps(row_styles, ensure_ascii=False) if row_styles else "{}"
    doc.col_styles = json.dumps(col_styles, ensure_ascii=False) if col_styles else "{}"
    doc.font_family = font_family or "Microsoft YaHei"
    doc.font_size = cint(font_size) or 12
    doc.page_header_left = page_header_left or ""
    doc.page_header_center = page_header_center or ""
    doc.page_header_right = page_header_right or ""
    doc.page_footer_left = page_footer_left or ""
    doc.page_footer_center = page_footer_center or ""
    doc.page_footer_right = page_footer_right or ""

    # Clear existing items
    doc.design_items = []

    # Clear and rebuild design_queries
    doc.design_queries = []
    if design_queries:
        for q in design_queries:
            doc.append("design_queries", {
                "query_name": q.get("query_name", ""),
                "query_code": q.get("query_code", ""),
                "parameters": q.get("parameters", ""),
            })

    # Build flat items from cells (only non-merged cells from frontend)
    if cells:
        for cell in cells:
            doc.append("design_items", {
                "cell_id": cell.get("cell_id", ""),
                "row": cint(cell.get("row", 0)),
                "col": cint(cell.get("col", 0)),
                "rowspan": cint(cell.get("rowspan", 1)),
                "colspan": cint(cell.get("colspan", 1)),
                "cell_type": cell.get("cell_type", "static"),
                "cell_value": cell.get("cell_value", ""),
                "cell_options": cell.get("cell_options", ""),
                "css_style": cell.get("css_style", ""),
                "data_key": cell.get("data_key", ""),
                "query_name": cell.get("query_name", ""),
                "barcode_format": cell.get("barcode_format", "CODE128"),
                "barcode_width": cint(cell.get("barcode_width", 100)),
                "barcode_height": cint(cell.get("barcode_height", 40)),
                "row_type": cell.get("row_type", ""),
                "row_display": cell.get("row_display", ""),
            })

    # validate() will call ensure_full_coverage() + validate_cells()
    # which fills missing cells and generates merge markers automatically
    doc.save()

    item_count = len(doc.design_items) if doc.design_items else 0
    return {"success": True, "item_count": item_count}


@frappe.whitelist()
def get_designer_html(design_name=None, rows=20, columns=15, font_family="Microsoft YaHei",
                      print_paper=None, col_styles=None):
    """Render designer shell HTML server-side. Returns the toolbar, paper structure,
    and property panel. Dynamic grid content is filled by JS after loading."""
    import time

    PX_PER_MM = 4
    rows = cint(rows) or 20
    columns = cint(columns) or 15

    if isinstance(col_styles, str):
        try:
            col_styles = json.loads(col_styles)
        except (json.JSONDecodeError, TypeError):
            col_styles = {}
    if not col_styles:
        col_styles = {}

    # Load paper info
    paper_w, paper_h = 210, 297
    m_top, m_bottom, m_left, m_right = 10, 10, 15, 15

    if design_name:
        if frappe.db.exists("Super Print Design", design_name):
            doc = frappe.get_doc("Super Print Design", design_name)
            rows = doc.rows or rows
            columns = doc.columns or columns
            font_family = doc.font_family or font_family
            if doc.row_styles:
                try:
                    rs = json.loads(doc.row_styles)
                except Exception:
                    rs = {}
            else:
                rs = {}
            if doc.col_styles:
                try:
                    cs = json.loads(doc.col_styles)
                    col_styles = cs
                except Exception:
                    pass
            print_paper = doc.print_paper

    if print_paper:
        paper = frappe.db.get_value("Super Print Paper", print_paper,
            ["width", "height", "margin_top", "margin_bottom", "margin_left", "margin_right"], as_dict=True)
        if paper:
            paper_w = float(paper.width or 210)
            paper_h = float(paper.height or 297)
            m_top = int(paper.margin_top or 10)
            m_bottom = int(paper.margin_bottom or 10)
            m_left = int(paper.margin_left or 15)
            m_right = int(paper.margin_right or 15)

    # Calculate dimensions
    total_width = sum(
        (col_styles.get(str(c), {}).get("width") or 60) for c in range(1, columns + 1)
    )
    paper_w_px = int(paper_w * PX_PER_MM)
    paper_h_px = int(paper_h * PX_PER_MM)
    m_top_px = m_top * PX_PER_MM
    m_bottom_px = m_bottom * PX_PER_MM
    m_left_px = m_left * PX_PER_MM
    m_right_px = m_right * PX_PER_MM

    content_area_w = paper_w_px - m_left_px - m_right_px
    centered_offset = max(0, (content_area_w - total_width) / 2)
    col_header_offset = 22 + m_left_px + centered_offset

    font_families = [
        {"value": "Microsoft YaHei", "label": "Microsoft YaHei"},
        {"value": "SimSun", "label": "SimSun"},
        {"value": "SimHei", "label": "SimHei"},
        {"value": "KaiTi", "label": "KaiTi"},
        {"value": "FangSong", "label": "FangSong"},
    ]

    context = {
        "container_id": "spd-" + str(int(time.time() * 1000)),
        "rows": rows,
        "cols": columns,
        "font_family": font_family,
        "font_families": font_families,
        "paper_w": paper_w_px,
        "paper_h": paper_h_px,
        "m_top": m_top_px,
        "m_bottom": m_bottom_px,
        "m_left": m_left_px,
        "m_right": m_right_px,
        "col_header_offset": int(col_header_offset),
        "total_width": total_width,
    }

    template_path = "zhiz_print/zhiz_print/doctype/super_print_design/designer_template.html"
    return frappe.render_template(template_path, context)


@frappe.whitelist()
def load_design_data(design_name):
    """Load design data as a parsed grid for frontend consumption.

    Returns row_styles, col_styles, headers/footers, and a flat cell list
    with merge info resolved. Frontend can directly build its grid from this.
    """
    _check_license()
    if not frappe.db.exists("Super Print Design", design_name):
        return {"rows": 20, "columns": 15, "cells": []}
    doc = frappe.get_doc("Super Print Design", design_name)

    row_styles = json.loads(doc.row_styles) if doc.row_styles else {}
    col_styles = json.loads(doc.col_styles) if doc.col_styles else {}

    # Parse paper info
    paper_info = {}
    if doc.print_paper:
        paper_info = frappe.db.get_value("Super Print Paper", doc.print_paper,
            ["width", "height", "margin_top", "margin_bottom",
             "margin_left", "margin_right"], as_dict=True) or {}

    # Parse cells into structured list
    cells = []
    if doc.design_items:
        for item in doc.design_items:
            is_merged = bool(item.cell_value and item.cell_value.startswith("||MERGED::"))
            cell_data = {
                "cell_id": item.cell_id or "",
                "row": item.row,
                "col": item.col,
                "rowspan": item.rowspan or 1,
                "colspan": item.colspan or 1,
                "cell_type": item.cell_type or "static",
                "cell_value": item.cell_value or "",
                "css_style": item.css_style or "",
                "data_key": item.data_key or "",
                "query_name": item.query_name or "",
                "barcode_format": item.barcode_format or "CODE128",
                "barcode_width": item.barcode_width or 100,
                "barcode_height": item.barcode_height or 40,
                "row_type": item.row_type or "",
                "row_display": item.row_display or "",
                "is_merged": is_merged,
            }
            if is_merged:
                # Extract master cell id from ||MERGED::<id>||
                val = item.cell_value or ""
                cell_data["master_cell_id"] = val[10:-2] if len(val) > 12 else ""
            cells.append(cell_data)

    return {
        "rows": doc.rows or 20,
        "columns": doc.columns or 15,
        "font_family": doc.font_family or "Microsoft YaHei",
        "font_size": doc.font_size or 12,
        "row_styles": row_styles,
        "col_styles": col_styles,
        "page_header_left": doc.page_header_left or "",
        "page_header_center": doc.page_header_center or "",
        "page_header_right": doc.page_header_right or "",
        "page_footer_left": doc.page_footer_left or "",
        "page_footer_center": doc.page_footer_center or "",
        "page_footer_right": doc.page_footer_right or "",
        "paper": paper_info,
        "cells": cells,
    }


# ==================== Excel Export Helper Functions ====================

def _parse_inline_style(style_str):
    """Parse CSS inline style to dict, handle semicolons in url() correctly"""
    styles = {}
    if not style_str:
        return styles
    import re
    # Protect content in url(...) from being split by ;
    _url_holder = []
    def _save(m):
        _url_holder.append(m.group(0))
        return '__URL_%d__' % (len(_url_holder) - 1)
    protected = re.sub(r'url\([^)]*\)', _save, style_str)
    for item in protected.split(';'):
        item = item.strip()
        if ':' in item:
            key, value = item.split(':', 1)
            value = value.strip()
            for i, url in enumerate(_url_holder):
                value = value.replace('__URL_%d__' % i, url)
            styles[key.strip().lower()] = value
    return styles


def _extract_css_rules(soup):
    """Extract CSS rules from <style> tags"""
    try:
        import cssutils
        import logging
        cssutils.log.setLevel(logging.CRITICAL)
    except ImportError:
        return []

    rules = []
    for style_tag in soup.find_all('style'):
        css_text = style_tag.string or ''
        if not css_text.strip():
            continue
        try:
            sheet = cssutils.parseString(css_text)
            for rule in sheet:
                if rule.type == rule.STYLE_RULE:
                    prop_dict = {}
                    for prop in rule.style:
                        prop_dict[prop.name.lower()] = prop.value
                    rules.append({
                        'selector': rule.selectorText,
                        'styles': prop_dict
                    })
        except Exception:
            pass
    return rules


def _element_matches_selector(element, selector):
    """Check if element matches CSS selector (simplified, supports descendant/class/tag/ID selectors)"""
    parts = selector.strip().split()
    target = parts[-1] if parts else selector

    tag = element.name
    elem_classes = element.get('class', []) or []
    elem_id = element.get('id', '') or ''

    if not target or target == '*':
        return True

    # .class1.class2
    if target.startswith('.') and '#' not in target:
        classes = [c for c in target.split('.') if c]
        return all(c in elem_classes for c in classes)

    # tag.class
    if '.' in target and not target.startswith('.') and '#' not in target:
        t, cls = target.split('.', 1)
        if t and tag != t:
            return False
        return cls in elem_classes

    # #id
    if target.startswith('#'):
        return elem_id == target[1:]

    # tag
    return target == tag


def _get_element_styles(element, css_rules):
    """Get merged element styles (CSS rules + inline), inline takes priority"""
    merged = {}
    for rule in css_rules:
        if _element_matches_selector(element, rule['selector']):
            merged.update(rule['styles'])
    inline = _parse_inline_style(element.get('style', ''))
    merged.update(inline)
    return merged


def _css_color_to_hex(color_str):
    """Convert CSS color value to hex (without #)"""
    import re
    if not color_str or color_str.strip().lower() in ('transparent', 'none', 'inherit', 'initial'):
        return None
    color_str = color_str.strip()

    if color_str.startswith('#'):
        h = color_str[1:]
        if len(h) == 3:
            h = ''.join(c * 2 for c in h)
        return h.upper()

    m = re.match(r'rgb[a]?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)', color_str)
    if m:
        return '{:02X}{:02X}{:02X}'.format(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    named = {
        'black': '000000', 'white': 'FFFFFF', 'red': 'FF0000',
        'green': '008000', 'blue': '0000FF', 'yellow': 'FFFF00',
        'gray': '808080', 'grey': '808080', 'silver': 'C0C0C0',
        'orange': 'FFA500', 'purple': '800080',
    }
    return named.get(color_str.lower())


def _css_px_value(value_str):
    """Extract number from CSS value"""
    import re
    if not value_str:
        return None
    m = re.match(r'([\d.]+)', value_str.strip())
    return float(m.group(1)) if m else None


def _css_width_to_excel(width_str):
    """CSS width (px) -> Excel column width (character units), designer px/4=mm"""
    val = _css_px_value(width_str)
    if val is None:
        return None
    mm = val / 4.0  # designer PX_PER_MM=4, px/4=mm
    # openpyxl column width is in character units
    # Use mm value directly, since openpyxl 1 char width ~ 1.85mm
    # Convert mm / 1.85 to character width
    width = mm / 1.85
    return max(round(width, 2), 2)


def _css_height_to_excel(height_str):
    """CSS height (px) -> Excel row height (pt), designer px/4=mm"""
    val = _css_px_value(height_str)
    if val is None:
        return None
    mm = val / 4.0  # designer PX_PER_MM=4, px/4=mm
    return mm * 2.8346  # mm → pt (72/25.4)


def _css_font_size_to_pt(size_str):
    """CSS font-size → pt"""
    import re
    if not size_str:
        return None
    m = re.match(r'([\d.]+)\s*(px|pt|em|rem)?', size_str.strip())
    if not m:
        return None
    val = float(m.group(1))
    unit = (m.group(2) or 'px').lower()
    if unit == 'pt':
        return val
    if unit in ('em', 'rem'):
        return val * 12
    return val * 0.75


def _parse_border_side(border_str):
    """CSS border → openpyxl Side"""
    from openpyxl.styles import Side
    import re

    if not border_str or border_str.strip().lower() in ('none', '0', '0px', 'hidden'):
        return Side(style=None)

    s = border_str.strip().lower()

    m = re.search(r'([\d.]+)\s*px', s)
    width = float(m.group(1)) if m else 1

    style = 'thin'
    if 'double' in s:
        style = 'double'
    elif 'dotted' in s:
        style = 'dotted'
    elif 'dashed' in s:
        style = 'dashed'
    elif width >= 3:
        style = 'thick'
    elif width >= 2:
        style = 'medium'

    color = '000000'
    for part in s.split():
        c = _css_color_to_hex(part)
        if c:
            color = c
            break

    return Side(style=style, color=color)


def _parse_cell_value(text):
    """Try to parse text as number"""
    import re
    if not text:
        return ''
    clean = text.strip().replace(',', '')
    if re.match(r'^-?[\d]+(\.\d+)?$', clean):
        try:
            return float(clean) if '.' in clean else int(clean)
        except ValueError:
            return text
    return text


def _apply_cell_format(cell, styles):
    """Apply CSS styles to openpyxl cell"""
    from openpyxl.styles import Font, Alignment, Border, Side, PatternFill

    if not styles:
        return

    # Font
    font_kw = {}
    if 'font-size' in styles:
        s = _css_font_size_to_pt(styles['font-size'])
        if s:
            font_kw['size'] = s
    if 'font-weight' in styles:
        w = str(styles['font-weight']).strip()
        if w in ('bold', 'bolder', '700', '800', '900'):
            font_kw['bold'] = True
    if 'font-family' in styles:
        font_kw['name'] = styles['font-family'].strip('"\'').split(',')[0].strip()
    if 'color' in styles:
        c = _css_color_to_hex(styles['color'])
        if c:
            font_kw['color'] = c
    if font_kw:
        cell.font = Font(**font_kw)

    # Alignment
    align_kw = {}
    # Valid horizontal values per openpyxl; defensive against malformed CSS
    # (e.g. css_style missing trailing ';' before appended border-left:none)
    _valid_h = ('left', 'right', 'center', 'justify', 'general',
                'distributed', 'fill', 'centerContinuous')
    if 'text-align' in styles:
        ta = str(styles['text-align']).strip().lower()
        if ta in _valid_h:
            align_kw['horizontal'] = ta
    if 'vertical-align' in styles:
        v = str(styles['vertical-align']).strip().lower()
        if v == 'middle':
            align_kw['vertical'] = 'center'
        elif v in ('top', 'bottom', 'justify', 'distributed'):
            align_kw['vertical'] = v
    if styles.get('white-space') in ('pre-wrap', 'pre', 'normal'):
        align_kw['wrap_text'] = True
    elif styles.get('white-space') == 'nowrap':
        align_kw['wrap_text'] = False
    if align_kw:
        cell.alignment = Alignment(**align_kw)

    # Background color
    if 'background-color' in styles:
        c = _css_color_to_hex(styles['background-color'])
        if c:
            cell.fill = PatternFill(start_color=c, end_color=c, fill_type='solid')

    # Border - apply shorthand border first, then override with border-X sides
    border_kw = {}
    if 'border' in styles:
        s = _parse_border_side(styles['border'])
        border_kw = {'top': s, 'right': s, 'bottom': s, 'left': s}
    for side_name in ('top', 'right', 'bottom', 'left'):
        prop = f'border-{side_name}'
        if prop in styles:
            border_kw[side_name] = _parse_border_side(styles[prop])
    if border_kw:
        cell.border = Border(**border_kw)


def _extract_background_image(styles):
    """Extract background image data from CSS styles (base64 or URL)"""
    import re
    import base64

    bg = styles.get('background', '') or styles.get('background-image', '')
    if not bg:
        return None

    m = re.search(r"url\(['\"]?(.*?)['\"]?\)", bg)
    if not m:
        return None

    url = m.group(1).strip()

    if url.startswith('data:'):
        m2 = re.match(r'data:(image/[\w+]+);base64,(.*)', url, re.DOTALL)
        if m2:
            img_type = m2.group(1)
            try:
                img_bytes = base64.b64decode(m2.group(2))
                return {'type': img_type, 'data': img_bytes}
            except Exception:
                return None
    else:
        # Regular URL image
        try:
            if url.startswith('/'):
                import os
                site_path = frappe.get_site_path()
                file_path = os.path.join(site_path, url.lstrip('/'))
                if os.path.exists(file_path):
                    with open(file_path, 'rb') as f:
                        img_bytes = f.read()
                    ext = os.path.splitext(url)[1].lower()
                    type_map = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.bmp': 'image/bmp'}
                    return {'type': type_map.get(ext, 'image/png'), 'data': img_bytes}
            else:
                import requests
                resp = requests.get(url, timeout=10)
                if resp.status_code == 200:
                    ct = resp.headers.get('Content-Type', 'image/png')
                    return {'type': ct, 'data': resp.content}
        except Exception:
            pass

    return None


def _svg_to_png_bytes(svg_bytes):
    """Convert SVG to PNG bytes"""
    try:
        import cairosvg
        return cairosvg.svg2png(bytestring=svg_bytes)
    except Exception:
        return None


def _add_image_to_excel_cell(ws, row, col, rowspan, colspan, img_info, align='left'):
    """Add image to Excel cell, auto-fit to merged area dimensions"""
    from openpyxl.drawing.image import Image as XlImage
    from openpyxl.utils import get_column_letter
    from io import BytesIO

    img_data = img_info['data']
    img_type = img_info.get('type', '')

    # SVG → PNG
    if 'svg' in img_type:
        png_data = _svg_to_png_bytes(img_data)
        if png_data:
            img_data = png_data
        else:
            return  # skip if conversion fails

    try:
        img_stream = BytesIO(img_data)
        xl_img = XlImage(img_stream)
    except Exception:
        return

    # Calculate total height of merged area (pt -> mm -> px@96DPI)
    total_h_pt = 0
    for r in range(row, row + rowspan):
        h = ws.row_dimensions[r].height
        if h:
            total_h_pt += h
    total_h_mm = total_h_pt / 2.8346 if total_h_pt else 10
    total_h_px = total_h_mm * 96.0 / 25.4

    # Calculate total width of merged area (chars -> mm -> px@96DPI)
    mm_per_char = 7.0 * 25.4 / 96.0
    total_w_chars = 0
    for c in range(col, col + colspan):
        letter = get_column_letter(c)
        w = ws.column_dimensions[letter].width
        if w:
            total_w_chars += w
    total_w_mm = total_w_chars * mm_per_char if total_w_chars else 30
    total_w_px = total_w_mm * 96.0 / 25.4

    # Scale with aspect ratio to fit within cell area
    orig_w = xl_img.width or 1
    orig_h = xl_img.height or 1
    cell_w = max(total_w_px, 10)
    cell_h = max(total_h_px, 10)
    scale = min(cell_w / orig_w, cell_h / orig_h)
    img_w = int(orig_w * scale)
    img_h = int(orig_h * scale)
    xl_img.width = img_w
    xl_img.height = img_h

    # Alignment: adjust anchor column based on align
    if align == 'right' and colspan > 1:
        anchor_col = col + colspan - 1
    elif align == 'center' and colspan > 1:
        anchor_col = col + (colspan - 1) // 2
    else:
        anchor_col = col
    cell_ref = '%s%d' % (get_column_letter(anchor_col), row)
    ws.add_image(xl_img, cell_ref)


def _set_column_widths(ws, table, css_rules):
    """Set Excel column widths from colgroup or first row cell CSS widths"""
    from openpyxl.utils import get_column_letter

    col_widths = {}

    # Prefer colgroup first
    colgroup = table.find('colgroup')
    if colgroup:
        for idx, col in enumerate(colgroup.find_all('col'), 1):
            styles = _get_element_styles(col, css_rules)
            if 'width' in styles:
                w = _css_width_to_excel(styles['width'])
                if w:
                    col_widths[idx] = w

    # Then fall back to first row cells
    if not col_widths:
        first_tr = table.find('tr')
        if first_tr:
            col_idx = 1
            for cell_elem in first_tr.find_all(['td', 'th']):
                styles = _get_element_styles(cell_elem, css_rules)
                if 'width' in styles:
                    w = _css_width_to_excel(styles['width'])
                    if w:
                        col_widths[col_idx] = w
                col_idx += int(cell_elem.get('colspan', 1))

    for idx, w in col_widths.items():
        letter = get_column_letter(idx)
        ws.column_dimensions[letter].width = w


def _write_table_to_excel(ws, table, start_row, css_rules, skip_rows=0):
    """Write HTML table to Excel, parse CSS styles, return number of rows written"""
    occupied = {}
    _set_column_widths(ws, table, css_rules)

    images_to_add = []  # Collect image info, add at end (needs row heights and col widths set)

    trs = table.find_all('tr')
    excel_row = start_row

    for tr_idx, tr in enumerate(trs):
        if tr_idx < skip_rows:
            continue

        col = 1
        row_max_lines = 1  # Track max line count for row height auto-fit
        row_max_font_pt = 10.5  # Default font size (~14px → 10.5pt)
        for cell_elem in tr.find_all(['td', 'th']):
            while occupied.get((excel_row, col)):
                col += 1

            colspan = int(cell_elem.get('colspan', 1))
            rowspan = int(cell_elem.get('rowspan', 1))

            # Replace <br> with \n BEFORE text extraction to preserve newlines
            for br in cell_elem.find_all('br'):
                br.replace_with('\n')
            # Get text — DO NOT use strip=True (would strip &nbsp; leading indent)
            # Only strip leading/trailing newlines
            text = cell_elem.get_text().strip('\n\r')
            if not text and cell_elem.find('img'):
                text = cell_elem.find('img').get('alt', '')

            # Get styles
            cell_styles = _get_element_styles(cell_elem, css_rules)
            if cell_elem.name == 'th' and 'font-weight' not in cell_styles:
                cell_styles['font-weight'] = 'bold'

            # Auto-enable wrap_text for multi-line text (unless white-space explicitly set)
            if '\n' in text and 'white-space' not in cell_styles:
                cell_styles['white-space'] = 'pre-wrap'

            # Track line count and font size for row height auto-fit (rowspan=1 cells only)
            if rowspan == 1 and text:
                lines = text.count('\n') + 1
                if lines > row_max_lines:
                    row_max_lines = lines
            if 'font-size' in cell_styles:
                fs_pt = _css_font_size_to_pt(cell_styles['font-size'])
                if fs_pt and fs_pt > row_max_font_pt:
                    row_max_font_pt = fs_pt

            # Check background image (QR/barcode/image cells)
            bg_img = _extract_background_image(cell_styles)
            if bg_img:
                text = ''  # No text output for image cells
                images_to_add.append({
                    'row': excel_row, 'col': col,
                    'rowspan': rowspan, 'colspan': colspan,
                    'img_info': bg_img,
                    'align': cell_styles.get('text-align', 'left'),
                })

            # Write value and styles
            value = _parse_cell_value(text)
            cell = ws.cell(row=excel_row, column=col, value=value)
            _apply_cell_format(cell, cell_styles)

            # Merge cells
            if colspan > 1 or rowspan > 1:
                ws.merge_cells(
                    start_row=excel_row, start_column=col,
                    end_row=excel_row + rowspan - 1,
                    end_column=col + colspan - 1
                )
                for r in range(excel_row, excel_row + rowspan):
                    for c in range(col, col + colspan):
                        if (r, c) != (excel_row, col):
                            _apply_cell_format(ws.cell(row=r, column=c), cell_styles)

            # Mark occupied cells
            for r in range(excel_row, excel_row + rowspan):
                for c in range(col, col + colspan):
                    if r != excel_row or c != col:
                        occupied[(r, c)] = True

            col += colspan

        # Row height: take max of CSS height and content-required height
        row_styles = _get_element_styles(tr, css_rules)
        css_h_pt = _css_height_to_excel(row_styles['height']) if 'height' in row_styles else None
        required_h_pt = row_max_lines * row_max_font_pt * 1.3
        if css_h_pt is not None:
            # CSS height present: ensure at least enough for content
            final_h = max(css_h_pt, required_h_pt)
            ws.row_dimensions[excel_row].height = round(final_h, 2)
        elif row_max_lines > 1:
            # No CSS height but multi-line content: set required height
            ws.row_dimensions[excel_row].height = round(required_h_pt, 2)
        # else: no CSS height and single line — leave unset for Excel auto-fit

        excel_row += 1

    # Second pass: add images (row heights and col widths now fully set)
    for item in images_to_add:
        _add_image_to_excel_cell(
            ws, item['row'], item['col'],
            item['rowspan'], item['colspan'],
            item['img_info'],
            item.get('align', 'left')
        )

    return excel_row - start_row


# ==================== Excel Export Main Function ====================

@frappe.whitelist()
def export_print_excel(doctype, docname, design_name=None, params=None):
    """Export print design data to Excel file download."""
    _check_license()
    from io import BytesIO
    from frappe.utils.xlsxutils import make_xlsx
    from bs4 import BeautifulSoup
    import openpyxl

    if isinstance(params, str):
        try:
            params = json.loads(params)
        except (json.JSONDecodeError, TypeError):
            params = {}

    if not design_name:
        # Fall back to simple field export when no design name
        doc = frappe.get_doc(doctype, docname)
        rows = [["Field Label", "Field Name", "Field Value"]]
        meta = frappe.get_meta(doctype)
        for df in meta.fields:
            if df.fieldtype in ('Section Break', 'Column Break', 'HTML', 'Button', 'Fold'):
                continue
            value = doc.get(df.fieldname)
            rows.append([df.label or df.fieldname, df.fieldname, str(value) if value else ''])
        xlsx_data = make_xlsx(rows, doctype)
        frappe.local.response.filename = f"{docname}.xlsx"
        frappe.local.response.filecontent = xlsx_data.getvalue()
        frappe.local.response.type = "binary"
        return

    design = frappe.get_doc("Super Print Design", design_name)

    if not frappe.has_permission(doctype, "print", docname):
        frappe.throw(_("No print permission"), frappe.PermissionError)

    # Render HTML (same as print preview)
    html = design.get_preview_for_document(doc_name=docname, params=params)

    # Parse HTML
    soup = BeautifulSoup(html, 'html.parser')
    css_rules = _extract_css_rules(soup)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"

    # Collect all page tables
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
    else:
        # Cross-page dedup: compare first N rows of page 1 and page 2 to determine repeat title rows
        header_count = 0
        if len(pages) > 1:
            rows_1 = [tr.get_text(strip=True) for tr in pages[0].find_all('tr')]
            rows_2 = [tr.get_text(strip=True) for tr in pages[1].find_all('tr')]
            for i in range(min(len(rows_1), len(rows_2))):
                if rows_1[i] == rows_2[i]:
                    header_count = i + 1
                else:
                    break

        # Write to Excel page by page
        current_row = 1
        for page_idx, table in enumerate(pages):
            skip = header_count if page_idx > 0 else 0
            rows_written = _write_table_to_excel(ws, table, current_row, css_rules, skip_rows=skip)
            current_row += rows_written

    # Generate Excel file
    output = BytesIO()
    wb.save(output)
    output.seek(0)

    frappe.local.response.filename = f"{docname}-{design.design_name}.xlsx"
    frappe.local.response.filecontent = output.getvalue()
    frappe.local.response.type = "binary"
    return

