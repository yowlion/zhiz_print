# -*- coding: utf-8 -*-
# Copyright (c) 2026, Zhiz Print and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
import json
import re
import datetime
import copy
from frappe import _
from frappe.utils import cint
from zhiz_print.utils.query_executor import (
    is_merged_cell, extract_master_id,
    generate_barcode_base64, generate_qrcode_base64,
    execute_query_code, parse_parameters, replace_dynamic_params,
    _is_function_path, _call_query_function, format_query_result
)

MERGED_PREFIX = "||MERGED::"
MERGED_SUFFIX = "||"

PX_PER_MM = 4


class SuperPrintDesign(frappe.model.document.Document):

    def before_insert(self):
        """Check license before allowing new design creation."""
        if frappe.flags.get("skip_zhiz_print_license"):
            return
        from zhiz_print.api.license import check_license_valid
        valid, info = check_license_valid()
        if not valid:
            frappe.throw(_("Cannot create new print design: {0}").format(
                info.get("message", "License expired")
            ))

    def validate(self):
        self.check_license_on_save()
        self.ensure_full_coverage()

    def check_license_on_save(self):
        """Check license before saving design (both new and existing)."""
        if frappe.flags.get("skip_zhiz_print_license"):
            return
        from zhiz_print.api.license import check_license_valid
        valid, info = check_license_valid()
        if not valid:
            frappe.throw(_("License expired: {0}").format(
                info.get("message", "License expired")
            ))
        self.validate_cells()

    def ensure_full_coverage(self):
        """Ensure design items cover all cells (rows * columns)"""
        if not self.rows or not self.columns:
            return

        existing_cells = {}
        if self.design_items:
            for item in self.design_items:
                existing_cells[f"{item.row}_{item.col}"] = item

        required_count = self.rows * self.columns
        current_count = len(self.design_items) if self.design_items else 0

        # Fill missing cells
        if current_count < required_count:
            for r in range(1, self.rows + 1):
                for c in range(1, self.columns + 1):
                    key = f"{r}_{c}"
                    if key not in existing_cells:
                        self.append("design_items", {
                            "cell_id": f"R{r}C{c}",
                            "row": r,
                            "col": c,
                            "rowspan": 1,
                            "colspan": 1,
                            "cell_type": "static",
                            "cell_value": "",
                            "cell_options": "",
                            "css_style": ""
                        })

        # Remove excess cells
        elif current_count > required_count:
            to_remove = []
            for item in self.design_items:
                if item.row > self.rows or item.col > self.columns:
                    to_remove.append(item)
            for item in to_remove:
                self.remove(item)

    def validate_cells(self):
        """Validate cell design, force correct merge markers"""
        if not self.design_items:
            return

        position_map = {}
        for item in self.design_items:
            if not item.cell_id:
                item.cell_id = f"R{item.row}C{item.col}"
            if item.row < 1 or item.col < 1:
                frappe.throw(f"Row/column numbers for cell {item.cell_id} must be greater than 0")
            position_map[f"{item.row}_{item.col}"] = item

        for item in self.design_items:
            rowspan = cint(item.rowspan or 1)
            colspan = cint(item.colspan or 1)

            if rowspan < 1 or colspan < 1:
                frappe.throw(f"Rowspan/colspan for cell {item.cell_id} must be greater than 0")
            if item.row + rowspan - 1 > self.rows:
                frappe.throw(f"Rowspan for cell {item.cell_id} exceeds grid range")
            if item.col + colspan - 1 > self.columns:
                frappe.throw(f"Colspan for cell {item.cell_id} exceeds grid range")

            is_self_merged = is_merged_cell(item.cell_value or "")

            # Process colspan
            if colspan > 1:
                for c in range(item.col + 1, item.col + colspan):
                    pos_key = f"{item.row}_{c}"
                    if pos_key in position_map:
                        position_map[pos_key].cell_value = f"{MERGED_PREFIX}{item.cell_id}{MERGED_SUFFIX}"
                        position_map[pos_key].cell_type = "static"
                        position_map[pos_key].rowspan = 1
                        position_map[pos_key].colspan = 1
            elif not is_self_merged:
                right_col = item.col + 1
                if right_col <= self.columns:
                    right_key = f"{item.row}_{right_col}"
                    if right_key in position_map and is_merged_cell(position_map[right_key].cell_value or ""):
                        position_map[right_key].cell_value = ""

            # Process rowspan
            if rowspan > 1:
                for r in range(item.row + 1, item.row + rowspan):
                    pos_key = f"{r}_{item.col}"
                    if pos_key in position_map:
                        position_map[pos_key].cell_value = f"{MERGED_PREFIX}{item.cell_id}{MERGED_SUFFIX}"
                        position_map[pos_key].cell_type = "static"
                        position_map[pos_key].rowspan = 1
                        position_map[pos_key].colspan = 1
            elif not is_self_merged:
                bottom_row = item.row + 1
                if bottom_row <= self.rows:
                    bottom_key = f"{bottom_row}_{item.col}"
                    if bottom_key in position_map and is_merged_cell(position_map[bottom_key].cell_value or ""):
                        position_map[bottom_key].cell_value = ""

    @frappe.whitelist()
    def get_preview_for_document(self, doc_name=None, params=None):
        """Preview design"""
        try:
            if isinstance(params, str):
                params = json.loads(params)

            # Load target document (for {doc.xxx} placeholder replacement)
            doc = None
            if doc_name and self.target_doctype:
                try:
                    doc = frappe.get_doc(self.target_doctype, doc_name)
                except Exception:
                    pass

            # Execute queries from child table
            query_results = {}
            for q in self.design_queries:
                if not q.query_code:
                    continue
                try:
                    result = self.execute_query(
                        q.query_code, q.parameters,
                        doc_name, self.target_doctype, params
                    )
                    query_results[q.query_name] = {'data': result}
                except Exception as e:
                    frappe.log_error(frappe.get_traceback(), 'Query execution failed: {0}'.format(q.query_name))
                    query_results[q.query_name] = {'data': []}

            html = self.build_preview_html(
                query_results, doc_name, doc, params=params)
            return html

        except Exception as e:
            frappe.log_error(frappe.get_traceback(), 'Print preview failed')
            return f'<div class="alert alert-danger">Preview failed: {frappe.utils.escape_html(str(e))}</div>'

    def execute_query(self, query_code, parameters=None, doc_name=None, doc_type=None, user_params=None, current_row_index=None):
        """Execute query with parameter injection as .where() clauses

        Parameters format examples:
          bom.name = doc.bom_no          → query.where(bom.name == "BOM-001")
          bop.parent = doc.items.name    → query.where(bop.parent.isin(["ITEM-1","ITEM-2"]))
        - Left side: query variable reference (e.g. bom.name, bop.parent)
        - Right side: doc.field for direct field → inject .where(key == value)
        - Right side: doc.childtable.field for child table → inject .where(key.isin([values]))
        """
        return _execute_query_with_doc_context(query_code, parameters, doc_type, doc_name, current_row_index, user_params)

    # ==================== Placeholder Replacement ====================

    @staticmethod
    def _eval_logic_code(expr, doc=None, row=None):
        """Evaluate logic code expression with doc and row context.

        Exposes:
        - doc: current document
        - row: current child table row (Data-Driven Row)
        - frappe: frappe module (for advanced use)
        - get_value(doctype, name, field): fast SQL single-field lookup, returns '' on None/empty
        """
        try:
            def get_value(doctype, name, field):
                if not name:
                    return ''
                v = frappe.db.get_value(doctype, name, field)
                return '' if v is None else str(v)

            local_vars = {
                'doc': doc,
                'row': row,
                'frappe': frappe,
                'get_value': get_value,
            }
            result = frappe.safe_eval(expr, {}, local_vars)
            return str(result) if result is not None else ''
        except Exception:
            frappe.log_error(frappe.get_traceback(), 'Logic code eval failed: %s' % expr[:100])
            return ''

    @staticmethod
    def _fmt_val(v):
        """Format display value, strip trailing zeros: 5.0 -> 5, 5.10 -> 5.1"""
        if v is None:
            return ''
        if isinstance(v, float):
            if v == int(v):
                return str(int(v))
            return str(v).rstrip('0').rstrip('.')
        if isinstance(v, int):
            return str(v)
        s = str(v)
        if '.' in s:
            try:
                float(s)
                return s.rstrip('0').rstrip('.')
            except (ValueError, TypeError):
                pass
        return s

    @staticmethod
    def _replace_doc_placeholders(value, doc):
        """Replace {doc.field_name} placeholders with actual document field values (does not match {doc.xxx.yyy} child table pattern)"""
        if not value or not doc:
            return value or ''

        def replacer(match):
            field_name = match.group(1)
            if hasattr(doc, field_name):
                v = getattr(doc, field_name)
                return SuperPrintDesign._fmt_val(v)
            return match.group(0)

        return re.sub(r'\{doc\.(\w+)(?!\.)\}', replacer, value)

    @staticmethod
    def _replace_child_table_placeholders(value, child_item):
        """Replace {doc.child_table.field_name} with actual child table row values"""
        if not value or not child_item:
            return value or ''

        def replacer(match):
            field_name = match.group(2)
            if hasattr(child_item, field_name):
                v = getattr(child_item, field_name)
                return SuperPrintDesign._fmt_val(v)
            elif isinstance(child_item, dict) and field_name in child_item:
                v = child_item.get(field_name, '')
                return SuperPrintDesign._fmt_val(v)
            return match.group(0)

        return re.sub(r'\{doc\.(\w+)\.(\w+)\}', replacer, value)

    @staticmethod
    def _replace_param_placeholders(value, params):
        """Replace {param.param_name} with user input parameter values"""
        if not value or not params:
            return value or ''

        def replacer(match):
            param_name = match.group(1)
            if param_name in params:
                v = params[param_name]
                return SuperPrintDesign._fmt_val(v)
            return match.group(0)

        return re.sub(r'\{param\.(\w+)\}', replacer, value)

    @staticmethod
    def _replace_query_data_placeholders(value, query_results):
        """Replace {query_name.column} with query result data (first row)"""
        if not value or not query_results:
            return value or ''

        def replacer(match):
            qn = match.group(1)
            col = match.group(2)
            # Skip doc/param prefixes (handled by other methods)
            if qn in ('doc', 'param'):
                return match.group(0)
            qr = query_results.get(qn, {})
            data = qr.get('data', [])
            if data and isinstance(data, list) and len(data) > 0:
                first = data[0]
                if isinstance(first, dict) and col in first:
                    v = first[col]
                    return SuperPrintDesign._fmt_val(v)
            return match.group(0)

        return re.sub(r'\{(\w+)\.(\w+)\}', replacer, value)

    @staticmethod
    def _detect_child_table_patterns(cell_value):
        """Detect {doc.child_table.field_name} patterns, return [(table_name, field_name), ...]"""
        if not cell_value:
            return []
        return re.findall(r'\{doc\.(\w+)\.(\w+)\}', cell_value)

    @staticmethod
    def _replace_header_footer_placeholders(html, page_num, total_pages):
        """Replace fixed placeholders in header/footer"""
        if not html:
            return ''
        now = datetime.datetime.now()
        replacements = {
            '{page}': str(page_num),
            '{pages}': str(total_pages),
            '{now_date}': now.strftime('%Y-%m-%d'),
            '{now_time}': now.strftime('%H:%M:%S'),
            '{date_time}': now.strftime('%Y-%m-%d %H:%M:%S'),
        }
        for placeholder, val in replacements.items():
            html = html.replace(placeholder, val)
        return html

    # ==================== Row Metadata ====================

    def _build_row_metadata(self):
        """Build row-level mapping from design_items"""
        row_type_map = {}
        row_display_map = {}
        if self.design_items:
            for item in self.design_items:
                rt = (item.row_type or '').strip()
                rd = (item.row_display or '').strip()
                if rt and rt != 'Normal Row':
                    row_type_map[item.row] = rt
                if rd:
                    row_display_map[item.row] = rd
        return row_type_map, row_display_map

    # ==================== Row Expansion (Data-Driven Rows) ====================

    def _build_expanded_rows_v2(self, cell_map, row_styles, doc, query_results):
        """Build expanded row list based on row_type and child table/query data"""
        all_rows = list(range(1, self.rows + 1))

        # Detect data-driven rows
        data_driven_rows = {}  # {row_num: {'child_tables': set(), 'query_names': set()}}

        if self.design_items:
            for item in self.design_items:
                row_num = item.row
                rt = (item.row_type or '').strip()
                cv = item.cell_value or ''

                # Explicitly marked as data-driven row
                is_data_driven = (rt == 'Data-Driven Row')

                # Auto-detect child table patterns
                child_patterns = self._detect_child_table_patterns(cv)
                if child_patterns:
                    is_data_driven = True

                # Auto-detect query data binding
                qn = (item.query_name or '').strip()
                dk = (item.data_key or '').strip()
                if qn and dk:
                    is_data_driven = True

                if is_data_driven:
                    if row_num not in data_driven_rows:
                        data_driven_rows[row_num] = {
                            'child_tables': set(), 'query_names': set()}
                    for (table_name, _) in child_patterns:
                        data_driven_rows[row_num]['child_tables'].add(
                            table_name)
                    if qn:
                        data_driven_rows[row_num]['query_names'].add(qn)

        if not data_driven_rows:
            return all_rows

        # Get actual data for each data-driven row
        row_data_map = {}  # {row_num: [data_items]}
        for row_num, info in data_driven_rows.items():
            row_data_map[row_num] = self._get_row_data_items(
                info, doc, query_results)

        # Build expanded row list — group adjacent data-driven rows sharing query_name
        result = []
        i = 0
        while i < len(all_rows):
            row = all_rows[i]
            if row in row_data_map:
                # Check if next rows form a consecutive group with same query source
                group = [row]
                j = i + 1
                while j < len(all_rows):
                    next_row = all_rows[j]
                    if next_row in row_data_map:
                        # Check query_names overlap (share at least one query)
                        curr_qn = data_driven_rows[row].get('query_names', set())
                        next_qn = data_driven_rows[next_row].get('query_names', set())
                        # Also check if they have the same data source (same child table or same query)
                        curr_ct = data_driven_rows[row].get('child_tables', set())
                        next_ct = data_driven_rows[next_row].get('child_tables', set())
                        same_source = (curr_qn & next_qn) or (curr_ct & next_ct)
                        if same_source and next_row == group[-1] + 1:
                            group.append(next_row)
                            j += 1
                            continue
                    break

                data_items = row_data_map[row]
                if data_items and len(group) > 1:
                    # Group expansion: for each data item, emit all rows in group
                    for data_idx, data_item in enumerate(data_items):
                        for gr in group:
                            result.append({
                                'template_row': gr,
                                'data_index': data_idx,
                                'data_item': data_item
                            })
                elif data_items:
                    # Single row expansion (no grouping)
                    for data_idx, data_item in enumerate(data_items):
                        result.append({
                            'template_row': row,
                            'data_index': data_idx,
                            'data_item': data_item
                        })
                else:
                    for gr in group:
                        result.append(gr)
                i = j
            else:
                result.append(row)
                i += 1

        return result

    def _get_row_data_items(self, info, doc, query_results):
        """Get data list for data-driven rows"""
        # Prefer document child table first
        if info.get('child_tables') and doc:
            for table_name in info['child_tables']:
                if hasattr(doc, table_name):
                    child_table = getattr(doc, table_name)
                    if child_table:
                        return [item for item in child_table]

        # Then fall back to query results
        if info.get('query_names'):
            for query_name in info['query_names']:
                qr = query_results.get(query_name, {})
                data = qr.get('data', [])
                if data and isinstance(data, list):
                    return data

        return []

    # ==================== Pagination ====================

    def _get_row_height(self, row_data, row_styles, cell_map=None, col_styles=None,
                        doc=None, row_display_map=None, font_size=None):
        """Get actual height of a single row (px), accounting for text wrapping and border-collapse"""
        import math
        import re as _re
        if isinstance(row_data, dict):
            row_num = row_data['template_row']
            data_item = row_data.get('data_item')
        else:
            row_num = row_data
            data_item = None

        configured_height = row_styles.get(str(row_num), {}).get('height', 20)

        # border-collapse: each row adds 1px for its bottom border
        border_px = 1

        if not cell_map or data_item is None:
            return configured_height + border_px

        row_display = (row_display_map or {}).get(row_num, '')
        if row_display == 'Fixed Height':
            return configured_height + border_px

        actual_font_size = font_size or self.font_size or 12
        max_content_height = 0

        for col in range(1, self.columns + 1):
            cell_key = f"{row_num}_{col}"
            cell_data = cell_map.get(cell_key)
            if not cell_data:
                continue

            cell_value = cell_data.get('cell_value', '')
            if not cell_value or cell_value.startswith(MERGED_PREFIX):
                continue

            cell_value = self._replace_doc_placeholders(cell_value, doc)
            if data_item:
                cell_value = self._replace_child_table_placeholders(cell_value, data_item)

            if not cell_value:
                continue

            cell_colspan = cell_data.get('colspan', 1)
            cell_w = sum(
                col_styles.get(str(c), {}).get('width', 60)
                for c in range(col, col + cell_colspan)
            )

            if cell_w <= 0:
                continue

            # Subtract 2px border (1px each side with border-collapse)
            effective_w = max(1, cell_w - 2)

            # Calculate actual text width: Chinese ~font_size, others ~font_size*0.55
            text = str(cell_value)
            cn_chars = len(_re.findall(r'[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]', text))
            other_chars = len(text) - cn_chars
            total_text_width = cn_chars * actual_font_size + other_chars * actual_font_size * 0.55
            lines_needed = max(1, math.ceil(total_text_width / effective_w))
            content_height = lines_needed * actual_font_size

            max_content_height = max(max_content_height, content_height)

        return max(configured_height, int(math.ceil(max_content_height))) + border_px

    def _paginate_rows_v2(self, all_rows, row_type_map, row_styles, paper,
                          cell_map=None, col_styles=None, doc=None,
                          row_display_map=None, font_size=None):
        """Auto pagination based on row_type"""
        margin_top = cint(paper.margin_top) or 10 if paper else 10
        margin_bottom = cint(paper.margin_bottom) or 10 if paper else 10

        available_mm = (paper.height if paper else 297) - \
            margin_top - margin_bottom
        available_px = available_mm * PX_PER_MM

        # Identify Repeat Title Row
        title_rows = [r for r in all_rows
                      if isinstance(r, int) and row_type_map.get(r) == 'Repeat Title Row']

        # Title height includes 1px border per row (border-collapse)
        title_height = sum(
            row_styles.get(str(r), {}).get('height', 20) + 1
            for r in title_rows
        ) if title_rows else 0

        # Data rows = non-title rows
        data_rows = [r for r in all_rows if r not in title_rows]

        if not data_rows:
            return [all_rows] if all_rows else [[]]

        # Accumulate pagination by actual row height
        # Subtract 1px for table top border (border-collapse)
        content_available = available_px - title_height - 1
        pages = []
        current_page_rows = []
        current_height = 0

        for row_data in data_rows:
            row_h = self._get_row_height(row_data, row_styles, cell_map, col_styles,
                                          doc, row_display_map, font_size)
            if current_page_rows and (current_height + row_h) > content_available:
                # Current row does not fit, page break
                pages.append(title_rows + current_page_rows)
                current_page_rows = []
                current_height = 0
            current_page_rows.append(row_data)
            current_height += row_h

        if current_page_rows:
            pages.append(title_rows + current_page_rows)

        return pages if pages else [all_rows]

    # ==================== Main Render Pipeline ====================

    def build_preview_html(self, query_results, doc_name=None, doc=None, params=None):
        """Build preview HTML"""
        try:
            # Parse styles
            row_styles = json.loads(self.row_styles) if self.row_styles else {}
            col_styles = json.loads(self.col_styles) if self.col_styles else {}

            # Get paper dimensions
            paper = frappe.get_doc("Super Print Paper", self.print_paper)
            paper_width = paper.width
            paper_height = paper.height

            # Build row metadata
            row_type_map, row_display_map = self._build_row_metadata()

            # Build cell mapping
            cell_map = {}
            if self.design_items:
                for item in self.design_items:
                    if is_merged_cell(item.cell_value or ""):
                        continue
                    cell_map[f"{item.row}_{item.col}"] = {
                        'cell_id': item.cell_id,
                        'row': item.row,
                        'col': item.col,
                        'rowspan': item.rowspan or 1,
                        'colspan': item.colspan or 1,
                        'cell_type': item.cell_type or 'static',
                        'cell_value': item.cell_value or '',
                        'cell_options': item.cell_options or '',
                        'css_style': item.css_style or '',
                        'data_key': item.data_key or '',
                        'query_name': item.query_name or '',
                        'barcode_format': item.barcode_format or 'CODE128',
                        'barcode_width': item.barcode_width or 100,
                        'barcode_height': item.barcode_height or 40,
                    }

            # Build expanded rows
            all_rows_data = self._build_expanded_rows_v2(
                cell_map, row_styles, doc, query_results)

            # Build placeholder grid (two passes: place all cells first, then mark merge areas)
            cell_grid = [[None] * self.columns for _ in range(self.rows)]
            for cell in cell_map.values():
                r = cell['row'] - 1
                c = cell['col'] - 1
                if 0 <= r < self.rows and 0 <= c < self.columns:
                    cell_grid[r][c] = cell
            for cell in cell_map.values():
                r = cell['row'] - 1
                c = cell['col'] - 1
                for dr in range(cell['rowspan'] or 1):
                    for dc in range(cell['colspan'] or 1):
                        if dr == 0 and dc == 0:
                            continue
                        nr, nc = r + dr, c + dc
                        if 0 <= nr < self.rows and 0 <= nc < self.columns:
                            cell_grid[nr][nc] = {
                                'is_merged': True,
                                'merge_origin_row': cell['row'],
                                'merge_origin_col': cell['col'],
                                'merge_rowspan': cell['rowspan'] or 1,
                                'merge_colspan': cell['colspan'] or 1,
                            }

            # Pagination
            pages = self._paginate_rows_v2(
                all_rows_data, row_type_map, row_styles, paper,
                cell_map=cell_map, col_styles=col_styles, doc=doc,
                row_display_map=row_display_map, font_size=self.font_size or 13)

            # Generate HTML for each page
            body_html = self._build_pages_html(
                pages, cell_map, cell_grid, row_styles, col_styles,
                query_results, doc, row_type_map, row_display_map, paper, params=params
            )

            return self._wrap_full_html(body_html, paper_width, paper_height, row_styles, col_styles, paper)

        except Exception as e:
            frappe.log_error(frappe.get_traceback(), 'Build preview HTML failed')
            return f'<div class="alert alert-danger">Preview failed: {frappe.utils.escape_html(str(e))}</div>'

    # ==================== Per-Page HTML Build (with Header/Footer Positioning) ====================

    def _build_pages_html(self, pages, cell_map, cell_grid, row_styles, col_styles,
                          query_results, doc, row_type_map, row_display_map, paper, params=None):
        """Build HTML for all pages, each page as a fixed-size container"""
        margin_top = cint(paper.margin_top) or 10
        margin_bottom = cint(paper.margin_bottom) or 10
        margin_left = cint(paper.margin_left) or 15
        margin_right = cint(paper.margin_right) or 15

        paper_h_px = paper.height * PX_PER_MM
        paper_w_px = paper.width * PX_PER_MM
        header_area_h = margin_top * PX_PER_MM
        footer_area_h = margin_bottom * PX_PER_MM
        content_top = header_area_h
        content_pad_lr = f'{margin_left * PX_PER_MM}px {margin_right * PX_PER_MM}px'
        header_pad_lr = f'2px {margin_right * PX_PER_MM}px 2px {margin_left * PX_PER_MM}px'

        total_pages = len(pages)
        pages_html = []

        for page_idx, page_rows in enumerate(pages):
            page_num = page_idx + 1
            is_last = (page_idx == total_pages - 1)

            page_html = f'<div class="print-page" style="width:{paper_w_px:.1f}px;height:{paper_h_px:.1f}px;position:relative;overflow:hidden;{"page-break-after:always;" if not is_last else ""}box-sizing:border-box;">'

            # Header area: from paper top to top margin (left/center/right columns)
            has_header = getattr(self, 'page_header_left', '') or getattr(
                self, 'page_header_center', '') or getattr(self, 'page_header_right', '')
            if has_header:
                header_left = self._replace_header_footer_placeholders(
                    getattr(self, 'page_header_left',
                            '') or '', page_num, total_pages
                )
                header_center = self._replace_header_footer_placeholders(
                    getattr(self, 'page_header_center',
                            '') or '', page_num, total_pages
                )
                header_right = self._replace_header_footer_placeholders(
                    getattr(self, 'page_header_right',
                            '') or '', page_num, total_pages
                )
                page_html += f'<div class="print-page-header" style="position:absolute;top:0;left:0;right:0;height:{header_area_h:.1f}px;overflow:hidden;display:flex;align-items:center;">'
                page_html += f'<div style="flex:1;text-align:left;padding-left:{margin_left * PX_PER_MM:.1f}px;">{header_left}</div>'
                page_html += f'<div style="flex:1;text-align:center;">{header_center}</div>'
                page_html += f'<div style="flex:1;text-align:right;padding-right:{margin_right * PX_PER_MM:.1f}px;">{header_right}</div>'
                page_html += '</div>'

            # Content area: from top margin to bottom margin
            page_html += f'<div class="print-page-content" style="position:absolute;top:{content_top:.1f}px;left:0;right:0;bottom:{footer_area_h:.1f}px;padding:0 {margin_right * PX_PER_MM:.1f}px 0 {margin_left * PX_PER_MM:.1f}px;overflow:hidden;">'
            page_html += '<table class="print-form-table">'
            page_html += self._build_colgroup(col_styles)
            for row_num in page_rows:
                page_html += self._build_row_html(
                    row_num, cell_map, cell_grid, row_styles, col_styles,
                    query_results, doc, row_type_map, row_display_map, params=params
                )
            page_html += '</table></div>'

            # Footer area: from bottom margin to paper bottom (left/center/right columns)
            has_footer = getattr(self, 'page_footer_left', '') or getattr(
                self, 'page_footer_center', '') or getattr(self, 'page_footer_right', '')
            if has_footer:
                footer_left = self._replace_header_footer_placeholders(
                    getattr(self, 'page_footer_left',
                            '') or '', page_num, total_pages
                )
                footer_center = self._replace_header_footer_placeholders(
                    getattr(self, 'page_footer_center',
                            '') or '', page_num, total_pages
                )
                footer_right = self._replace_header_footer_placeholders(
                    getattr(self, 'page_footer_right',
                            '') or '', page_num, total_pages
                )
                page_html += f'<div class="print-page-footer" style="position:absolute;bottom:0;left:0;right:0;height:{footer_area_h:.1f}px;overflow:hidden;display:flex;align-items:center;">'
                page_html += f'<div style="flex:1;text-align:left;padding-left:{margin_left * PX_PER_MM:.1f}px;">{footer_left}</div>'
                page_html += f'<div style="flex:1;text-align:center;">{footer_center}</div>'
                page_html += f'<div style="flex:1;text-align:right;padding-right:{margin_right * PX_PER_MM:.1f}px;">{footer_right}</div>'
                page_html += '</div>'

            page_html += '</div>'
            pages_html.append(page_html)

        return ''.join(pages_html)

    # ==================== Row/Column HTML ====================

    def _build_colgroup(self, col_styles):
        html = '<colgroup>'
        for col in range(1, self.columns + 1):
            col_style = col_styles.get(str(col), {})
            col_width = col_style.get('width', 60)
            html += f'<col style="width: {col_width}px;">'
        html += '</colgroup>'
        return html

    def _build_row_html(self, row_data, cell_map, cell_grid, row_styles, col_styles,
                        query_results, doc=None, row_type_map=None, row_display_map=None, params=None):
        """Build a single row HTML"""
        # Process expanded rows
        if isinstance(row_data, dict):
            template_row = row_data['template_row']
            data_item = row_data['data_item']
            row_num = template_row
        else:
            row_num = row_data
            data_item = None

        def _is_merged_covering(merged_info, check_row, check_col):
            """Check if merge area covers specified row/column"""
            if not merged_info or not merged_info.get('is_merged'):
                return False
            or_ = merged_info['merge_origin_row']
            oc = merged_info['merge_origin_col']
            rs = merged_info['merge_rowspan']
            cs = merged_info['merge_colspan']
            return or_ <= check_row < or_ + rs and oc <= check_col < oc + cs

        row_style = row_styles.get(str(row_num), {})
        row_display = (row_display_map or {}).get(row_num, '')

        # Extract vertical-align from row css_style — it only works on <td>, not <tr>
        row_css = row_style.get('css_style', '') or ''
        row_va = ''
        import re as _re
        va_match = _re.search(r'vertical-align\s*:\s*(top|middle|bottom)', row_css)
        if va_match:
            row_va = va_match.group(1)
            row_css = _re.sub(r'vertical-align\s*:\s*\w+\s*;?', '', row_css).strip()

        row_style_attr = f'height:{row_style.get("height", 20)}px;'

        # For data-driven rows, use estimated content height to match pagination
        if data_item:
            estimated_h = self._get_row_height(row_data, row_styles, cell_map, col_styles,
                                                doc, row_display_map, self.font_size or 13)
            if estimated_h > row_style.get("height", 20):
                row_style_attr = f'height:{estimated_h}px;'

        if row_css:
            row_style_attr += row_css

        # Row display effect
        if row_display == 'Fixed Height':
            row_style_attr += 'overflow:hidden;white-space:nowrap;'
        elif row_display == 'Auto Shrink Font':
            row_style_attr += 'overflow:hidden;'
        else:
            # Auto wrap (default)
            row_style_attr += 'word-wrap:break-word;word-break:break-all;'

        row_style_attr += 'line-height:1;'

        html = f'<tr style="{row_style_attr}">'

        for col in range(1, self.columns + 1):
            grid_row = row_num - 1
            grid_col = col - 1
            if grid_row >= self.rows or grid_col >= self.columns:
                continue

            cell = cell_grid[grid_row][grid_col] if grid_row < len(
                cell_grid) and grid_col < len(cell_grid[0]) else None
            if cell and isinstance(cell, dict) and cell.get('is_merged'):
                continue

            cell_key = f"{row_num}_{col}"
            cell_data = cell_map.get(cell_key)

            if cell_data:
                rowspan_attr = f'rowspan="{cell_data["rowspan"]}"' if cell_data['rowspan'] > 1 else ''
                colspan_attr = f'colspan="{cell_data["colspan"]}"' if cell_data['colspan'] > 1 else ''

                # Build styles
                style_attr = 'line-height:1;'
                font_size = row_style.get(
                    'font_size') or (self.font_size or 12)
                style_attr += f'font-size:{font_size}px;'
                # Apply row-level vertical-align before cell css_style (cell overrides row)
                if row_va:
                    style_attr += f'vertical-align:{row_va};'
                if cell_data.get('css_style'):
                    style_attr += cell_data['css_style']

                # Get cell value
                cell_value = cell_data.get('cell_value', '')
                cell_type = cell_data.get('cell_type', 'static')

                # Logic code: evaluate Python expression
                if cell_type == 'logic' and cell_value:
                    cell_value = self._eval_logic_code(cell_value, doc, data_item)

                # Replace {doc.field_name} placeholder (single level)
                cell_value = self._replace_doc_placeholders(cell_value, doc)

                # Replace {param.param_name} user parameter placeholder
                cell_value = self._replace_param_placeholders(
                    cell_value, params)

                # Replace {query_name.column} query data placeholder (non-doc/param)
                cell_value = self._replace_query_data_placeholders(
                    cell_value, query_results)

                # Replace {doc.child_table.field_name} child table placeholder
                if data_item:
                    cell_value = self._replace_child_table_placeholders(
                        cell_value, data_item)
                    # data_key method: first try data_item, then fallback to query_results
                    if cell_data.get('data_key'):
                        dk = cell_data['data_key']
                        found_in_data_item = False
                        if isinstance(data_item, dict):
                            if dk in data_item:
                                cell_value = str(data_item.get(dk, cell_value) if data_item.get(dk, cell_value) is not None else '')
                                found_in_data_item = True
                        elif hasattr(data_item, dk):
                            cell_value = str(v if (v := getattr(data_item, dk, cell_value)) is not None else '')
                            found_in_data_item = True
                        # Fallback to query_results if not found in data_item
                        if not found_in_data_item and cell_data.get('query_name'):
                            qr = query_results.get(cell_data['query_name'], {})
                            qr_data = qr.get('data', [])
                            if qr_data and isinstance(qr_data, list) and len(qr_data) > 0:
                                # Try to match by data_index or take first row
                                data_idx = row_data.get('data_index', 0) if isinstance(row_data, dict) else 0
                                if data_idx < len(qr_data):
                                    row_result = qr_data[data_idx]
                                else:
                                    row_result = qr_data[0]
                                if isinstance(row_result, dict) and dk in row_result:
                                    cell_value = str(row_result[dk] if row_result[dk] is not None else '')

                # Query data replacement (for non-expanded rows, take first query result row)
                if not data_item and cell_data.get('query_name') and cell_data.get('data_key'):
                    qr = query_results.get(cell_data['query_name'], {})
                    qr_data = qr.get('data', [])
                    if qr_data and isinstance(qr_data, list) and len(qr_data) > 0:
                        first = qr_data[0]
                        if isinstance(first, dict) and cell_data['data_key'] in first:
                            cell_value = str(first[cell_data['data_key']] if first[cell_data['data_key']] is not None else '')

                # Calculate cell dimensions
                cell_rowspan = cell_data['rowspan']
                cell_colspan = cell_data['colspan']
                cell_h = sum(row_styles.get(str(r), {}).get('height', 20)
                             for r in range(row_num, row_num + cell_rowspan))
                cell_w = sum(col_styles.get(str(c), {}).get('width', 60)
                             for c in range(col, col + cell_colspan))

                # Auto Shrink Font: estimate suitable font size
                if row_display == 'Auto Shrink Font' and cell_value:
                    shrunk = self._estimate_font_size(
                        str(cell_value), cell_w, cell_h, font_size)
                    if shrunk < font_size:
                        style_attr = style_attr.replace(
                            f'font-size:{font_size}px;', f'font-size:{shrunk}px;')

                # Generate content
                content = self._render_cell_content(
                    cell_data, cell_value, cell_w, cell_h)

                # Image-type cells: rendered with background-image, completely excluded from document flow
                cell_type = cell_data.get('cell_type', 'static')
                if cell_type in ('barcode', 'qrcode', 'image') and content and '<img' in content:
                    import re as _re
                    src_match = _re.search(r'src="([^"]*)"', content)
                    if src_match:
                        img_src = src_match.group(1)
                        # Parse text-align alignment
                        css_style = cell_data.get('css_style', '')
                        align = 'center'
                        for prop in css_style.split(';'):
                            prop = prop.strip()
                            if prop.startswith('text-align:'):
                                align = prop.split(':', 1)[1].strip()
                                break
                        # background-image does not affect cell dimensions
                        # Ensure style_attr ends with ; to prevent attribute concatenation
                        if not style_attr.rstrip().endswith(';'):
                            style_attr += ';'
                        style_attr += (
                            f'background:url(\'{img_src}\') no-repeat {align} center/contain;'
                        )
                        content = '&nbsp;'

                    # Suppress ghost borders from merge areas (WeasyPrint border-collapse compatibility)
                    border_suppress = ''
                    # Check if left neighbor is covered by merge area
                    if col > 1:
                        left_merged = cell_grid[grid_row][grid_col - 1] if grid_row < len(
                            cell_grid) and grid_col - 1 < len(cell_grid[0]) else None
                        if _is_merged_covering(left_merged, row_num, col - 1) or _is_merged_covering(left_merged, row_num, col):
                            border_suppress += 'border-left:none;'
                    # Check if top neighbor is covered by merge area
                    if row_num > 1:
                        top_merged = cell_grid[grid_row - 1][grid_col] if grid_row - 1 < len(
                            cell_grid) and grid_col < len(cell_grid[0]) else None
                        if _is_merged_covering(top_merged, row_num - 1, col) or _is_merged_covering(top_merged, row_num, col):
                            border_suppress += 'border-top:none;'
                    style_attr += border_suppress

                html += f'<td {rowspan_attr} {colspan_attr} style="{style_attr}">{content}</td>'
            else:
                # Empty cell - also needs merge border suppression
                empty_style = 'line-height:1;'
                col_style = col_styles.get(str(col), {})
                if row_va:
                    empty_style += f'vertical-align:{row_va};'
                if col_style.get('css_style'):
                    empty_style += col_style['css_style']
                # Empty cell also checks merge borders
                if col > 1:
                    left_merged = cell_grid[grid_row][grid_col - 1] if grid_row < len(
                        cell_grid) and grid_col - 1 < len(cell_grid[0]) else None
                    if _is_merged_covering(left_merged, row_num, col - 1) or _is_merged_covering(left_merged, row_num, col):
                        empty_style += 'border-left:none;'
                if row_num > 1:
                    top_merged = cell_grid[grid_row - 1][grid_col] if grid_row - \
                        1 < len(cell_grid) and grid_col < len(cell_grid[0]) else None
                    if _is_merged_covering(top_merged, row_num - 1, col) or _is_merged_covering(top_merged, row_num, col):
                        empty_style += 'border-top:none;'
                html += f'<td style="{empty_style}"></td>'

        html += '</tr>'
        return html

    @staticmethod
    def _estimate_font_size(text, cell_w, cell_h, base_font_size=12):
        """Estimate font size for Auto Shrink Font"""
        if not text:
            return base_font_size
        char_count = len(text)
        if char_count == 0:
            return base_font_size
        # Chinese chars ~ font_size wide, English ~ font_size*0.6
        # Rough estimate: mixed char average width = font_size * 0.7
        avg_char_w = base_font_size * 0.7
        chars_per_line = max(1, cell_w / avg_char_w)
        lines_needed = max(1, char_count / chars_per_line)
        total_height = lines_needed * base_font_size * 1.2
        if total_height <= cell_h:
            return base_font_size
        ratio = cell_h / total_height
        return max(6, int(base_font_size * ratio))

    # ==================== Cell Content Rendering ====================

    def _render_cell_content(self, cell_data, cell_value, cell_w=100, cell_h=40):
        """Render cell content"""
        cell_type = cell_data.get('cell_type', 'static')

        if cell_type == 'barcode':
            return self._render_barcode_content(cell_value, cell_data, cell_w, cell_h)
        elif cell_type == 'qrcode':
            return self._render_qrcode_content(cell_value, cell_data, cell_w, cell_h)
        elif cell_type == 'image':
            if cell_value:
                return f'<img src="{frappe.utils.escape_html(cell_value)}" style="max-width:100%;max-height:100%;object-fit:contain;">'
            return ''
        else:
            return frappe.utils.escape_html(cell_value)

    def _render_barcode_content(self, value, cell_data, cell_w=100, cell_h=40):
        """Render barcode"""
        if not value:
            return '<span style="color:#999">--</span>'
        # Limit to cell dimensions, maintain aspect ratio
        bw = min(cell_data.get('barcode_width') or cell_w, cell_w)
        bh = min(cell_data.get('barcode_height') or cell_h, cell_h)
        img_src = generate_barcode_base64(
            value,
            barcode_format=cell_data.get('barcode_format', 'CODE128'),
            width=bw,
            height=bh
        )
        if img_src:
            return f'<img src="{img_src}" style="max-width:{bw}px;max-height:{bh}px;object-fit:contain;">'
        return frappe.utils.escape_html(value)

    def _render_qrcode_content(self, value, cell_data, cell_w=100, cell_h=40):
        """Render QR code"""
        if not value:
            return '<span style="color:#999">--</span>'
        qr_size = min(cell_w, cell_h)
        img_src = generate_qrcode_base64(
            value,
            width=qr_size,
            height=qr_size
        )
        if img_src:
            return f'<img src="{img_src}" style="max-width:{qr_size}px;max-height:{qr_size}px;object-fit:contain;">'
        return frappe.utils.escape_html(value)

    def _parse_css(self, css_string):
        """Parse CSS string"""
        pairs = {}
        if not css_string:
            return pairs
        for pair in css_string.split(';'):
            trimmed = pair.strip()
            if trimmed and ':' in trimmed:
                key, val = trimmed.split(':', 1)
                pairs[key.strip()] = val.strip()
        return pairs

    # ==================== Full HTML Wrapper ====================

    def _wrap_full_html(self, body_html, paper_width, paper_height, row_styles, col_styles, paper=None):
        """Wrap full HTML"""
        font_family = self.font_family or 'Microsoft YaHei'

        total_col_width = sum(
            col_styles.get(str(col), {}).get('width', 60)
            for col in range(1, (self.columns or 1) + 1)
        )

        orientation = 'landscape' if paper_width > paper_height else 'portrait'

        html_template = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
