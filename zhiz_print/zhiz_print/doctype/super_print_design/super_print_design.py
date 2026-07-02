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
    generate_barcode_base64, generate_qrcode_base64, image_to_base64_src,
    execute_query_code, parse_parameters, replace_dynamic_params,
    _is_function_path, _call_query_function, format_query_result
)

MERGED_PREFIX = "||MERGED::"
MERGED_SUFFIX = "||"

PX_PER_MM = 4

# =rowsum(R:C) — sum column C across all expanded items of Data-Driven Row R.
# Tolerates half-width () and full-width （） parentheses and surrounding spaces.
_ROWSUM_RE = re.compile(r'\s*=\s*rowsum\s*[（(]\s*(\d+)\s*:\s*(\d+)\s*[）)]')


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
        """Ensure design items cover all cells (pages * rows * columns)"""
        if not self.rows or not self.columns:
            return

        page_count = cint(getattr(self, "page_count", 1)) or 1

        existing_cells = {}
        if self.design_items:
            for item in self.design_items:
                # Backfill page_no=1 for legacy items missing the field
                if not item.page_no:
                    item.page_no = 1
                existing_cells[f"{item.page_no}_{item.row}_{item.col}"] = item

        required_count = page_count * self.rows * self.columns
        current_count = len(self.design_items) if self.design_items else 0

        # Fill missing cells across all pages
        if current_count < required_count:
            for p in range(1, page_count + 1):
                for r in range(1, self.rows + 1):
                    for c in range(1, self.columns + 1):
                        key = f"{p}_{r}_{c}"
                        if key not in existing_cells:
                            self.append("design_items", {
                                "cell_id": f"P{p}R{r}C{c}",
                                "page_no": p,
                                "row": r,
                                "col": c,
                                "rowspan": 1,
                                "colspan": 1,
                                "cell_type": "static",
                                "cell_value": "",
                                "cell_options": "",
                                "css_style": ""
                            })

        # Remove excess cells (page_no out of range, or row/col exceeds grid)
        elif current_count > required_count:
            to_remove = []
            for item in self.design_items:
                p = cint(item.page_no) or 1
                if p > page_count or item.row > self.rows or item.col > self.columns:
                    to_remove.append(item)
            for item in to_remove:
                self.remove(item)

    def validate_cells(self):
        """Validate cell design, force correct merge markers"""
        if not self.design_items:
            return

        position_map = {}
        for item in self.design_items:
            if not item.page_no:
                item.page_no = 1
            if not item.cell_id:
                item.cell_id = f"P{item.page_no}R{item.row}C{item.col}"
            if item.row < 1 or item.col < 1:
                frappe.throw(f"Row/column numbers for cell {item.cell_id} must be greater than 0")
            position_map[f"{item.page_no}_{item.row}_{item.col}"] = item

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
            page = item.page_no

            # Process colspan
            if colspan > 1:
                for c in range(item.col + 1, item.col + colspan):
                    pos_key = f"{page}_{item.row}_{c}"
                    if pos_key in position_map:
                        position_map[pos_key].cell_value = f"{MERGED_PREFIX}{item.cell_id}{MERGED_SUFFIX}"
                        position_map[pos_key].cell_type = "static"
                        position_map[pos_key].rowspan = 1
                        position_map[pos_key].colspan = 1
            elif not is_self_merged:
                right_col = item.col + 1
                if right_col <= self.columns:
                    right_key = f"{page}_{item.row}_{right_col}"
                    if right_key in position_map and is_merged_cell(position_map[right_key].cell_value or ""):
                        position_map[right_key].cell_value = ""

            # Process rowspan
            if rowspan > 1:
                for r in range(item.row + 1, item.row + rowspan):
                    pos_key = f"{page}_{r}_{item.col}"
                    if pos_key in position_map:
                        position_map[pos_key].cell_value = f"{MERGED_PREFIX}{item.cell_id}{MERGED_SUFFIX}"
                        position_map[pos_key].cell_type = "static"
                        position_map[pos_key].rowspan = 1
                        position_map[pos_key].colspan = 1
            elif not is_self_merged:
                bottom_row = item.row + 1
                if bottom_row <= self.rows:
                    bottom_key = f"{page}_{bottom_row}_{item.col}"
                    if bottom_key in position_map and is_merged_cell(position_map[bottom_key].cell_value or ""):
                        position_map[bottom_key].cell_value = ""

    @frappe.whitelist()
    def get_preview_for_document(self, doc_name=None, params=None,
                                page_break_map=None, row_heights=None, shrink_map=None):
        """Preview design.

        v15.10.01: page_break_map / row_heights forwarded to build_preview_html so a
        client-measured pagination can drive the render (preview/print/PDF share this).
        """
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
                query_results, doc_name, doc, params=params,
                page_break_map=page_break_map, row_heights=row_heights)
            return html

        except Exception as e:
            frappe.log_error(frappe.get_traceback(), 'Print preview failed')
            return f'<div class="alert alert-danger">Preview failed: {frappe.utils.escape_html(str(e))}</div>'

    def get_measurement_for_document(self, doc_name=None, params=None):
        """v15.10.01: produce the off-screen measurement scaffold for this document.
        Runs the same doc/query setup as get_preview_for_document, then builds the
        unpaginated measurement HTML + geometry meta. Returns (html, meta)."""
        try:
            if isinstance(params, str):
                params = json.loads(params)

            doc = None
            if doc_name and self.target_doctype:
                try:
                    doc = frappe.get_doc(self.target_doctype, doc_name)
                except Exception:
                    pass

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

            return self.build_measurement_html(query_results, doc=doc, params=params)

        except Exception as e:
            frappe.log_error(frappe.get_traceback(), 'Measurement scaffold failed')
            return '', {}

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
    def _build_safe_eval_locals(doc=None, row=None):
        """Build local_vars dict for frappe.safe_eval.

        Centralizes the workarounds for safe_eval's three traps (see buglog bug-008):
        - str.format blocked ("format is an unsafe attribute") -> expose fmt() based on %
        - frappe.utils.* attribute access blocked -> expose flt directly
        - builtins hidden by default -> expose max/min/round

        Used by both _eval_logic_code (cell value expressions) and
        check_enable_conditions (advanced filter conditions) so users can write
        the same helpers in both places, e.g.
            get_value("Item", doc.production_item, "classification") == "滑板"
        """
        def get_value(doctype, name, field):
            if not name:
                return ''
            v = frappe.db.get_value(doctype, name, field)
            return '' if v is None else str(v)

        def fmt(value, precision=2):
            try:
                return f"%.{precision}f" % frappe.utils.flt(value)
            except Exception:
                return str(value) if value is not None else ''

        return {
            'doc': doc,
            'row': row,
            'frappe': frappe,
            'get_value': get_value,
            'fmt': fmt,
            'flt': frappe.utils.flt,
            'max': max,
            'min': min,
            'round': round,
        }

    @staticmethod
    def _eval_logic_code(expr, doc=None, row=None):
        """Evaluate logic code expression with doc and row context.

        Exposes (via _build_safe_eval_locals):
        - doc, row, frappe
        - get_value(doctype, name, field): fast SQL single-field lookup, returns '' on None/empty
        - fmt(value, precision=2): format numeric value as fixed-decimal string
          (str.format is blocked by safe_eval — "format is an unsafe attribute")
        - flt(value): alias for frappe.utils.flt (safe_eval blocks frappe.utils.* attribute access)
        - max/min/round: builtins (safe_eval hides them by default)
        """
        try:
            local_vars = SuperPrintDesign._build_safe_eval_locals(doc, row)
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

    def _build_row_metadata(self, page_no=None):
        """Build row-level mapping from design_items.

        row_display / row_type are stored per-cell but applied per-row.
        We use FIRST-non-empty-wins (instead of last-write-wins) so that
        auto-generated blank cells (which often have empty row_display)
        don't override values set on user-configured cells in the same row.
        Iteration order is Frappe's idx order which roughly follows creation
        order — user-configured cells typically have lower idx than auto-fill cells.

        v15.04.35: Optional page_no filter — only consider items on this logical page.
        """
        row_type_map = {}
        row_display_map = {}
        if self.design_items:
            for item in self.design_items:
                if page_no is not None and cint(item.page_no or 1) != page_no:
                    continue
                rt = (item.row_type or '').strip()
                rd = (item.row_display or '').strip()
                if rt and rt != 'Normal Row' and item.row not in row_type_map:
                    row_type_map[item.row] = rt
                if rd and item.row not in row_display_map:
                    row_display_map[item.row] = rd
        return row_type_map, row_display_map

    # ==================== Row Expansion (Data-Driven Rows) ====================

    def _build_expanded_rows_v2(self, cell_map, row_styles, doc, query_results, page_no=None, params=None):
        """Build expanded row list based on row_type and child table/query data"""
        all_rows = list(range(1, self.rows + 1))

        # Detect data-driven rows
        data_driven_rows = {}  # {row_num: {'child_tables': set(), 'query_names': set()}}

        if self.design_items:
            for item in self.design_items:
                if page_no is not None and cint(item.page_no or 1) != page_no:
                    continue
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
                        row_sorts = ''
                        if row_styles and isinstance(row_styles, dict):
                            row_sorts = (row_styles.get(str(row_num), {}) or {}).get('sorts', '') or ''
                        data_driven_rows[row_num] = {
                            'child_tables': set(), 'query_names': set(),
                            'sorts': row_sorts}
                    for (table_name, _) in child_patterns:
                        data_driven_rows[row_num]['child_tables'].add(
                            table_name)
                    if qn:
                        data_driven_rows[row_num]['query_names'].add(qn)

        if not data_driven_rows:
            return all_rows, {}

        # Get actual data for each data-driven row
        row_data_map = {}  # {row_num: [data_items]}
        for row_num, info in data_driven_rows.items():
            items = self._get_row_data_items(info, doc, query_results)
            sorts = info.get('sorts', '')
            if sorts and items:
                items = self._sort_data_items_by_row_cols(
                    items, sorts, row_num, cell_map, doc, query_results, params)
            row_data_map[row_num] = items

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

        return result, row_data_map

    def _get_row_data_items(self, info, doc, query_results):
        """Get data list for data-driven rows in natural child-table order.

        Sorting is NOT applied here — the caller (_build_expanded_rows_v2) sorts
        by rendered column values after we return the raw item list, because the
        sort key may be a logic-cell / computed column whose value is only known
        after placeholder substitution.
        """
        if info.get('child_tables') and doc:
            for table_name in info['child_tables']:
                if hasattr(doc, table_name):
                    child_table = getattr(doc, table_name)
                    if child_table:
                        return list(child_table)

        # Then fall back to query results
        if info.get('query_names'):
            for query_name in info['query_names']:
                qr = query_results.get(query_name, {})
                data = qr.get('data', [])
                if data and isinstance(data, list):
                    return data

        return []

    def _sort_data_items_by_row_cols(self, items, sorts_str, template_row,
                                     cell_map, doc, query_results, params):
        """Sort data items by the rendered display value of row columns.

        sorts_str format: "row.N DIR, row.M DIR, ..."
          - row.N : the rendered display value of column N (1-based) of this
                    Data-Driven Row template, evaluated per data item (logic-cell
                    eval + doc/child/param placeholder substitution). Works even
                    when the column is a computed/conditional value, since it
                    sorts by what actually gets displayed.
          - DIR   : ASC (default) / DESC
        Multi-level via stable sort from lowest to highest priority. Never
        raises — logs and returns natural order on any error.
        """
        parsed = []
        for part in (sorts_str or '').split(','):
            part = part.strip()
            if not part:
                continue
            tokens = part.split()
            key = tokens[0].strip().lower()
            order = tokens[1].upper() if len(tokens) > 1 else 'ASC'
            if order not in ('ASC', 'DESC'):
                order = 'ASC'
            if key.startswith('row.'):
                try:
                    col_idx = int(key[4:])
                except (ValueError, TypeError):
                    continue
                if col_idx >= 1:
                    parsed.append((col_idx, order))
        if not parsed:
            return items
        try:
            for col_idx, order in reversed(parsed):
                items = sorted(
                    items,
                    key=lambda it, ci=col_idx: self._sort_key_normalized(
                        self._compute_cell_sort_value(
                            template_row, ci, it, cell_map, doc, query_results, params)),
                    reverse=(order == 'DESC'))
            return items
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                'Super Print Design: row-col sort "%s" failed, fallback to natural order' % sorts_str)
            return items

    @staticmethod
    def _sort_key_normalized(v):
        """Sort key that compares numbers numerically and strings lexically
        without raising on mixed types: numeric -> (0, float), non-numeric ->
        (1, str), empty -> (2, ''). Numbers sort before strings; empty sorts
        last under ASC (DESC reverse flips it to first)."""
        s = (str(v) if v is not None else '').strip()
        if s == '':
            return (2, '')
        try:
            return (0, float(s))
        except (ValueError, TypeError):
            return (1, s)

    def _compute_cell_sort_value(self, template_row, col_idx, data_item,
                                 cell_map, doc, query_results, params):
        """Compute the rendered display value of a cell, for sorting. Mirrors
        the value-resolution part of _build_row_html (expression / logic eval
        + doc/child/param/query placeholder substitution). Covers static,
        {doc.x}, {doc.child.field}, logic-cell, {param.x}, and '=expr'
        expression columns. data_key/query-only columns fall back to raw."""
        cell = cell_map.get("{0}_{1}".format(template_row, col_idx))
        if not cell:
            return ''
        cv = cell.get('cell_value', '') or ''
        if cv and cv.lstrip().startswith('=') and cell.get('cell_type') != 'logic':
            return self._eval_expression_cell(cv, doc, data_item, query_results, params)
        if cell.get('cell_type') == 'logic' and cv:
            cv = self._eval_logic_code(cv, doc, data_item)
        cv = self._replace_doc_placeholders(cv, doc)
        cv = self._replace_param_placeholders(cv, params)
        cv = self._replace_query_data_placeholders(cv, query_results)
        if data_item:
            cv = self._replace_child_table_placeholders(cv, data_item)
        return cv or ''

    def _eval_expression_cell(self, raw, doc, data_item, query_results, params):
        """Evaluate a cell whose value starts with '=' (arithmetic expression).

        Placeholders are substituted first ({doc.x}, {doc.child.field},
        {param.x}, {query.column} → their formatted values), then the resulting
        expression is evaluated via safe_eval. Returns the formatted result;
        on any failure logs and returns the raw input so the user sees something
        instead of an empty cell. e.g. '={doc.items.qty}*{doc.items.rate}' with
        qty=100, rate=0.5 → '100*0.5' → 50.0 → '50'."""
        if not raw:
            return ''
        expr = raw.lstrip()[1:]  # strip leading '='
        expr = self._replace_doc_placeholders(expr, doc)
        expr = self._replace_param_placeholders(expr, params)
        expr = self._replace_query_data_placeholders(expr, query_results)
        if data_item:
            expr = self._replace_child_table_placeholders(expr, data_item)
        try:
            result = frappe.safe_eval(expr, {}, {'flt': frappe.utils.flt})
            return self._fmt_val(result)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                'Super Print Design: expression eval failed: %s' % (raw[:120]))
            return raw

    def _eval_rowsum(self, target_row, target_col, data_items, cell_map, doc, query_results, params):
        """Sum the rendered display values of column target_col across all expanded
        data items of the Data-Driven Row target_row (e.g. =rowsum(5:7) sums col 7
        of row 5). Reuses _compute_cell_sort_value so expression / logic / placeholder
        columns resolve correctly before summing; non-numeric cells are skipped.
        Returns formatted sum (trailing zeros stripped)."""
        total = 0.0
        for it in (data_items or []):
            v = self._compute_cell_sort_value(
                target_row, target_col, it, cell_map, doc, query_results, params)
            try:
                total += float(v or 0)
            except (ValueError, TypeError):
                continue
        return self._fmt_val(total)

        def key(item):
            return _safe(getattr(item, sort_field, None))
        return key

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

            # Logic cells: cell_value is a Python expression string, must eval first
            cell_type = cell_data.get('cell_type', 'static')
            if cell_type == 'logic':
                try:
                    cell_value = self._eval_logic_code(cell_value, doc, data_item)
                except Exception:
                    continue
                if not cell_value:
                    continue
            else:
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

            # Subtract border + padding safety margin (border-collapse 1px each side + cell padding slack)
            # was cell_w - 2, which underestimated wrap-triggering at the critical width boundary
            effective_w = max(1, cell_w - 6)

            # Per-category char width estimation (more accurate than flat 0.55 for ASCII-heavy text)
            # Browser metrics (Microsoft YaHei, Arial) approximated as ratio of font-size:
            # - CJK chars: ~1.00
            # - Digits 0-9: ~0.60 (numbers in specs/quantities often sit near the wrap boundary)
            # - Uppercase A-Z: ~0.65 (W/M/@ wider)
            # - Lowercase a-z: ~0.50
            # - Symbols (*, /, -, ., etc.): ~0.45
            text = str(cell_value)
            cn_chars = len(_re.findall(r'[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]', text))
            digit_chars = len(_re.findall(r'[0-9]', text))
            upper_chars = len(_re.findall(r'[A-Z]', text))
            lower_chars = len(_re.findall(r'[a-z]', text))
            symbol_chars = len(text) - cn_chars - digit_chars - upper_chars - lower_chars
            total_text_width = (
                cn_chars * actual_font_size
                + digit_chars * actual_font_size * 0.60
                + upper_chars * actual_font_size * 0.65
                + lower_chars * actual_font_size * 0.50
                + symbol_chars * actual_font_size * 0.45
            )

            # Critical-width protection: when text width falls in 85%-100% of cell width,
            # browsers trigger word-break:break-all wrapping even though math says it fits.
            # Treat as 2 lines to match actual render behavior and avoid silent row clipping.
            ratio = total_text_width / effective_w if effective_w > 0 else 1.0
            if 0.85 <= ratio <= 1.0:
                lines_needed = 2
            else:
                lines_needed = max(1, math.ceil(total_text_width / effective_w))
            content_height = lines_needed * actual_font_size

            max_content_height = max(max_content_height, content_height)

        return max(configured_height, int(math.ceil(max_content_height))) + border_px

    def _paginate_rows_v2(self, all_rows, row_type_map, row_styles, paper,
                          cell_map=None, col_styles=None, doc=None,
                          row_display_map=None, font_size=None):
        """Auto pagination based on row_type"""
        margin_top = cint(paper.margin_top) if paper else 10
        margin_bottom = cint(paper.margin_bottom) if paper else 10

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

    def build_preview_html(self, query_results, doc_name=None, doc=None, params=None,
                           page_break_map=None, row_heights=None):
        """Build preview HTML.

        v15.10.01 pagination rework:
        - page_break_map supplied (client-measured): bypass _paginate_rows_v2 and group
          rows by the client's serial indices; row_heights locks each row to its measured
          pixel height so PDF engines cannot reflow.
        - both None: legacy _paginate_rows_v2 estimation (fallback, zero behaviour change).
        """
        try:
            # Parse styles
            row_styles = json.loads(self.row_styles) if self.row_styles else {}
            col_styles = json.loads(self.col_styles) if self.col_styles else {}

            # Get paper dimensions
            paper = frappe.get_doc("Super Print Paper", self.print_paper)
            paper_width = paper.width
            paper_height = paper.height

            page_count = cint(getattr(self, "page_count", 1)) or 1

            # Aggregate pages from all logical page_no's
            all_pages = []
            cell_grids_by_page = {}
            cell_maps_by_page = {}
            row_items_map_by_page = {}

            for page_no in range(1, page_count + 1):
                cell_map = self._build_cell_map_for_page(page_no)

                # Build row metadata scoped to this page's items
                row_type_map, row_display_map = self._build_row_metadata(page_no=page_no)

                # Build expanded rows
                all_rows_data, row_items_map = self._build_expanded_rows_v2(
                    cell_map, row_styles, doc, query_results, page_no=page_no, params=params)
                row_items_map_by_page[page_no] = row_items_map

                # Build placeholder grid (two passes)
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

                # Serial index of each expanded row — stable, matches the measurement
                # scaffold order, so the client's break map (flow serials) maps back here.
                serial_to_row = {i: r for i, r in enumerate(all_rows_data)}
                title_serials = [i for i, r in enumerate(all_rows_data)
                                 if isinstance(r, int) and row_type_map.get(r) == 'Repeat Title Row']

                client_pages = None
                if page_break_map is not None:
                    key = page_no if page_no in page_break_map else str(page_no)
                    client_pages = page_break_map.get(key)

                if client_pages is not None:
                    # Client-driven pagination: title rows repeat on every page, data rows
                    # flow in groups supplied by the client's measured break map.
                    title_rows_with_serial = [(s, serial_to_row[s]) for s in title_serials]
                    for group in client_pages:
                        page_rows = list(title_rows_with_serial)
                        for s in group:
                            if s in serial_to_row:
                                page_rows.append((s, serial_to_row[s]))
                        all_pages.append((page_no, page_rows))
                else:
                    # Legacy estimation fallback
                    pages = self._paginate_rows_v2(
                        all_rows_data, row_type_map, row_styles, paper,
                        cell_map=cell_map, col_styles=col_styles, doc=doc,
                        row_display_map=row_display_map, font_size=self.font_size or 13)
                    for p in pages:
                        all_pages.append((page_no, [(None, r) for r in p]))

                cell_grids_by_page[page_no] = cell_grid
                cell_maps_by_page[page_no] = cell_map

            # Generate HTML for all pages
            body_html = self._build_pages_html(
                all_pages, cell_maps_by_page, cell_grids_by_page, row_styles, col_styles,
                query_results, doc, row_type_map, row_display_map, paper, params=params,
                row_heights_by_page=row_heights, row_items_map_by_page=row_items_map_by_page,
                shrink_map=shrink_map
            )

            return self._wrap_full_html(body_html, paper_width, paper_height, row_styles, col_styles, paper)

        except Exception as e:
            frappe.log_error(frappe.get_traceback(), 'Build preview HTML failed')
            return f'<div class="alert alert-danger">Preview failed: {frappe.utils.escape_html(str(e))}</div>'

    def build_measurement_html(self, query_results, doc=None, params=None):
        """v15.10.01: off-screen measurement scaffold. Renders every expanded row of
        every logical page_no in one table per page_no, with NO height locking, so the
        browser's offsetHeight reports each row's true wrapped height. Each <tr> is
        tagged data-pg / data-serial / data-kind for client-side greedy packing.

        Returns (html, meta); meta carries paper geometry + per-page content height.
        """
        try:
            row_styles = json.loads(self.row_styles) if self.row_styles else {}
            col_styles = json.loads(self.col_styles) if self.col_styles else {}
            paper = frappe.get_doc("Super Print Paper", self.print_paper)
            page_count = cint(getattr(self, "page_count", 1)) or 1

            margin_top = cint(paper.margin_top)
            margin_bottom = cint(paper.margin_bottom)
            content_h_px = (paper.height - margin_top - margin_bottom) * PX_PER_MM
            content_w_px = sum(col_styles.get(str(c), {}).get('width', 60)
                               for c in range(1, (self.columns or 1) + 1))

            blocks_meta = {}
            blocks_html = []

            for page_no in range(1, page_count + 1):
                cell_map = self._build_cell_map_for_page(page_no)
                row_type_map, row_display_map = self._build_row_metadata(page_no=page_no)
                all_rows_data, row_items_map = self._build_expanded_rows_v2(
                    cell_map, row_styles, doc, query_results, page_no=page_no, params=params)

                # cell_grid (two passes, identical to build_preview_html)
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

                rows_html = ''
                for serial, row_data in enumerate(all_rows_data):
                    is_title = isinstance(row_data, int) and row_type_map.get(row_data) == 'Repeat Title Row'
                    kind = 'title' if is_title else 'data'
                    tr_attr = f' data-pg="{page_no}" data-serial="{serial}" data-kind="{kind}"'
                    row_html = self._build_row_html(
                        row_data, cell_map, cell_grid, row_styles, col_styles,
                        query_results, doc, row_type_map, row_display_map, params=params,
                        measure=True, tr_extra_attr=tr_attr, row_items_map=row_items_map)
                    rows_html += row_html

                blocks_html.append(
                    f'<div class="sp-measure-block" data-pg="{page_no}" '
                    f'style="width:{content_w_px}px;">'
                    f'<table class="print-form-table" style="width:{content_w_px}px;'
                    f'border-collapse:collapse;table-layout:fixed;margin:0;">'
                    f'{self._build_colgroup(col_styles)}{rows_html}</table></div>'
                )
                blocks_meta[page_no] = {'content_h_px': content_h_px}

            font_family = self.font_family or 'Microsoft YaHei'
            font_size = self.font_size or 12
            html = (
                '<!DOCTYPE html><html><head><meta charset="utf-8"><style>'
                '* { box-sizing: border-box; }'
                f'body {{ font-family: \'{font_family}\', sans-serif; font-size: {font_size}px; margin:0; padding:0; }}'
                '.print-form-table td { padding:0; text-align:center; vertical-align:middle; line-height:1; }'
                '.sp-measure-block { overflow:visible; }'
                f'</style></head><body>{"".join(blocks_html)}</body></html>'
            )
            meta = {
                'content_h_px': content_h_px,
                'content_w_px': content_w_px,
                'paper_width': paper.width,
                'paper_height': paper.height,
                'margin_top': margin_top,
                'margin_bottom': margin_bottom,
                'page_count': page_count,
                'blocks': blocks_meta,
            }
            return html, meta

        except Exception as e:
            frappe.log_error(frappe.get_traceback(), 'Build measurement HTML failed')
            return '', {}

    def _build_cell_map_for_page(self, page_no):
        """Build cell mapping for a specific logical page (filtered by page_no)"""
        cell_map = {}
        if self.design_items:
            for item in self.design_items:
                if cint(item.page_no or 1) != page_no:
                    continue
                if is_merged_cell(item.cell_value or ""):
                    continue
                cell_map[f"{item.row}_{item.col}"] = {
                    'cell_id': item.cell_id,
                    'page_no': page_no,
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
        return cell_map

    # ==================== Per-Page HTML Build (with Header/Footer Positioning) ====================

    def _build_pages_html(self, pages, cell_maps_by_page, cell_grids_by_page, row_styles, col_styles,
                          query_results, doc, row_type_map, row_display_map, paper, params=None,
                          row_heights_by_page=None, row_items_map_by_page=None, shrink_map=None):
        """Build HTML for all pages, each page as a fixed-size container

        v15.04.35: pages is a list of (page_no, rows) tuples.
        cell_maps_by_page / cell_grids_by_page are dicts keyed by page_no.
        v15.10.01: each row is (serial, row_data); row_heights_by_page[page_no][serial]
        locks the row to its client-measured height (None serial => legacy path).
        """
        margin_top = cint(paper.margin_top)
        margin_bottom = cint(paper.margin_bottom)
        margin_left = cint(paper.margin_left)
        margin_right = cint(paper.margin_right)

        paper_h_px = paper.height * PX_PER_MM
        paper_w_px = paper.width * PX_PER_MM
        header_area_h = margin_top * PX_PER_MM
        footer_area_h = margin_bottom * PX_PER_MM
        content_top = header_area_h

        total_pages = len(pages)
        pages_html = []

        for page_idx, page_entry in enumerate(pages):
            if isinstance(page_entry, tuple):
                page_no, page_rows = page_entry
            else:
                page_no, page_rows = 1, page_entry
            page_num = page_idx + 1
            is_last = (page_idx == total_pages - 1)

            cell_map = cell_maps_by_page.get(page_no, {})
            cell_grid = cell_grids_by_page.get(page_no)

            page_html = f'<div class="print-page" data-page-no="{page_no}" style="width:{paper_w_px:.1f}px;height:{paper_h_px:.1f}px;position:relative;overflow:hidden;{"page-break-after:always;" if not is_last else ""}box-sizing:border-box;">'

            # Header area
            has_header = getattr(self, 'page_header_left', '') or getattr(
                self, 'page_header_center', '') or getattr(self, 'page_header_right', '')
            if has_header:
                header_left = self._replace_header_footer_placeholders(
                    getattr(self, 'page_header_left', '') or '', page_num, total_pages
                )
                header_center = self._replace_header_footer_placeholders(
                    getattr(self, 'page_header_center', '') or '', page_num, total_pages
                )
                header_right = self._replace_header_footer_placeholders(
                    getattr(self, 'page_header_right', '') or '', page_num, total_pages
                )
                page_html += f'<div class="print-page-header" style="position:absolute;top:0;left:0;right:0;height:{header_area_h:.1f}px;overflow:hidden;display:flex;align-items:center;">'
                page_html += f'<div style="flex:1;text-align:left;padding-left:{margin_left * PX_PER_MM:.1f}px;">{header_left}</div>'
                page_html += f'<div style="flex:1;text-align:center;">{header_center}</div>'
                page_html += f'<div style="flex:1;text-align:right;padding-right:{margin_right * PX_PER_MM:.1f}px;">{header_right}</div>'
                page_html += '</div>'

            # Content area
            page_html += f'<div class="print-page-content" style="position:absolute;top:{content_top:.1f}px;left:0;right:0;bottom:{footer_area_h:.1f}px;padding:0 {margin_right * PX_PER_MM:.1f}px 0 {margin_left * PX_PER_MM:.1f}px;overflow:hidden;">'
            page_html += '<table class="print-form-table">'
            page_html += self._build_colgroup(col_styles)
            for serial, row_data in page_rows:
                locked = None
                if serial is not None and row_heights_by_page:
                    rh = row_heights_by_page.get(page_no)
                    if rh is None:
                        rh = row_heights_by_page.get(str(page_no))
                    if rh:
                        locked = rh.get(serial)
                        if locked is None:
                            locked = rh.get(str(serial))
                _row_items = row_items_map_by_page.get(page_no, {}) if row_items_map_by_page else {}
                page_html += self._build_row_html(
                    row_data, cell_map, cell_grid, row_styles, col_styles,
                    query_results, doc, row_type_map, row_display_map, params=params,
                    locked_height=locked, row_items_map=_row_items, shrink_map=shrink_map
                )
            page_html += '</table></div>'

            # Footer area
            has_footer = getattr(self, 'page_footer_left', '') or getattr(
                self, 'page_footer_center', '') or getattr(self, 'page_footer_right', '')
            if has_footer:
                footer_left = self._replace_header_footer_placeholders(
                    getattr(self, 'page_footer_left', '') or '', page_num, total_pages
                )
                footer_center = self._replace_header_footer_placeholders(
                    getattr(self, 'page_footer_center', '') or '', page_num, total_pages
                )
                footer_right = self._replace_header_footer_placeholders(
                    getattr(self, 'page_footer_right', '') or '', page_num, total_pages
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
                        query_results, doc=None, row_type_map=None, row_display_map=None, params=None,
                        locked_height=None, measure=False, tr_extra_attr='', row_items_map=None, shrink_map=None):
        """Build a single row HTML.

        v15.10.01 pagination rework:
        - measure=True: emit row WITHOUT height lock so the browser reports the true
          wrapped height (used by the off-screen measurement scaffold).
        - locked_height=<px>: lock row to the client-measured height so wkhtmltopdf /
          WeasyPrint cannot reflow it (used when rebuilding from a client page-break map).
        - default: legacy behaviour (configured height, bumped by _get_row_height estimate).
        """
        # Process expanded rows
        if isinstance(row_data, dict):
            template_row = row_data['template_row']
            data_item = row_data['data_item']
            row_num = template_row
            _data_idx = row_data.get('data_index')
        else:
            row_num = row_data
            data_item = None
            _data_idx = None

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

        if measure:
            # Measurement mode: a <td> height acts as a MINIMUM (table cells grow to fit
            # content), so setting height=configured (no max-height / overflow) lets the
            # row grow to its true wrapped height while never under-reporting a Fixed
            # Height / designed-height row. offsetHeight => max(configured, content).
            row_h_value = row_style.get("height", 20)
            row_style_attr = ''
            cell_h_constraint = f'height:{row_h_value}px;'
        elif locked_height is not None:
            # Client-measured precise height, floored at the designer-set height so a
            # precise row can never render shorter than what was configured in the designer
            # (measurement is already max(configured, content); this max() is a guarantee).
            configured = row_style.get("height", 0) or 0
            row_h_value = max(locked_height, configured)
            row_style_attr = f'height:{row_h_value}px;max-height:{row_h_value}px;'
            cell_h_constraint = f'height:{row_h_value}px;max-height:{row_h_value}px;overflow:hidden;'
        else:
            row_h_value = row_style.get("height", 20)
            row_style_attr = f'height:{row_h_value}px;max-height:{row_h_value}px;'

            # For data-driven rows, use estimated content height to match pagination
            if data_item:
                estimated_h = self._get_row_height(row_data, row_styles, cell_map, col_styles,
                                                    doc, row_display_map, self.font_size or 13)
                if estimated_h > row_h_value:
                    row_h_value = estimated_h
                    row_style_attr = f'height:{row_h_value}px;max-height:{row_h_value}px;'

            # Per-cell height constraint passed to <td> generation
            cell_h_constraint = f'height:{row_h_value}px;max-height:{row_h_value}px;overflow:hidden;'

        if row_css:
            row_style_attr += row_css

        # Row display effect
        if row_display == 'Fixed Height':
            row_style_attr += 'overflow:hidden;white-space:nowrap;'
        elif row_display == 'Auto Shrink Font':
            # nowrap: 文字不换行,放不下时由 _estimate_font_size 缩字号 fit 单元格宽度
            row_style_attr += 'overflow:hidden;white-space:nowrap;'
        else:
            # Auto wrap (default)
            row_style_attr += 'word-wrap:break-word;word-break:break-all;'

        row_style_attr += 'line-height:1;'

        html = f'<tr{tr_extra_attr} style="{row_style_attr}">'

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
                style_attr = cell_h_constraint + 'line-height:1;'
                font_size = row_style.get(
                    'font_size') or (self.font_size or 12)
                style_attr += f'font-size:{font_size}px;'
                # Apply row-level vertical-align before cell css_style (cell overrides row)
                if row_va:
                    style_attr += f'vertical-align:{row_va};'
                if cell_data.get('css_style'):
                    _cell_css = cell_data['css_style'].strip()
                    if _cell_css and not _cell_css.endswith(';'):
                        _cell_css += ';'
                    style_attr += _cell_css

                # Get cell value
                cell_value = cell_data.get('cell_value', '')
                cell_type = cell_data.get('cell_type', 'static')

                # '=' cells: =rowsum(R:C) sum across a Data-Driven Row, or =expr arithmetic
                if cell_value and cell_type != 'logic' and cell_value.lstrip().startswith('='):
                    rs = _ROWSUM_RE.match(cell_value)
                    if rs:
                        tr_i, tc_i = int(rs.group(1)), int(rs.group(2))
                        items = (row_items_map or {}).get(tr_i, [])
                        cell_value = self._eval_rowsum(
                            tr_i, tc_i, items, cell_map, doc, query_results, params)
                    else:
                        cell_value = self._eval_expression_cell(
                            cell_value, doc, data_item, query_results, params)

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

                # Auto Shrink Font: shrink_map(前端 measureText 精确)优先,估算兜底;嵌标记供前端测量
                shrink_attr = ''
                if row_display == 'Auto Shrink Font' and cell_value:
                    shrink_key = '{0}_{1}{2}'.format(
                        row_num, col, '_{0}'.format(_data_idx) if _data_idx is not None else '')
                    if shrink_map and shrink_key in shrink_map:
                        shrunk = shrink_map[shrink_key]
                    else:
                        shrunk = self._estimate_font_size(str(cell_value), cell_w, cell_h, font_size)
                    if shrunk < font_size:
                        style_attr = style_attr.replace(
                            f'font-size:{font_size}px;', f'font-size:{shrunk}px;')
                    # 嵌标记:base 字号 + cell 宽(前端 measureText 量精确字号用)
                    shrink_attr = ' data-shrink-cell="{0}" data-base-fs="{1}" data-cell-w="{2}"'.format(
                        shrink_key, font_size, int(cell_w))

                # Generate content
                content = self._render_cell_content(
                    cell_data, cell_value, cell_w, cell_h)

                # Image-type cells: explicit <img> with fixed dimensions to prevent row oversize
                cell_type = cell_data.get('cell_type', 'static')
                if cell_type in ('barcode', 'qrcode', 'image') and content and '<img' in content:
                    import re as _re
                    src_match = _re.search(r'src="([^"]*)"', content)
                    if src_match:
                        img_src = src_match.group(1)
                        # Constrain image to cell dimensions; do not let PNG natural size push the row
                        img_max_w = cell_w
                        img_max_h = cell_h
                        # Inline <img> honors <td> text-align (set via css_style) for left/right alignment.
                        # Wrap in line-height:0/font-size:0 div to suppress inline baseline gap that
                        # otherwise inflates row height. Height locked to cell_h prevents row oversize.
                        content = (
                            f'<div style="line-height:0;font-size:0;">'
                            f'<img src="{img_src}" style="'
                            f'height:{img_max_h}px;'
                            f'max-width:{img_max_w}px;max-height:{img_max_h}px;'
                            f'object-fit:contain;vertical-align:top;">'
                            f'</div>'
                        )

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

                html += f'<td {rowspan_attr} {colspan_attr}{shrink_attr} style="{style_attr}">{content}</td>'
            else:
                # Empty cell - also needs merge border suppression
                empty_style = cell_h_constraint + 'line-height:1;'
                col_style = col_styles.get(str(col), {})
                if row_va:
                    empty_style += f'vertical-align:{row_va};'
                if col_style.get('css_style'):
                    _col_css = col_style['css_style'].strip()
                    if _col_css and not _col_css.endswith(';'):
                        _col_css += ';'
                    empty_style += _col_css
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
        """Auto Shrink Font: 找一个能在单元格内单行(nowrap)放下的最大字号。

        基于 cell_w 宽度 fit(不再看高度),用分类字符宽度系数估算文字总宽
        (CJK 1.0 / 数字 0.60 / 大写 0.65 / 小写 0.50 / 符号 0.45 × 字号),
        与 _get_row_height 同系数。base 字号能放下就返回 base;否则线性缩到
        text_width(fs) <= cell_w。下限 6px。nowrap 单行高≈字号,行高通常 >= 字号,
        故 cell_h 不再作为缩字号依据。"""
        if not text:
            return base_font_size
        import re as _re
        s = str(text)
        cn = len(_re.findall(r'[一-鿿　-〿＀-￯]', s))
        digit = len(_re.findall(r'[0-9]', s))
        upper = len(_re.findall(r'[A-Z]', s))
        lower = len(_re.findall(r'[a-z]', s))
        symbol = len(s) - cn - digit - upper - lower
        # 每字号单位的文字总宽(所有字符宽度系数之和)
        width_per_fs = cn * 1.0 + digit * 0.60 + upper * 0.65 + lower * 0.50 + symbol * 0.45
        if width_per_fs <= 0:
            return base_font_size
        w_base = width_per_fs * base_font_size
        # 留 4px 安全余量(border-collapse + 单元格 padding)
        avail = max(8, cell_w - 4)
        if w_base <= avail:
            return base_font_size
        fs = base_font_size * avail / w_base
        return max(6, int(fs))

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
                if cint(frappe.db.get_single_value("Zprint Setting", "explicit_image_preview")):
                    img_src = image_to_base64_src(cell_value) or cell_value
                else:
                    img_src = frappe.utils.escape_html(cell_value)
                return f'<img src="{img_src}" style="max-width:100%;max-height:100%;object-fit:contain;">'
            return ''
        else:
            escaped = frappe.utils.escape_html(cell_value)
            if not escaped:
                return escaped
            escaped = re.sub(r' {2,}', lambda m: '&nbsp;' * len(m.group()), escaped)
            return re.sub(r'\r\n|\r|\n', '<br>', escaped)

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
        # Normalize JS-style operators to Python. Note: ' & ' / ' | ' (with spaces)
        # only — avoids mangling brand names like "AT&T" written without spaces.
        condition = (condition
                     .replace('===', '==')
                     .replace('!==', '!=')
                     .replace('||', ' or ')
                     .replace('&&', ' and ')
                     .replace(' & ', ' and ')
                     .replace(' | ', ' or '))

        try:
            local_vars = SuperPrintDesign._build_safe_eval_locals(doc)
            local_vars['user'] = frappe.session.user
            return frappe.safe_eval(condition, {}, local_vars)
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
