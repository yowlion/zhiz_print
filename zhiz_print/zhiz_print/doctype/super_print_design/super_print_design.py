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
    execute_query_code, parse_parameters, replace_dynamic_params
)

MERGED_PREFIX = "||MERGED::"
MERGED_SUFFIX = "||"

PX_PER_MM = 4


class SuperPrintDesign(frappe.model.document.Document):

    def validate(self):
        self.ensure_full_coverage()
        self.validate_cells()

    def ensure_full_coverage(self):
        """确保设计明细覆盖所有单元格 (rows * columns)"""
        if not self.rows or not self.columns:
            return

        existing_cells = {}
        if self.design_items:
            for item in self.design_items:
                existing_cells[f"{item.row}_{item.col}"] = item

        required_count = self.rows * self.columns
        current_count = len(self.design_items) if self.design_items else 0

        # 填充缺失的单元格
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

        # 移除多余的单元格
        elif current_count > required_count:
            to_remove = []
            for item in self.design_items:
                if item.row > self.rows or item.col > self.columns:
                    to_remove.append(item)
            for item in to_remove:
                self.remove(item)

    def validate_cells(self):
        """验证单元格设计，强制设置正确的合并标记"""
        if not self.design_items:
            return

        position_map = {}
        for item in self.design_items:
            if not item.cell_id:
                item.cell_id = f"R{item.row}C{item.col}"
            if item.row < 1 or item.col < 1:
                frappe.throw(f"单元格 {item.cell_id} 的行列号必须大于0")
            position_map[f"{item.row}_{item.col}"] = item

        for item in self.design_items:
            rowspan = cint(item.rowspan or 1)
            colspan = cint(item.colspan or 1)

            if rowspan < 1 or colspan < 1:
                frappe.throw(f"单元格 {item.cell_id} 的跨行数或跨列数必须大于0")
            if item.row + rowspan - 1 > self.rows:
                frappe.throw(f"单元格 {item.cell_id} 的跨行数超出网格范围")
            if item.col + colspan - 1 > self.columns:
                frappe.throw(f"单元格 {item.cell_id} 的跨列数超出网格范围")

            is_self_merged = is_merged_cell(item.cell_value or "")

            # 处理 colspan
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

            # 处理 rowspan
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
        """预览设计"""
        try:
            if isinstance(params, str):
                params = json.loads(params)

            # 加载目标文档（用于 {doc.xxx} 占位符替换）
            doc = None
            if doc_name and self.target_doctype:
                try:
                    doc = frappe.get_doc(self.target_doctype, doc_name)
                except Exception:
                    pass

            # 执行查询
            query_results = {}
            if self.query_code:
                try:
                    result = self.execute_query(
                        self.query_code, self.query_parameters,
                        doc_name, self.target_doctype, params
                    )
                    query_results['main'] = {
                        'data': result,
                        'is_iterable': True
                    }
                except Exception as e:
                    frappe.log_error(frappe.get_traceback(), '查询执行失败')
                    query_results['main'] = {'data': [], 'is_iterable': False}

            html = self.build_preview_html(
                query_results, doc_name, doc, params=params)
            return html

        except Exception as e:
            frappe.log_error(frappe.get_traceback(), '打印预览失败')
            return f'<div class="alert alert-danger">预览失败: {frappe.utils.escape_html(str(e))}</div>'

    def execute_query(self, query_code, parameters=None, doc_name=None, doc_type=None, user_params=None):
        """执行查询"""
        params = parse_parameters(parameters)
        if doc_name and doc_type:
            params = replace_dynamic_params(params, doc_name, doc_type)

        # 注入用户参数
        if user_params and isinstance(user_params, dict):
            params.update(user_params)

        return execute_query_code(query_code, filters=params, format_result=True)

    # ==================== 占位符替换 ====================

    @staticmethod
    def _replace_doc_placeholders(value, doc):
        """替换 {doc.field_name} 占位符为文档实际字段值（不匹配 {doc.xxx.yyy} 子表模式）"""
        if not value or not doc:
            return value or ''

        def replacer(match):
            field_name = match.group(1)
            if hasattr(doc, field_name):
                return str(getattr(doc, field_name) or '')
            return match.group(0)

        return re.sub(r'\{doc\.(\w+)(?!\.)\}', replacer, value)

    @staticmethod
    def _replace_child_table_placeholders(value, child_item):
        """替换 {doc.child_table.field_name} 为子表行的实际值"""
        if not value or not child_item:
            return value or ''

        def replacer(match):
            field_name = match.group(2)
            if hasattr(child_item, field_name):
                return str(getattr(child_item, field_name) or '')
            elif isinstance(child_item, dict) and field_name in child_item:
                return str(child_item.get(field_name, '') or '')
            return match.group(0)

        return re.sub(r'\{doc\.(\w+)\.(\w+)\}', replacer, value)

    @staticmethod
    def _replace_param_placeholders(value, params):
        """替换 {param.param_name} 为用户输入的参数值"""
        if not value or not params:
            return value or ''

        def replacer(match):
            param_name = match.group(1)
            if param_name in params:
                return str(params[param_name] or '')
            return match.group(0)

        return re.sub(r'\{param\.(\w+)\}', replacer, value)

    @staticmethod
    def _detect_child_table_patterns(cell_value):
        """检测 {doc.child_table.field_name} 模式，返回 [(table_name, field_name), ...]"""
        if not cell_value:
            return []
        return re.findall(r'\{doc\.(\w+)\.(\w+)\}', cell_value)

    @staticmethod
    def _replace_header_footer_placeholders(html, page_num, total_pages):
        """替换页眉页脚中的固定占位符"""
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

    # ==================== 行元数据 ====================

    def _build_row_metadata(self):
        """从 design_items 构建行级别映射"""
        row_type_map = {}
        row_display_map = {}
        if self.design_items:
            for item in self.design_items:
                rt = (item.row_type or '').strip()
                rd = (item.row_display or '').strip()
                if rt and rt != '普通行':
                    row_type_map[item.row] = rt
                if rd:
                    row_display_map[item.row] = rd
        return row_type_map, row_display_map

    # ==================== 行扩展（数据驱动行） ====================

    def _build_expanded_rows_v2(self, cell_map, row_styles, doc, query_results):
        """基于 row_type 和子表/查询数据构建展开行列表"""
        all_rows = list(range(1, self.rows + 1))

        # 检测数据驱动行
        data_driven_rows = {}  # {row_num: {'child_tables': set(), 'query_names': set()}}

        if self.design_items:
            for item in self.design_items:
                row_num = item.row
                rt = (item.row_type or '').strip()
                cv = item.cell_value or ''

                # 显式标记为数据驱动行
                is_data_driven = (rt == '数据驱动行')

                # 自动检测子表模式
                child_patterns = self._detect_child_table_patterns(cv)
                if child_patterns:
                    is_data_driven = True

                # 自动检测查询数据绑定
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

        # 获取每个数据驱动行的实际数据
        row_data_map = {}  # {row_num: [data_items]}
        for row_num, info in data_driven_rows.items():
            row_data_map[row_num] = self._get_row_data_items(
                info, doc, query_results)

        # 构建展开行列表
        result = []
        for row in all_rows:
            if row in row_data_map:
                data_items = row_data_map[row]
                if data_items:
                    for data_idx, data_item in enumerate(data_items):
                        result.append({
                            'template_row': row,
                            'data_index': data_idx,
                            'data_item': data_item
                        })
                else:
                    # 无数据时保留一行空行
                    result.append(row)
            else:
                result.append(row)

        return result

    def _get_row_data_items(self, info, doc, query_results):
        """获取数据驱动行的数据列表"""
        # 优先从文档子表获取
        if info.get('child_tables') and doc:
            for table_name in info['child_tables']:
                if hasattr(doc, table_name):
                    child_table = getattr(doc, table_name)
                    if child_table:
                        return [item for item in child_table]

        # 其次从查询结果获取
        if info.get('query_names'):
            for query_name in info['query_names']:
                qr = query_results.get(query_name, {})
                data = qr.get('data', [])
                if data and isinstance(data, list):
                    return data

        return []

    # ==================== 分页 ====================

    def _get_row_height(self, row_data, row_styles):
        """获取单行实际高度(px)"""
        if isinstance(row_data, dict):
            row_num = row_data['template_row']
        else:
            row_num = row_data
        return row_styles.get(str(row_num), {}).get('height', 20)

    def _paginate_rows_v2(self, all_rows, row_type_map, row_styles, paper):
        """基于 row_type 的自动分页"""
        margin_top = cint(paper.margin_top) or 10 if paper else 10
        margin_bottom = cint(paper.margin_bottom) or 10 if paper else 10

        available_mm = (paper.height if paper else 297) - \
            margin_top - margin_bottom
        available_px = available_mm * PX_PER_MM

        # 识别重复标题行
        title_rows = [r for r in all_rows
                      if isinstance(r, int) and row_type_map.get(r) == '重复标题行']

        title_height = sum(
            row_styles.get(str(r), {}).get('height', 20)
            for r in title_rows
        ) if title_rows else 0

        # 数据行 = 非标题行
        data_rows = [r for r in all_rows if r not in title_rows]

        if not data_rows:
            return [all_rows] if all_rows else [[]]

        # 按实际行高逐行累积分页
        content_available = available_px - title_height
        pages = []
        current_page_rows = []
        current_height = 0

        for row_data in data_rows:
            row_h = self._get_row_height(row_data, row_styles)
            if current_page_rows and (current_height + row_h) > content_available:
                # 当前行放不下，换页
                pages.append(title_rows + current_page_rows)
                current_page_rows = []
                current_height = 0
            current_page_rows.append(row_data)
            current_height += row_h

        if current_page_rows:
            pages.append(title_rows + current_page_rows)

        return pages if pages else [all_rows]

    # ==================== 主渲染流程 ====================

    def build_preview_html(self, query_results, doc_name=None, doc=None, params=None):
        """构建预览HTML"""
        try:
            # 解析样式
            row_styles = json.loads(self.row_styles) if self.row_styles else {}
            col_styles = json.loads(self.col_styles) if self.col_styles else {}

            # 获取纸张尺寸
            paper = frappe.get_doc("Super Print Paper", self.print_paper)
            paper_width = paper.width
            paper_height = paper.height

            # 构建行元数据
            row_type_map, row_display_map = self._build_row_metadata()

            # 构建单元格映射
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

            # 构建展开行
            all_rows_data = self._build_expanded_rows_v2(
                cell_map, row_styles, doc, query_results)

            # 构建占位网格（两遍：先放置所有单元格，再标记合并区域）
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

            # 分页
            pages = self._paginate_rows_v2(
                all_rows_data, row_type_map, row_styles, paper)

            # 生成每页 HTML
            body_html = self._build_pages_html(
                pages, cell_map, cell_grid, row_styles, col_styles,
                query_results, doc, row_type_map, row_display_map, paper, params=params
            )

            return self._wrap_full_html(body_html, paper_width, paper_height, row_styles, col_styles, paper)

        except Exception as e:
            frappe.log_error(frappe.get_traceback(), '构建预览HTML失败')
            return f'<div class="alert alert-danger">预览失败: {frappe.utils.escape_html(str(e))}</div>'

    # ==================== 每页HTML构建（含页眉页脚定位） ====================

    def _build_pages_html(self, pages, cell_map, cell_grid, row_styles, col_styles,
                          query_results, doc, row_type_map, row_display_map, paper, params=None):
        """构建所有页面的HTML，每页为固定尺寸容器"""
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

            # 页眉区域：纸张顶部到上边距之间（左/中/右三栏）
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

            # 内容区域：上边距到下边距之间
            page_html += f'<div class="print-page-content" style="position:absolute;top:{content_top:.1f}px;left:0;right:0;bottom:{footer_area_h:.1f}px;padding:0 {margin_right * PX_PER_MM:.1f}px 0 {margin_left * PX_PER_MM:.1f}px;overflow:hidden;">'
            page_html += '<table class="print-form-table">'
            page_html += self._build_colgroup(col_styles)
            for row_num in page_rows:
                page_html += self._build_row_html(
                    row_num, cell_map, cell_grid, row_styles, col_styles,
                    query_results, doc, row_type_map, row_display_map, params=params
                )
            page_html += '</table></div>'

            # 页脚区域：下边距到纸张底部之间（左/中/右三栏）
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

    # ==================== 行/列 HTML ====================

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
        """构建一行HTML"""
        # 处理展开行
        if isinstance(row_data, dict):
            template_row = row_data['template_row']
            data_item = row_data['data_item']
            row_num = template_row
        else:
            row_num = row_data
            data_item = None

        def _is_merged_covering(merged_info, check_row, check_col):
            """检查合并区域是否覆盖指定行列"""
            if not merged_info or not merged_info.get('is_merged'):
                return False
            or_ = merged_info['merge_origin_row']
            oc = merged_info['merge_origin_col']
            rs = merged_info['merge_rowspan']
            cs = merged_info['merge_colspan']
            return or_ <= check_row < or_ + rs and oc <= check_col < oc + cs

        row_style = row_styles.get(str(row_num), {})
        row_display = (row_display_map or {}).get(row_num, '')

        row_style_attr = f'height:{row_style.get("height", 20)}px;'
        if row_style.get('css_style'):
            row_style_attr += row_style['css_style']

        # 行显示效果
        if row_display == '固定行高':
            row_style_attr += 'overflow:hidden;white-space:nowrap;'
        elif row_display == '自动缩小字体':
            row_style_attr += 'overflow:hidden;'
        else:
            # 自动换行（默认）
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

                # 构建样式
                style_attr = 'line-height:1;'
                font_size = row_style.get(
                    'font_size') or (self.font_size or 12)
                style_attr += f'font-size:{font_size}px;'
                if cell_data.get('css_style'):
                    style_attr += cell_data['css_style']

                # 获取单元格值
                cell_value = cell_data.get('cell_value', '')

                # 替换 {doc.field_name} 占位符（单层）
                cell_value = self._replace_doc_placeholders(cell_value, doc)

                # 替换 {param.param_name} 用户参数占位符
                cell_value = self._replace_param_placeholders(
                    cell_value, params)

                # 替换 {doc.child_table.field_name} 子表占位符
                if data_item:
                    cell_value = self._replace_child_table_placeholders(
                        cell_value, data_item)
                    # data_key 方式（兼容旧查询绑定）
                    if cell_data.get('data_key'):
                        dk = cell_data['data_key']
                        if isinstance(data_item, dict):
                            cell_value = str(data_item.get(dk, cell_value))
                        elif hasattr(data_item, dk):
                            cell_value = str(
                                getattr(data_item, dk, cell_value))

                # 查询数据替换（非展开行时，取查询结果首行）
                if not data_item and cell_data.get('query_name') and cell_data.get('data_key'):
                    qr = query_results.get(cell_data['query_name'], {})
                    qr_data = qr.get('data', [])
                    if qr_data and isinstance(qr_data, list) and len(qr_data) > 0:
                        first = qr_data[0]
                        if isinstance(first, dict) and cell_data['data_key'] in first:
                            cell_value = str(first[cell_data['data_key']])

                # 计算单元格尺寸
                cell_rowspan = cell_data['rowspan']
                cell_colspan = cell_data['colspan']
                cell_h = sum(row_styles.get(str(r), {}).get('height', 20)
                             for r in range(row_num, row_num + cell_rowspan))
                cell_w = sum(col_styles.get(str(c), {}).get('width', 60)
                             for c in range(col, col + cell_colspan))

                # 自动缩小字体：估算适合的字号
                if row_display == '自动缩小字体' and cell_value:
                    shrunk = self._estimate_font_size(
                        str(cell_value), cell_w, cell_h, font_size)
                    if shrunk < font_size:
                        style_attr = style_attr.replace(
                            f'font-size:{font_size}px;', f'font-size:{shrunk}px;')

                # 生成内容
                content = self._render_cell_content(
                    cell_data, cell_value, cell_w, cell_h)

                # 图片类单元格：用 background-image 渲染，完全不参与文档流布局
                cell_type = cell_data.get('cell_type', 'static')
                if cell_type in ('barcode', 'qrcode', 'image') and content and '<img' in content:
                    import re as _re
                    src_match = _re.search(r'src="([^"]*)"', content)
                    if src_match:
                        img_src = src_match.group(1)
                        # 解析 text-align 对齐方式
                        css_style = cell_data.get('css_style', '')
                        align = 'center'
                        for prop in css_style.split(';'):
                            prop = prop.strip()
                            if prop.startswith('text-align:'):
                                align = prop.split(':', 1)[1].strip()
                                break
                        # background-image 完全不影响单元格尺寸
                        # 确保 style_attr 以 ; 结尾，防止属性粘连
                        if not style_attr.rstrip().endswith(';'):
                            style_attr += ';'
                        style_attr += (
                            f'background:url(\'{img_src}\') no-repeat {align} center/contain;'
                        )
                        content = '&nbsp;'

                    # 抑制合并区域产生的幽灵边框（WeasyPrint border-collapse 兼容）
                    border_suppress = ''
                    # 检查左侧邻居是否为合并区域覆盖
                    if col > 1:
                        left_merged = cell_grid[grid_row][grid_col - 1] if grid_row < len(
                            cell_grid) and grid_col - 1 < len(cell_grid[0]) else None
                        if _is_merged_covering(left_merged, row_num, col - 1) or _is_merged_covering(left_merged, row_num, col):
                            border_suppress += 'border-left:none;'
                    # 检查上方邻居是否为合并区域覆盖
                    if row_num > 1:
                        top_merged = cell_grid[grid_row - 1][grid_col] if grid_row - 1 < len(
                            cell_grid) and grid_col < len(cell_grid[0]) else None
                        if _is_merged_covering(top_merged, row_num - 1, col) or _is_merged_covering(top_merged, row_num, col):
                            border_suppress += 'border-top:none;'
                    style_attr += border_suppress

                html += f'<td {rowspan_attr} {colspan_attr} style="{style_attr}">{content}</td>'
            else:
                # 空单元格 - 同样需要抑制合并区域边框
                empty_style = 'line-height:1;'
                col_style = col_styles.get(str(col), {})
                if col_style.get('css_style'):
                    empty_style += col_style['css_style']
                # 空单元格也检查合并边框
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
        """估算自动缩小字体的字号"""
        if not text:
            return base_font_size
        char_count = len(text)
        if char_count == 0:
            return base_font_size
        # 中文字符约 font_size 宽，英文约 font_size*0.6
        # 粗略估算：混合字符平均宽度 = font_size * 0.7
        avg_char_w = base_font_size * 0.7
        chars_per_line = max(1, cell_w / avg_char_w)
        lines_needed = max(1, char_count / chars_per_line)
        total_height = lines_needed * base_font_size * 1.2
        if total_height <= cell_h:
            return base_font_size
        ratio = cell_h / total_height
        return max(6, int(base_font_size * ratio))

    # ==================== 单元格内容渲染 ====================

    def _render_cell_content(self, cell_data, cell_value, cell_w=100, cell_h=40):
        """渲染单元格内容"""
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
        """渲染条形码"""
        if not value:
            return '<span style="color:#999">--</span>'
        # 限制在单元格尺寸内，保持长宽比
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
        """渲染二维码"""
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
        """解析CSS字符串"""
        pairs = {}
        if not css_string:
            return pairs
        for pair in css_string.split(';'):
            trimmed = pair.strip()
            if trimmed and ':' in trimmed:
                key, val = trimmed.split(':', 1)
                pairs[key.strip()] = val.strip()
        return pairs

    # ==================== 完整HTML包装 ====================

    def _wrap_full_html(self, body_html, paper_width, paper_height, row_styles, col_styles, paper=None):
        """包装完整HTML"""
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

    # ==================== 启用条件 ====================

    @staticmethod
    def check_enable_conditions(design_name, doc=None):
        """检查设计是否对当前文档/用户启用"""
        design = frappe.get_doc("Super Print Design", design_name)
        if not design.enabled:
            return False

        condition = (design.enable_condition or '').strip()
        if not condition:
            return True

        # 兼容 eval: 前缀和 JS 语法
        if condition.startswith('eval:'):
            condition = condition[5:].strip()
        condition = condition.replace('===', '==').replace('!==', '!=')

        try:
            return frappe.safe_eval(condition, {}, {'doc': doc, 'user': frappe.session.user})
        except Exception:
            frappe.log_error(frappe.get_traceback(),
                             f'启用条件评估失败: {design_name}')
            return False