@page {{
	size: {paper_width}mm {paper_height}mm;
	margin: 0;
	-webkit-print-color-adjust: exact;
	print-color-adjust: exact;
}}
* {{ box-sizing: border-box; }}
body {{
	font-family: '{font_family}', sans-serif;
	font-size: {font_size}px;
	margin: 0;
	padding: 0;
}}
.print-pages-wrapper {{
	width: {paper_width_mm}mm;
}}
.print-pages-wrapper .print-page {{
	width: {paper_w_px}px;
	height: {paper_h_px}px;
	position: relative;
	overflow: hidden;
	page-break-after: always;
}}
.print-pages-wrapper .print-page:last-child {{
	page-break-after: auto;
}}
.print-form-table {{
	width: {total_col_width}px;
	border-collapse: collapse;
	table-layout: fixed;
	margin: 0 auto;
}}
.print-form-table td {{
	padding: 0;
	text-align: center;
	vertical-align: middle;
	overflow: hidden;
	line-height: 1;
}}
</style>
</head>
<body>
<div class="print-pages-wrapper">
{body_html}
</div>
</body>
</html>"""

        return html_template.format(
            paper_width=paper_width,
            paper_height=paper_height,
            paper_width_mm=paper_width,
            paper_w_px=paper_width * PX_PER_MM,
            paper_h_px=paper_height * PX_PER_MM,
            font_family=font_family,
            font_size=self.font_size or 12,
            total_col_width=total_col_width,
            body_html=body_html
        )

    # ==================== Enable Conditions ====================

    @staticmethod
    def check_enable_conditions(design_name, doc=None):
        """Check if design is enabled for current document/user"""
        design = frappe.get_doc("Super Print Design", design_name)
        if not design.enabled:
            return False

        condition = (design.enable_condition or '').strip()
        if not condition:
            return True

        # Support eval: prefix and JS syntax
        if condition.startswith('eval:'):
            condition = condition[5:].strip()
        condition = condition.replace('===', '==').replace('!==', '!=').replace('||', ' or ').replace('&&', ' and ')

        try:
            return frappe.safe_eval(condition, {}, {'doc': doc, 'user': frappe.session.user})
        except Exception:
            frappe.log_error(frappe.get_traceback(),
                             f'Enable condition evaluation failed: {design_name}')
            return False


@frappe.whitelist()
def preview_query_with_doc(query_code, parameters=None, target_doctype=None, doc_name=None):
    """Execute query preview with document context for parameter substitution.
    Used by the designer's query preview to resolve doc.field references.
    """
    if not query_code or not query_code.strip():
        frappe.throw('Query code is required')

    return _execute_query_with_doc_context(query_code, parameters, target_doctype, doc_name)


@frappe.whitelist()
def get_query_keys(design_name, query_name):
    """Return the column keys for a specific query in a design, using sample_doc for preview."""
    doc = frappe.get_doc("Super Print Design", design_name)
    query = None
    for q in doc.design_queries:
        if q.query_name == query_name:
            query = q
            break
    if not query or not query.query_code:
        return []

    result = _execute_query_with_doc_context(
        query.query_code, query.parameters,
        doc.target_doctype, doc.sample_doc
    )

    if not result or not isinstance(result, list):
        return []

    first = result[0]
    if isinstance(first, dict):
        return list(first.keys())
    return []


def _resolve_and_call_function(func_path, parameters, doc_type, doc_name, user_params=None):
    """Resolve doc. parameters and call a Python function as query."""
    params = parse_parameters(parameters)
    kwargs = {}

    if doc_name and doc_type:
        try:
            doc = frappe.get_doc(doc_type, doc_name)
            for key, val in params.items():
                if isinstance(val, str) and val.startswith('doc.'):
                    field_path = val[4:]
                    if hasattr(doc, field_path):
                        kwargs[key] = getattr(doc, field_path)
                    else:
                        kwargs[key] = val
                else:
                    kwargs[key] = val
        except Exception:
            kwargs = dict(params)
    else:
        kwargs = dict(params)

    if user_params and isinstance(user_params, dict):
        kwargs.update(user_params)

    return _call_query_function(func_path, kwargs)


def _execute_query_with_doc_context(query_code, parameters, doc_type, doc_name, current_row_index=None, user_params=None):
    """Execute query with parameter injection as .where() clauses (standalone, no Document instance needed)."""
    if _is_function_path(query_code):
        return _resolve_and_call_function(query_code, parameters, doc_type, doc_name, user_params)

    params = parse_parameters(parameters)
    doc = None

    where_clauses = []
    skip_query = False
    if doc_name and doc_type:
        try:
            doc = frappe.get_doc(doc_type, doc_name)
            for key, val in list(params.items()):
                if not val.startswith('doc.'):
                    if '.' in key:
                        escaped = val.replace('\\', '\\\\').replace('"', '\\"')
                        where_clauses.append('query = query.where({0} == "{1}")'.format(key, escaped))
                    continue

                field_path = val[4:]

                if '.' in field_path:
                    parts = field_path.split('.', 1)
                    child_table_name = parts[0]
                    child_field = parts[1]

                    child_rows = getattr(doc, child_table_name, None)
                    if child_rows is None:
                        skip_query = True
                        break

                    if current_row_index is not None and 0 <= current_row_index < len(child_rows):
                        v = getattr(child_rows[current_row_index], child_field, None)
                        if v is not None:
                            if isinstance(v, str):
                                escaped = v.replace('\\', '\\\\').replace('"', '\\"')
                                where_clauses.append('query = query.where({0} == "{1}")'.format(key, escaped))
                            else:
                                where_clauses.append('query = query.where({0} == {1})'.format(key, v))
                    else:
                        values = []
                        for row in child_rows:
                            v = getattr(row, child_field, None)
                            if v is not None:
                                values.append(v)
                        if not values:
                            skip_query = True
                            break
                        str_values = ['"' + v.replace('\\', '\\\\').replace('"', '\\"') + '"' if isinstance(v, str) else str(v) for v in values]
                        where_clauses.append('query = query.where({0}.isin([{1}]))'.format(key, ', '.join(str_values)))
                else:
                    if hasattr(doc, field_path):
                        actual_value = getattr(doc, field_path)
                        if actual_value is not None:
                            if isinstance(actual_value, str):
                                escaped = actual_value.replace('\\', '\\\\').replace('"', '\\"')
                                where_clauses.append('query = query.where({0} == "{1}")'.format(key, escaped))
                            else:
                                where_clauses.append('query = query.where({0} == {1})'.format(key, actual_value))
        except Exception:
            pass

    if skip_query:
        return []

    for key in [k for k, v in params.items() if '.' in k or v.startswith('doc.')]:
        del params[key]

    if doc_name and doc_type:
        params = replace_dynamic_params(params, doc_name, doc_type)

    # Inject user parameters
    if user_params and isinstance(user_params, dict):
        params.update(user_params)

    modified_code = query_code
    if where_clauses:
        where_inject = '\n'.join(where_clauses) + '\n'
        modified_code = re.sub(
            r'(result\s*=\s*query\.run\()',
            where_inject + r'\1',
            modified_code
        )

    if doc_type and doc_name and re.search(r'\bdoc\b', modified_code):
        modified_code = 'doc = frappe.get_doc({0}, {1})\n'.format(
            json.dumps(doc_type), json.dumps(doc_name)) + modified_code

    return execute_query_code(modified_code, filters=params, format_result=True)
