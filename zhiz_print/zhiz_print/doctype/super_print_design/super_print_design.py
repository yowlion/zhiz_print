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
_PAGEROWSUM_RE = re.compile(r'\s*=\s*pagerowsum\s*[（(]\s*(\d+)\s*:\s*(\d+)\s*[）)]')


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
        self.validate_design_target()
        self.ensure_full_coverage()
        self.validate_print_count_driver()

    def validate_design_target(self):
        """设计目标校验:Report 模式必须有目标报表;DocType 模式清空 report_name"""
        if (self.design_target or 'DocType') == 'Report':
            if not self.report_name:
                frappe.throw(_("报表设计必须选择目标报表"))
        else:
            self.report_name = None
        # rep 为报表数据保留占位符前缀({rep.字段名}),禁止占用为查询名
        for q in (self.design_queries or []):
            if (q.query_name or '').strip().lower() == 'rep':
                frappe.throw(_("查询名称不能设为 rep — rep 是报表数据保留占位符前缀({{rep.字段名}})"))

    def validate_print_count_driver(self):
        """打印次数驱动单元格:全设计最多 1 个 + cell_value 必须解析为数字(纯数字 或 数字类型字段占位符)"""
        drivers = [d for d in (self.design_items or []) if cint(d.get("is_print_count_driver"))]
        if len(drivers) > 1:
            frappe.throw(_("At most one cell can be marked as 'Print Count Driver' per design (found {0})").format(len(drivers)))
        for d in drivers:
            cv = (d.cell_value or '').strip()
            if re.match(r'^\d+(\.\d+)?$', cv):
                continue
            m = re.match(r'^\{doc\.([\w.]+)\}$', cv)
            if not m:
                frappe.throw(_("打印次数驱动单元格的值必须是纯数字(如 5)或字段占位符(如 {doc.items.qty})"))
            if not self._is_numeric_print_count_field(m.group(1)):
                frappe.throw(_("打印次数驱动单元格的字段必须是数字类型(Int/Float/Currency 等),{0} 不是数字字段").format(m.group(1)))

    def _is_numeric_print_count_field(self, path):
        """path 如 'items.qty' 或 'name';查 frappe meta 该字段是否数字类型"""
        numeric_types = ('Int', 'Float', 'Decimal', 'Currency', 'Percent', 'Long Int', 'Rating')
        parts = path.split('.')
        try:
            if len(parts) == 1:
                df = frappe.get_meta(self.target_doctype).get_field(parts[0])
                return bool(df and df.fieldtype in numeric_types)
            table_field = frappe.get_meta(self.target_doctype).get_field(parts[0])
            child_doctype = table_field and table_field.options
            if not child_doctype:
                return False
            df = frappe.get_meta(child_doctype).get_field(parts[1])
            return bool(df and df.fieldtype in numeric_types)
        except Exception:
            return False

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
                                page_break_map=None, row_heights=None, shrink_map=None,
                                inject_query_results=None, report_filters=None):
        """Preview design.

        v15.10.01: page_break_map / row_heights forwarded to build_preview_html so a
        client-measured pagination can drive the render (preview/print/PDF share this).
        v15.23: 报表模式 — inject_query_results 注入伪查询数据(如 __report_main__ 报表行),
        report_filters 构造伪 doc 供 {doc.xxx}/{filter.xxx} 占位符与 logic 单元格求值。
        """
        self._active_filters = None
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

            # 报表模式:无单据,用筛选值构造伪 doc(占位符/logic 可引用筛选字段)
            if isinstance(report_filters, dict) and doc is None:
                self._active_filters = report_filters
                doc = frappe._dict(report_filters)

            # Execute queries (仅当有具体 doc 时执行;doc_name=None 是纯模板结构预览,
            # query parameters 依赖 doc.xxx,doc 为空时无法过滤会返回全表,导致 data-driven
            # 行展开成海量行 → preview 爆炸推送失败。模板结构预览走 query_results={} 空结果,
            # 与本地设计器网格"占位符原样"语义一致)
            query_results = {}
            if doc_name:
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

            # 报表注入(伪查询)后置合并:可覆盖同名设计查询,__report_main__ 为报表行保留键
            if inject_query_results:
                query_results.update(inject_query_results)

            html = self.build_preview_html(
                query_results, doc_name, doc, params=params,
                page_break_map=page_break_map, row_heights=row_heights, shrink_map=shrink_map)
            return html

        except Exception as e:
            frappe.log_error(frappe.get_traceback(), 'Print preview failed')
            return f'<div class="alert alert-danger">Preview failed: {frappe.utils.escape_html(str(e))}</div>'
        finally:
            self._active_filters = None

    def get_measurement_for_document(self, doc_name=None, params=None,
                                     inject_query_results=None, report_filters=None):
        """v15.10.01: produce the off-screen measurement scaffold for this document.
        Runs the same doc/query setup as get_preview_for_document, then builds the
        unpaginated measurement HTML + geometry meta. Returns (html, meta).
        v15.23: 报表模式注入参数同 get_preview_for_document。"""
        self._active_filters = None
        try:
            if isinstance(params, str):
                params = json.loads(params)

            doc = None
            if doc_name and self.target_doctype:
                try:
                    doc = frappe.get_doc(self.target_doctype, doc_name)
                except Exception:
                    pass

            # 报表模式:无单据,用筛选值构造伪 doc
            if isinstance(report_filters, dict) and doc is None:
                self._active_filters = report_filters
                doc = frappe._dict(report_filters)

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

            if inject_query_results:
                query_results.update(inject_query_results)

            return self.build_measurement_html(query_results, doc=doc, params=params)

        except Exception as e:
            frappe.log_error(frappe.get_traceback(), 'Measurement scaffold failed')
            return '', {}
        finally:
            self._active_filters = None

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

        def has_native_print_format(doctype=None):
            # True if the (doc's) doctype has at least one enabled built-in Print Format
            # besides the implicit 'Standard'. Lets enable_condition express
            # "step aside when a native format exists", e.g.  not has_native_print_format()
            dt = doctype or (doc.doctype if doc else None)
            if not dt:
                return False
            return bool(frappe.db.exists('Print Format', {'doc_type': dt, 'disabled': 0}))

        return {
            'doc': doc,
            'row': row,
            'frappe': frappe,
            'get_value': get_value,
            'fmt': fmt,
            'flt': frappe.utils.flt,
            'has_native_print_format': has_native_print_format,
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
        # 设计预览(doc=None):不求值,显示表达式原文(截断长表达式,让用户看到 logic cell 内容)
        if doc is None:
            _s = ' '.join(expr.split())
            return _s[:60] + ('...' if len(_s) > 60 else '')
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
                # Text Editor(Quill 富文本)字段存 HTML(如 <div class="ql-editor"><p>..</p></div>),
                # static 单元格会 escape_html → 原样显示标签。先 strip 成纯文本,
                # 符合"打印显示文本"语义(要保留富文本格式需另行渲染,不走此路径)。
                df = doc.meta.get_field(field_name) if hasattr(doc, 'meta') else None
                if df and df.fieldtype == 'Text Editor' and v:
                    v = frappe.utils.strip_html_tags(v)
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

    def _replace_rep_placeholders(self, value, data_item=None):
        """{rep.xxx} 报表命名空间(v15.22.18 三段语义):

        {rep.name}          报表本身字段(Report 文档字段,如 name/ref_doctype/module)
        {rep.filters.字段}   当前报表筛选值
        {rep.items.字段}     数据驱动行上下文的当前报表行字段
        仅报表设计(design_target=Report)生效;其余场景占位符原样保留。
        单级 {rep.字段} 的正则不含点号,天然不会误吞 items/filters 两段。"""
        if not value or '{rep.' not in value:
            return value or ''
        if (self.design_target or 'DocType') != 'Report' or not self.report_name:
            return value

        # 1) {rep.items.field} — 当前报表行(仅数据驱动行上下文)
        if data_item:
            def item_repl(m):
                f = m.group(1)
                if hasattr(data_item, f):
                    return self._fmt_val(getattr(data_item, f))
                if isinstance(data_item, dict) and f in data_item:
                    return self._fmt_val(data_item.get(f))
                return m.group(0)
            value = re.sub(r'\{rep\.items\.(\w+)\}', item_repl, value)

        # 2) {rep.filters.field} — 当前筛选值
        filters = getattr(self, '_active_filters', None) or {}

        def filter_repl(m):
            f = m.group(1)
            if f in filters:
                return self._fmt_val(filters[f])
            return m.group(0)

        value = re.sub(r'\{rep\.filters\.(\w+)\}', filter_repl, value)

        # 3) {rep.field} 单级 — 报表本身字段(Report 文档)
        def meta_repl(m):
            f = m.group(1)
            try:
                v = frappe.db.get_value("Report", self.report_name, f)
            except Exception:
                v = None
            return self._fmt_val(v) if v is not None else m.group(0)

        return re.sub(r'\{rep\.(\w+)\}', meta_repl, value)

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

    def _replace_filter_placeholders(self, value):
        """Replace {filter.filter_name} with report filters (报表模式专用).

        依赖实例属性 _active_filters(由 get_preview_for_document /
        get_measurement_for_document 的 report_filters 参数设置)。"""
        filters = getattr(self, '_active_filters', None)
        if not value or not filters:
            return value or ''

        def replacer(match):
            filter_name = match.group(1)
            if filter_name in filters:
                v = filters[filter_name]
                return SuperPrintDesign._fmt_val(v)
            return match.group(0)

        return re.sub(r'\{filter\.(\w+)\}', replacer, value)

    @staticmethod
    def _replace_query_data_placeholders(value, query_results):
        """Replace {query_name.column} with query result data (first row)"""
        if not value or not query_results:
            return value or ''

        def replacer(match):
            qn = match.group(1)
            col = match.group(2)
            # Skip doc/param/rep prefixes (handled by other methods; rep 为报表保留前缀)
            if qn in ('doc', 'param', 'rep'):
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

    def _replace_header_footer_placeholders(self, html, page_num, total_pages, raw=False):
        """Replace fixed placeholders in header/footer. raw=True 时原样返回(doc=None 模板结构预览不替换)
        v15.23: 非 raw 路径追加 {filter.xxx} 报表筛选替换(报表模式页眉页脚用)"""
        if raw or not html:
            return html or ''
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
        html = self._replace_filter_placeholders(html)
        # {rep.xxx} 报表命名空间(页眉页脚支持 rep.filters.x / rep.name)
        return self._replace_rep_placeholders(html)

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

    def _detect_data_driven_rows(self, row_styles, page_no=None):
        """Detect data-driven rows on a logical page from design_items.

        Returns {row_num: {'child_tables': set(), 'query_names': set(),
        'sorts': str, 'data_mode': str}}. data_mode comes from row_styles JSON
        (same place as 'sorts'): '' = auto render (default), 'select' =
        checkbox-picked rows at print time (勾选呈现)."""
        data_driven_rows = {}

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

                # {rep.items.field} 模式:自动识别为报表数据行(与子表 {doc.child.field} 同款机制),
                # 绑定伪查询 __report_main__(报表行由 render_report_preview 注入);
                # 注意 {rep.filters.x}/{rep.field} 不触发行展开(非行级数据)
                rep_patterns = re.findall(r'\{rep\.items\.(\w+)\}', cv)
                if rep_patterns:
                    is_data_driven = True

                # query 绑定(qn 供 data-driven 行收集 query_names 用)。
                # 注意:不再仅凭 data_query cell 就把整行判为 data-driven —— 非数据驱动行
                # 的 data_query cell 只取 query 首条值(_build_row_html 第1426行 fallback),
                # 只有 row_type=Data-Driven Row 或子表 {doc.child.field} 模式才按多值展开。
                # 否则:当 query 未被 parameters 过滤到单条时(如模板平台预览 ds01 返回全表),
                # 普通标题行会被按结果条数展开成 N 行,出现大量重复行。
                qn = (item.query_name or '').strip()

                if is_data_driven:
                    if row_num not in data_driven_rows:
                        row_sorts = ''
                        row_data_mode = ''
                        if row_styles and isinstance(row_styles, dict):
                            row_cfg = row_styles.get(str(row_num), {}) or {}
                            row_sorts = row_cfg.get('sorts', '') or ''
                            row_data_mode = row_cfg.get('data_mode', '') or ''
                        data_driven_rows[row_num] = {
                            'child_tables': set(), 'query_names': set(),
                            'sorts': row_sorts, 'data_mode': row_data_mode}
                    for (table_name, _) in child_patterns:
                        data_driven_rows[row_num]['child_tables'].add(
                            table_name)
                    if rep_patterns:
                        data_driven_rows[row_num]['query_names'].add('__report_main__')
                    if qn:
                        data_driven_rows[row_num]['query_names'].add(qn)
        return data_driven_rows

    # Reserved param key carrying the pre-print checkbox selection.
    # Shape: {'5': [item key, ...]} keyed by template row number.
    ROW_SELECTION_KEY = '__row_selection'

    @classmethod
    def _get_row_selection_map(cls, params):
        """Extract the checkbox-selection map from print params. Returns None
        when no selection was made (auto render, batch print, designer
        structure preview)."""
        if not isinstance(params, dict):
            return None
        sel = params.get(cls.ROW_SELECTION_KEY)
        if not isinstance(sel, dict) or not sel:
            return None
        return sel

    @staticmethod
    def _data_item_key(item, position):
        """Stable key identifying a data item for selection. Child-table rows
        use their idx; query rows (plain dicts, no idx) use 1-based natural
        position."""
        idx = getattr(item, 'idx', None)
        idx = cint(idx)
        return idx if idx > 0 else position + 1

    @classmethod
    def _filter_items_by_selection(cls, items, keys):
        """Keep only items whose key is in keys. keys=None means no selection
        made -> keep everything (auto render)."""
        if keys is None:
            return items
        try:
            key_set = set(cint(k) for k in keys)
        except (TypeError, ValueError):
            return items
        return [it for pos, it in enumerate(items)
                if cls._data_item_key(it, pos) in key_set]

    @staticmethod
    def _get_select_group_heads(data_driven_rows):
        """Rows whose expansion is governed by a checkbox selection.

        Adjacent data-driven rows sharing a data source expand in lockstep as
        a group driven by the group head's item list (see the grouping logic
        below), so a select-mode member redirects its selection to the head.
        Returns the set of head rows carrying a selection."""
        heads = set()
        row_to_head = {}
        prev_r = None
        for r in sorted(data_driven_rows.keys()):
            h = r
            if prev_r is not None and r == prev_r + 1:
                cand = row_to_head.get(prev_r, prev_r)
                head_info = data_driven_rows[cand]
                cur_info = data_driven_rows[r]
                if ((head_info.get('query_names') & cur_info.get('query_names')) or
                        (head_info.get('child_tables') & cur_info.get('child_tables'))):
                    h = cand
            row_to_head[r] = h
            prev_r = r
            if (data_driven_rows[r].get('data_mode') or '') == 'select':
                heads.add(h)
        return heads

    def _build_expanded_rows_v2(self, cell_map, row_styles, doc, query_results, page_no=None, params=None):
        """Build expanded row list based on row_type and child table/query data"""
        all_rows = list(range(1, self.rows + 1))

        # Detect data-driven rows
        data_driven_rows = self._detect_data_driven_rows(row_styles, page_no=page_no)

        if not data_driven_rows:
            return all_rows, {}

        # Get actual data for each data-driven row
        sel_map = self._get_row_selection_map(params)
        select_heads = self._get_select_group_heads(data_driven_rows) if sel_map is not None else set()
        row_data_map = {}  # {row_num: [data_items]}
        for row_num, info in data_driven_rows.items():
            items = self._get_row_data_items(info, doc, query_results)
            # 勾选呈现: 只展开打印前勾选的数据行(组首行的勾选治理整组)
            if row_num in select_heads:
                items = self._filter_items_by_selection(items, sel_map.get(str(row_num)))
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

    def get_data_row_options(self, doc_name, row, params=None):
        """Candidate data rows for the pre-print selection dialog of a
        checkbox-render (data_mode='select') Data-Driven Row.

        Returns {'row': row, 'items': [{'key', 'seq', 'cells': [...]}]} where
        cells are the rendered display values of the template row's columns
        for each data item (same value resolution as sorting / final print),
        so the dialog shows exactly what would print. Empty items when the
        row has no data or is not data-driven."""
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except (json.JSONDecodeError, TypeError, ValueError):
                params = {}

        row = cint(row)

        doc = None
        if doc_name and self.target_doctype:
            try:
                doc = frappe.get_doc(self.target_doctype, doc_name)
            except Exception:
                doc = None

        # Run the design's queries (same setup as get_preview_for_document —
        # the candidate rows of a query-driven data row need query_results)
        query_results = {}
        if doc_name:
            for q in self.design_queries:
                if not q.query_code:
                    continue
                try:
                    result = self.execute_query(
                        q.query_code, q.parameters,
                        doc_name, self.target_doctype, params)
                    query_results[q.query_name] = {'data': result}
                except Exception:
                    frappe.log_error(frappe.get_traceback(),
                                     'Super Print Design: selection dialog query failed: {0}'.format(q.query_name))
                    query_results[q.query_name] = {'data': []}

        # Locate the logical page owning this data-driven row + its cell map
        row_styles = json.loads(self.row_styles) if self.row_styles else {}
        page_count = cint(getattr(self, 'page_count', 1)) or 1
        cell_map = None
        info = None
        for page_no in range(1, page_count + 1):
            dd = self._detect_data_driven_rows(row_styles, page_no=page_no)
            if row in dd:
                info = dd[row]
                cell_map = self._build_cell_map_for_page(page_no)
                break
        if info is None or cell_map is None:
            return {'row': row, 'items': []}

        items = self._get_row_data_items(info, doc, query_results)

        # Visible columns of the template row: cell origins in col order
        cols = sorted(
            [c for c in cell_map.values() if c.get('row') == row],
            key=lambda c: c.get('col') or 1)

        out_items = []
        for pos, it in enumerate(items):
            cells = []
            for c in cols[:12]:
                v = self._compute_cell_sort_value(
                    row, c.get('col') or 1, it, cell_map, doc, query_results, params)
                v = (v or '').replace('\n', ' ').strip()
                if len(v) > 60:
                    v = v[:60] + '…'
                cells.append(v)
            out_items.append({
                'key': self._data_item_key(it, pos),
                'seq': pos + 1,
                'cells': cells,
            })

        # Drop columns empty across ALL items (auto-fill spacer cells) so the
        # dialog only shows meaningful columns
        if out_items:
            keep = [j for j in range(len(out_items[0]['cells']))
                    if any(it['cells'][j] for it in out_items)]
            if len(keep) < len(out_items[0]['cells']):
                for it in out_items:
                    it['cells'] = [it['cells'][j] for j in keep]
        return {'row': row, 'items': out_items}

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
        # barcode/qrcode '=' 前缀与 _build_row_html 取值同步:剥离 '=' 走 logic 求值,
        # 避免 rowsum/排序引用该列时与渲染路径取值分叉(取到表达式原文)
        if cv and cv.lstrip().startswith('=') and cell.get('cell_type') in ('barcode', 'qrcode'):
            cv = self._eval_logic_code(cv.lstrip()[1:], doc, data_item) if doc is not None else ''
            return cv
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
                           page_break_map=None, row_heights=None, shrink_map=None):
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
                        measure=True, tr_extra_attr=tr_attr, row_items_map=row_items_map,
                        page_row_items_map=row_items_map)
                    rows_html += row_html

                blocks_html.append(
                    f'<div class="sp-measure-block" data-pg="{page_no}" '
                    f'style="width:{content_w_px}px;">'
                    f'<table class="print-form-table" style="width:{content_w_px}px;'
                    f'border-collapse:collapse;table-layout:fixed;margin:0;'
                    f"font-family:'{self.font_family}',sans-serif;\">"
                    f'{self._build_colgroup(col_styles)}{rows_html}</table></div>'
                )
                blocks_meta[page_no] = {'content_h_px': content_h_px}

            font_family = self.font_family or 'Microsoft YaHei'
            font_size = self.font_size or 12
            html = (
                '<!DOCTYPE html><html><head><meta charset="utf-8"><style>'
                '* { box-sizing: border-box; }'
                f'body {{ font-family: \'{font_family}\', sans-serif; font-size: {font_size}px; margin:0; padding:0; }}'
                '.print-form-table td { padding:0; text-align:center; vertical-align:middle; line-height:inherit; }'
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
                    'barcode_show_text': cint(item.barcode_show_text) if item.barcode_show_text is not None else 1,
                    'barcode_text_size': cint(item.barcode_text_size) or 10,
                    'is_print_count_driver': cint(item.is_print_count_driver),
                }
        return cell_map

    # ==================== Per-Page HTML Build (with Header/Footer Positioning) ====================

    def _render_native_letterhead(self, doc, which):
        """Render native Letter Head HTML for the header band ('header' → content) or
        footer band ('footer' → footer). Returns '' when doc is None or no letter head
        content is available, so callers fall back to the default Left/Center/Right.
        Uses frappe.www.printview.get_letter_head so doc.letter_head > is_default priority
        is honored. Jinja-renders the Letter Head content with {doc} context, and prepends
        the Letter Head's header_script/footer_script as a <style> block.
        """
        if doc is None:
            return ''
        from frappe.www.printview import get_letter_head
        lh = frappe._dict(get_letter_head(doc, False) or {})
        field = 'content' if which == 'header' else 'footer'
        html = (lh.get(field) or '').strip()
        if not html:
            return ''
        try:
            html = frappe.utils.jinja.render_template(html, {"doc": doc.as_dict()})
        except Exception:
            frappe.log_error(frappe.get_traceback(), 'Letter Head jinja render failed')
        script = (lh.get('header_script') if which == 'header' else lh.get('footer_script')) or ''
        if script.strip():
            html = '<style>' + script + '</style>' + html
        return html

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
        # 页眉页脚垂直对齐(Top=flex-start / Center=center / Bottom=flex-end),默认居中
        _align_map = {'Top': 'flex-start', 'Center': 'center', 'Bottom': 'flex-end'}
        header_align = _align_map.get((getattr(self, 'page_header_align', '') or 'Center'), 'center')
        footer_align = _align_map.get((getattr(self, 'page_footer_align', '') or 'Center'), 'center')

        # 扫描打印次数驱动单元格(is_print_count_driver=1) — 全设计最多 1 个(validate 保证)
        _driver_cell = None
        for _pm in (cell_maps_by_page or {}).values():
            for _cv in (_pm or {}).values():
                if cint(_cv.get('is_print_count_driver')):
                    _driver_cell = _cv
                    break
            if _driver_cell:
                break

        total_pages = len(pages)
        pages_html = []

        for page_idx, page_entry in enumerate(pages):
            if isinstance(page_entry, tuple):
                page_no, page_rows = page_entry
            else:
                page_no, page_rows = 1, page_entry
            page_num = page_idx + 1
            is_last = (page_idx == total_pages - 1)

            # 当前物理页各 template_row 的 data_items(供 =pagerowsum 只合计当前打印页)
            _page_row_items = {}
            for _s, _rd in page_rows:
                if isinstance(_rd, dict):
                    _tr = _rd.get('template_row')
                    _di = _rd.get('data_item')
                    if _tr is not None and _di is not None:
                        _page_row_items.setdefault(_tr, []).append(_di)

            cell_map = cell_maps_by_page.get(page_no, {})
            cell_grid = cell_grids_by_page.get(page_no)

            # 该页打印次数:驱动单元格渲染值(子表字段替换后 cint,max(1,N) 兜底;非数字/0→1)
            _driver_count = 1
            if _driver_cell:
                _dr_row = _driver_cell.get('row')
                _dr_items = _page_row_items.get(_dr_row) or []
                if _dr_items:
                    _dr_val = self._replace_child_table_placeholders(_driver_cell.get('cell_value') or '', _dr_items[0])
                    _driver_count = max(1, cint(_dr_val))

            page_html = f'<div class="print-page" data-page-no="{page_no}" data-print-count="{_driver_count}" style="width:{paper_w_px:.1f}px;height:{paper_h_px:.1f}px;position:relative;overflow:hidden;{"page-break-after:always;" if not is_last else ""}box-sizing:border-box;">'

            # Header area
            has_header = getattr(self, 'page_header_left', '') or getattr(
                self, 'page_header_center', '') or getattr(self, 'page_header_right', '')
            if cint(getattr(self, 'use_native_letterhead_header', 0)):
                # 勾选原生:只用 Letter Head content;空(doc=None 或无 Letter Head)则页眉区不输出,
                # 不回退到左/中/右(用户明确:没配原生抬头就该什么都没有)
                native_header_html = self._render_native_letterhead(doc, 'header')
                if native_header_html:
                    page_html += f'<div class="print-page-header" style="position:absolute;top:0;left:0;right:0;height:{header_area_h:.1f}px;overflow:hidden;display:flex;align-items:{header_align};"><div style="flex:1">{native_header_html}</div></div>'
            elif has_header:
                header_left = self._replace_header_footer_placeholders(
                    getattr(self, 'page_header_left', '') or '', page_num, total_pages, raw=(doc is None)
                )
                header_center = self._replace_header_footer_placeholders(
                    getattr(self, 'page_header_center', '') or '', page_num, total_pages, raw=(doc is None)
                )
                header_right = self._replace_header_footer_placeholders(
                    getattr(self, 'page_header_right', '') or '', page_num, total_pages, raw=(doc is None)
                )
                page_html += f'<div class="print-page-header" style="position:absolute;top:0;left:0;right:0;height:{header_area_h:.1f}px;overflow:hidden;display:flex;align-items:{header_align};">'
                page_html += f'<div style="flex:1;white-space:pre-wrap;text-align:{(getattr(self,"page_header_left_align","") or "Left").lower()};padding-left:{margin_left * PX_PER_MM:.1f}px;">{header_left}</div>'
                page_html += f'<div style="flex:1;white-space:pre-wrap;text-align:{(getattr(self,"page_header_center_align","") or "Center").lower()};">{header_center}</div>'
                page_html += f'<div style="flex:1;white-space:pre-wrap;text-align:{(getattr(self,"page_header_right_align","") or "Right").lower()};padding-right:{margin_right * PX_PER_MM:.1f}px;">{header_right}</div>'
                page_html += '</div>'

            # Content area
            page_html += f'<div class="print-page-content" style="position:absolute;top:{content_top:.1f}px;left:0;right:0;bottom:{footer_area_h:.1f}px;padding:0 {margin_right * PX_PER_MM:.1f}px 0 {margin_left * PX_PER_MM:.1f}px;overflow:hidden;">'
            page_html += '<table class="print-form-table" style="font-family:\'' + (self.font_family or 'Microsoft YaHei') + '\',sans-serif;">'
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
                    locked_height=locked, row_items_map=_row_items, shrink_map=shrink_map,
                    page_row_items_map=_page_row_items
                )
            page_html += '</table></div>'

            # Footer area
            has_footer = getattr(self, 'page_footer_left', '') or getattr(
                self, 'page_footer_center', '') or getattr(self, 'page_footer_right', '')
            if cint(getattr(self, 'use_native_letterhead_footer', 0)):
                # 勾选原生:只用 Letter Head footer;空则页脚区不输出,不回退到左/中/右
                native_footer_html = self._render_native_letterhead(doc, 'footer')
                if native_footer_html:
                    page_html += f'<div class="print-page-footer" style="position:absolute;bottom:0;left:0;right:0;height:{footer_area_h:.1f}px;overflow:hidden;display:flex;align-items:{footer_align};"><div style="flex:1">{native_footer_html}</div></div>'
            elif has_footer:
                footer_left = self._replace_header_footer_placeholders(
                    getattr(self, 'page_footer_left', '') or '', page_num, total_pages, raw=(doc is None)
                )
                footer_center = self._replace_header_footer_placeholders(
                    getattr(self, 'page_footer_center', '') or '', page_num, total_pages, raw=(doc is None)
                )
                footer_right = self._replace_header_footer_placeholders(
                    getattr(self, 'page_footer_right', '') or '', page_num, total_pages, raw=(doc is None)
                )
                page_html += f'<div class="print-page-footer" style="position:absolute;bottom:0;left:0;right:0;height:{footer_area_h:.1f}px;overflow:hidden;display:flex;align-items:{footer_align};">'
                page_html += f'<div style="flex:1;white-space:pre-wrap;text-align:{(getattr(self,"page_footer_left_align","") or "Left").lower()};padding-left:{margin_left * PX_PER_MM:.1f}px;">{footer_left}</div>'
                page_html += f'<div style="flex:1;white-space:pre-wrap;text-align:{(getattr(self,"page_footer_center_align","") or "Center").lower()};">{footer_center}</div>'
                page_html += f'<div style="flex:1;white-space:pre-wrap;text-align:{(getattr(self,"page_footer_right_align","") or "Right").lower()};padding-right:{margin_right * PX_PER_MM:.1f}px;">{footer_right}</div>'
                page_html += '</div>'

            # Left header area (竖排)
            left_header = self._replace_header_footer_placeholders(
                getattr(self, 'page_left_header', '') or '', page_num, total_pages, raw=(doc is None))
            if margin_left > 0 and left_header:
                _lha = {'Left':'flex-start','Center':'center','Right':'flex-end'}.get(getattr(self,'page_left_header_h_align','') or 'Center','center')
                _lva = {'Top':'flex-start','Center':'center','Bottom':'flex-end'}.get(getattr(self,'page_left_header_v_align','') or 'Center','center')
                page_html += f'<div style="position:absolute;left:0;top:{header_area_h:.1f}px;bottom:{footer_area_h:.1f}px;width:{margin_left * PX_PER_MM:.1f}px;overflow:hidden;display:flex;flex-direction:column;justify-content:{_lva};align-items:{_lha};"><div style="writing-mode:vertical-rl;white-space:pre-wrap;font-size:12px;color:#666;">{left_header}</div></div>'

            # Right footer area (竖排)
            right_footer = self._replace_header_footer_placeholders(
                getattr(self, 'page_right_footer', '') or '', page_num, total_pages, raw=(doc is None))
            if margin_right > 0 and right_footer:
                _rha = {'Left':'flex-start','Center':'center','Right':'flex-end'}.get(getattr(self,'page_right_footer_h_align','') or 'Center','center')
                _rva = {'Top':'flex-start','Center':'center','Bottom':'flex-end'}.get(getattr(self,'page_right_footer_v_align','') or 'Center','center')
                page_html += f'<div style="position:absolute;right:0;top:{header_area_h:.1f}px;bottom:{footer_area_h:.1f}px;width:{margin_right * PX_PER_MM:.1f}px;overflow:hidden;display:flex;flex-direction:column;justify-content:{_rva};align-items:{_rha};"><div style="writing-mode:vertical-rl;white-space:pre-wrap;font-size:12px;color:#666;">{right_footer}</div></div>'

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
                        locked_height=None, measure=False, tr_extra_attr='', row_items_map=None, shrink_map=None, page_row_items_map=None):
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
            # 数据驱动行:内容多变,取 max(测量,配置) 适应内容;
            # 静态行:尊重设计器配置高度,与设计器画布"所见即所得"(设计器用配置高度,预览也用配置高度;
            # line_spacing 拉开后文本若超出配置高度会被 overflow:hidden 截断,需自行调大 height)。
            if data_item:
                row_h_value = max(locked_height, configured)
            else:
                row_h_value = configured or locked_height or 20
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

        # Auto Wrap 时按行间距设 line-height(行与行之间额外间距);其他模式 line-height:1
        # 基数 1em,与设计器画布前端(super_print_design.js generateGridHtml)的
        # calc(1em + Npx) 完全一致 → 所见即所得。字体已对齐(print-form-table 显式 font-family),
        # 一致字体下 1em 基数表现与设计器画布相同。
        _line_spacing = int(row_style.get('line_spacing') or 0)
        if row_display == '' and _line_spacing > 0:
            row_style_attr += f'line-height:calc(1em + {_line_spacing}px);'
        else:
            row_style_attr += 'line-height:1;'

        # 显式 tr font-size:line-height:calc(1em + Npx) 的 1em 相对 tr 自身 font-size 解析,
        # tr 不显式设字号时走继承 → 设计器画布(继承 desk body)与演示预览 iframe(继承 iframe body)
        # 字号不同 → 1em 解析值不同 → 行间距表现不一致。显式设 row font_size 统一两端。
        _row_font_size = row_style.get('font_size') or (self.font_size or 12)
        row_style_attr += f'font-size:{_row_font_size}px;'

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
                style_attr = cell_h_constraint + 'line-height:inherit;'
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
                # doc=None(纯模板结构预览)时原样显示 =rowsum()/=expr 文本,不计算(无 doc/items 无法求值)
                # barcode/qrcode 除外:走下方专门的 '=' logic 求值分支,不进 _eval_expression_cell
                # (弱上下文无 get_value/doc/row,求值失败返回原文会把表达式文本编进条码)
                if cell_value and cell_type not in ('logic', 'barcode', 'qrcode') and cell_value.lstrip().startswith('=') and doc is not None:
                    prs = _PAGEROWSUM_RE.match(cell_value)
                    rs = _ROWSUM_RE.match(cell_value) if not prs else None
                    if prs or rs:
                        m = prs or rs
                        tr_i, tc_i = int(m.group(1)), int(m.group(2))
                        # pagerowsum 合计当前物理页的 items;rowsum 合计当前逻辑页全部 items
                        items_map = page_row_items_map if prs else row_items_map
                        items = (items_map or {}).get(tr_i, [])
                        cell_value = self._eval_rowsum(
                            tr_i, tc_i, items, cell_map, doc, query_results, params)
                    else:
                        cell_value = self._eval_expression_cell(
                            cell_value, doc, data_item, query_results, params)

                # barcode/qrcode '=' 前缀:剥离 '=' 后走 logic 求值(get_value/doc/row 强上下文),
                # 求值结果再编码为条码/二维码,而不是把表达式文本编进条码。
                # 不加 '=' 保持现有行为:占位符替换({doc.name} 等)后直接编码。
                # doc=None(纯模板结构预览)不求值,置空让条码渲染显示 '--'
                if cell_type in ('barcode', 'qrcode') and cell_value and cell_value.lstrip().startswith('='):
                    if doc is not None:
                        cell_value = self._eval_logic_code(cell_value.lstrip()[1:], doc, data_item)
                    else:
                        cell_value = ''

                # Logic code: evaluate Python expression
                # doc=None(纯模板结构预览)时 logic 原样显示表达式文本,不计算(无 doc/row 无法求值)
                if cell_type == 'logic' and cell_value and doc is not None:
                    cell_value = self._eval_logic_code(cell_value, doc, data_item)

                # Replace {doc.field_name} placeholder (single level)
                cell_value = self._replace_doc_placeholders(cell_value, doc)

                # Replace {param.param_name} user parameter placeholder
                cell_value = self._replace_param_placeholders(
                    cell_value, params)

                # Replace {filter.filter_name} report filter placeholder (报表模式)
                cell_value = self._replace_filter_placeholders(cell_value)

                # Replace {query_name.column} query data placeholder (non-doc/param)
                cell_value = self._replace_query_data_placeholders(
                    cell_value, query_results)

                # Replace {doc.child_table.field_name} child table placeholder
                if data_item:
                    cell_value = self._replace_child_table_placeholders(
                        cell_value, data_item)
                    # data_key method: first try data_item, then fallback to query_results
                    if cell_data.get('data_key'):
                        # v15.22.18:报表数据键为三段式命名空间值(items.x);
                        # 行数据解析剥掉 items. 前缀(filters.x/单级不走行数据路径)
                        dk = cell_data['data_key']
                        if dk.startswith('items.'):
                            dk = dk[len('items.'):]
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

                # {rep.xxx} 报表命名空间(items=当前行,仅数据行上下文;filters/单级任意单元格)
                cell_value = self._replace_rep_placeholders(
                    cell_value, data_item)

                # Query data replacement (for non-expanded rows, take first query result row)
                if not data_item and cell_data.get('query_name') and cell_data.get('data_key'):
                    qr = query_results.get(cell_data['query_name'])
                    if qr is None:
                        # query 未执行(如纯模板结构预览 doc_name=None):显示 {query.data_key} 占位符,与本地设计器网格一致
                        # __report_main__ 报表伪查询显示 rep 命名空间({rep.items.字段} 等)
                        _ph_ns = 'rep' if cell_data['query_name'] == '__report_main__' else cell_data['query_name']
                        cell_value = '{' + _ph_ns + '.' + cell_data['data_key'] + '}'
                    else:
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
                    # base 字号取 cell css_style 的 font-size(实际渲染字号),不是行默认
                    _fs_m = _re.search(r'font-size\s*:\s*(\d+(?:\.\d+)?)', cell_data.get('css_style') or '')
                    _actual_fs = int(float(_fs_m.group(1))) if _fs_m else font_size
                    # 可用宽度 = cell_w - 左右线宽 - 预留 2px(border-collapse 下 border 占内宽)
                    _bw_m = _re.search(r'border(?:-(?:left|right|top|bottom))?\s*:\s*(\d+)', cell_data.get('css_style') or '')
                    _border_w = int(_bw_m.group(1)) if _bw_m else 0
                    _avail_w = cell_w - 2 * _border_w - 2
                    shrink_key = '{0}_{1}{2}'.format(
                        row_num, col, '_{0}'.format(_data_idx) if _data_idx is not None else '')
                    if shrink_map and shrink_key in shrink_map:
                        shrunk = shrink_map[shrink_key]
                    else:
                        shrunk = self._estimate_font_size(str(cell_value), _avail_w, cell_h, _actual_fs)
                    if shrunk < _actual_fs:
                        style_attr = style_attr.replace(
                            f'font-size:{_actual_fs}px;', f'font-size:{shrunk}px;')
                    # 嵌标记:实际字号 + 可用宽(已减线宽+预留,前端 measureText 直接用)
                    shrink_attr = ' data-shrink-cell="{0}" data-base-fs="{1}" data-cell-w="{2}"'.format(
                        shrink_key, _actual_fs, int(_avail_w))

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
                empty_style = cell_h_constraint + 'line-height:inherit;'
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
        avail = max(8, cell_w)  # cell_w 已是可用宽(调用方减了左右线宽 + 预留 2px)
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
        """Render barcode — v15.22.32 起条码尺寸跟随单元格,不再使用宽高设置项。

        v15.22.34: 条码目标高 = 单元格高 - 2*(单元格内边距+1)。
        内边距从单元格 css_style 的 padding 解析(默认 2px)。"""
        if not value:
            return '<span style="color:#999">--</span>'
        # 解析单元格内边距(css padding,统一取四向最大值;无则默认2px)
        pad = 2
        try:
            css = cell_data.get('css_style') or ''
            import re as _re
            m = _re.search(r'padding\s*:\s*(\d+(?:\.\d+)?)px', css)
            if m:
                pad = float(m.group(1))
        except Exception:
            pass
        avail_h = max(20, int(cell_h) - 2 * int(pad + 1))
        img_src = generate_barcode_base64(
            value,
            barcode_format=cell_data.get('barcode_format', 'CODE128'),
            height=avail_h,
            show_text=cint(cell_data.get('barcode_show_text', 1)),
            text_size=cint(cell_data.get('barcode_text_size', 10)) or 10,
        )
        if img_src:
            # 条码标准比例出图,等比收进单元格实际尺寸(单元格多大条码区就多大):
            # 外层 span 占满单元格,img max-width/max-height:100% 等比缩放
            return (f'<span style="display:block;width:100%;height:100%;">'
                    f'<img src="{img_src}" style="max-width:100%;max-height:100%;object-fit:contain;"></span>')
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
	line-height: inherit;
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


@frappe.whitelist()
def preview_with_sample(design_name, doc_name):
    """设计器演示预览:用 sample_doc 渲染当前设计的实际数据预览 HTML。"""
    if not design_name or not doc_name:
        frappe.throw("design_name and doc_name required")
    doc = frappe.get_doc("Super Print Design", design_name)
    return doc.get_preview_for_document(doc_name=doc_name) or ""
