# -*- coding: utf-8 -*-
# Copyright (c) 2026, Guangde Zhizhao Technology Co., Ltd. and contributors
# For license information, please see LICENSE
"""报表打印 API(P1 方案A2)。

数据流:报表页拦截入口 → 预览页(print/Report/<name> 报表模式) → 本模块渲染。
报表行通过伪查询 __report_main__ 注入现有渲染管线(design.get_preview_for_document),
分页/表头重复/锁高等能力全部复用,详见 .claude/plan/SPD_REPORT_PRINT_P1_DESIGN.md。
"""

import json
import os
import re

import frappe
from frappe import _
from frappe.desk.query_report import run as run_query_report
from frappe.utils import cint

from zhiz_print.api.print_designer import _check_license

# 报表行注入的保留伪查询键(设计器数据驱动行绑此数据源)
REPORT_MAIN_KEY = '__report_main__'

# 超过该行数提示改用 Excel(预览仍可强制继续)
ROW_LIMIT_WARNING = 3000


def _as_dict(v):
    if not v:
        return None
    if isinstance(v, dict):
        return v
    try:
        return json.loads(v)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _check_report_permission(report_name):
    if not frappe.has_permission("Report", "read", report_name):
        frappe.throw(_("No permission to access this report"), frappe.PermissionError)


def _run_report(report_name, filters):
    """同用户身份执行报表,返回 (rows, columns)。

    rows: list[dict](generate_report_result 内部 normalize_result 已转 dict);
    columns: list[dict](fieldname/label/fieldtype)。
    prepared report 处理:显式带了 prepared_report_name 的筛选走预生成数据;
    否则强制同步执行(ignore_prepared_report=True)——GL/Stock Balance 等预生成型
    报表在无已完成预生成时 run() 返回空列零异常,打印场景必须同步真跑。"""
    filters = dict(filters or {})
    has_prepared_name = bool(filters.get("prepared_report_name"))
    res = run_query_report(
        report_name=report_name, filters=filters,
        ignore_prepared_report=not has_prepared_name)
    columns = res.get("columns") or []
    rows = res.get("result") or []
    # Query Report(SQL型)兜底:极旧写法 result 为 list[list] 时按列转 dict
    if rows and not isinstance(rows[0], dict):
        keys = [c.get("fieldname") or str(i) for i, c in enumerate(columns)]
        rows = [dict(zip(keys, r)) for r in rows if isinstance(r, (list, tuple))]
    return rows, columns


def _get_report_filter_fields(report_name):
    """报表筛选字段名清单,双通道:

    1) Report doc 的 filters 字段(Query Report 型报表的定义存放处);
    2) 报表目录 <name>.js 中 filters 数组的 fieldname(标准/自定义 Script Report
       的筛选定义在 JS 里,服务端只能从文件正则抽取,覆盖 frappe 标准 erpnext 报表)。"""
    fields = []
    # 1) Query Report 型
    try:
        fj = frappe.db.get_value("Report", report_name, "filters") or ""
        if fj:
            for fd in json.loads(fj):
                fn = (fd or {}).get("fieldname")
                if fn:
                    fields.append(str(fn))
    except Exception:
        pass

    # 2) Script Report 型:解析报表 JS 文件的 filters 数组
    try:
        rep = frappe.get_doc("Report", report_name)
        module = rep.module or frappe.db.get_value("DocType", rep.ref_doctype, "module")
        if module and not frappe.get_cached_value("Module Def", module, "custom"):
            from frappe.modules import get_module_path, scrub
            js_path = os.path.join(get_module_path(module), "report",
                                   scrub(report_name), scrub(report_name) + ".js")
            if os.path.exists(js_path):
                with open(js_path, encoding="utf-8") as f:
                    js = f.read()
                m = re.search(r'filters\s*:\s*\[', js)
                if m:
                    seg = js[m.end():m.end() + 30000]
                    end = seg.find(']')
                    if end > 0:
                        seg = seg[:end]
                    fields += re.findall(r'fieldname\s*[:=]\s*[\'"](\w+)[\'"]', seg)
    except Exception:
        pass

    # 去重保序
    seen, out = set(), []
    for f in fields:
        if f and f not in seen:
            seen.add(f)
            out.append(f)
    return out


@frappe.whitelist()
def get_report_designs(report_name):
    """报表绑定的可用设计列表(预览页模板选择器)。

    结构与 print_designer.get_available_designs 对齐(name/label/is_default),
    默认设计 = priority 最小的启用设计;草稿设计也列出并标记(draft_no_print
    仅在打印拦截生效,预览页允许选择查看)。"""
    _check_license()
    _check_report_permission(report_name)

    designs = frappe.get_all(
        "Super Print Design",
        filters={"design_target": "Report", "report_name": report_name, "enabled": 1},
        fields=["name", "design_name", "print_paper", "priority",
                "draft_no_print", "page_count"],
        order_by="priority asc, design_name",
    )
    if not designs:
        return []

    result = []
    default_name = designs[0]["name"] if designs else None
    for d in designs:
        paper_info = {}
        if d.print_paper:
            paper_info = frappe.db.get_value(
                "Super Print Paper", d.print_paper,
                ["width", "height", "margin_top", "margin_bottom",
                 "margin_left", "margin_right"], as_dict=True) or {}
        has_params = frappe.db.count(
            "Super Print Design Parameter",
            filters={"parent": d.name, "parenttype": "Super Print Design"})
        result.append({
            "name": d.name,
            "design_name": d.design_name or d.name,
            "label": d.design_name or d.name,
            "is_default": (d.name == default_name),
            "print_paper": d.print_paper,
            "draft_no_print": cint(d.draft_no_print),
            "page_count": cint(d.page_count or 1),
            "paper_info": paper_info,
            "has_params": bool(has_params),
        })
    return result


@frappe.whitelist()
def get_report_sample_data(report_name, filters=None, sample_rows=20):
    """设计器报表模式:列清单 + 演示数据(前 N 行)。

    filters 为空时逐级补默认筛选重试:原样 → +公司(用户默认) → +公司+当日区间。
    绝大多数 erpnext 报表必填 company,这样设计器无需用户填筛选即可拿到列清单;
    全部失败时返回 error 提示(前端提示先在报表页运行一次)。"""
    _check_license()
    _check_report_permission(report_name)

    given = _as_dict(filters) or {}

    attempts = [given]
    if not given.get("company"):
        company = (frappe.defaults.get_user_default("Company")
                   or frappe.defaults.get_global_default("company"))
        if company:
            a = dict(given)
            a["company"] = company
            attempts.append(a)
            b = dict(a)
            b.setdefault("from_date", frappe.utils.nowdate())
            b.setdefault("to_date", frappe.utils.nowdate())
            attempts.append(b)

    rows, columns, last_err = [], [], ""
    for f in attempts:
        try:
            rows, columns = _run_report(report_name, f)
            if columns:
                break
        except Exception as e:
            last_err = str(e)
            rows, columns = [], []

    cols = [{
        "fieldname": c.get("fieldname") or "",
        "label": c.get("label") or c.get("fieldname") or "",
        "fieldtype": c.get("fieldtype") or "Data",
    } for c in columns if (c.get("fieldname") or c.get("label"))]

    if not cols:
        hint = last_err or _("Report returned no columns. Run the report once in the report view with filters, then open the designer from the report page.")
        return {"columns": [], "rows": [], "error": str(hint)}

    # v15.22.18 三类数据键(rep 命名空间):
    # 1类 报表本身字段(rep.name 等,Report 文档字段)
    report_fields = []
    try:
        rf = frappe.db.get_value("Report", report_name,
            ["name", "report_name", "ref_doctype", "module", "is_standard"], as_dict=True)
        if rf:
            rf_fields = [("name", rf.name), ("report_name", rf.report_name),
                         ("ref_doctype", rf.ref_doctype), ("module", rf.module),
                         ("is_standard", rf.is_standard)]
            report_fields = [{"key": k, "label": "{0} ({1})".format(k, v or k)}
                             for k, v in rf_fields]
    except Exception:
        report_fields = []

    # 2类 筛选字段(rep.filters.字段名):传入 filters 的键 + 报表定义双通道
    # (Report doc filters + 报表 JS 文件解析,Script Report 筛选在 JS 里)
    filter_fields = []
    for k in (given or {}).keys():
        if k and not str(k).startswith('__') and k != 'prepared_report_name':
            filter_fields.append(str(k))
    for fn in _get_report_filter_fields(report_name):
        if fn not in filter_fields:
            filter_fields.append(fn)

    n = max(1, min(cint(sample_rows) or 20, 200))
    return {
        "columns": cols, "rows": rows[:n], "total_rows": len(rows),
        "report_fields": report_fields, "filter_fields": filter_fields,
    }


@frappe.whitelist()
def render_report_preview(report_name, filters, design_name, params=None,
                          measurement_only=False, page_break_map=None,
                          row_heights=None, shrink_map=None):
    """报表渲染入口,返回结构与 render_print_preview 完全一致(前端零适配)。

    v15.10.02 双段式照搬:measurement_only=True 只返回量高脚手架,
    客户端实测分页后带 page_break_map 再来一次精确渲染。"""
    _check_license()
    _check_report_permission(report_name)

    filters = _as_dict(filters) or {}
    params = _as_dict(params) or {}
    page_break_map = _as_dict(page_break_map)
    row_heights = _as_dict(row_heights)
    shrink_map = _as_dict(shrink_map)

    design = frappe.get_doc("Super Print Design", design_name)
    if (design.design_target or 'DocType') != 'Report' or design.report_name != report_name:
        frappe.throw(_("设计与报表不匹配"))

    paper = frappe.get_doc("Super Print Paper", design.print_paper)

    rows, _columns = _run_report(report_name, filters)
    inject = {REPORT_MAIN_KEY: {'data': rows}}
    meta = {
        "row_count": len(rows),
        "row_limit_warning": ROW_LIMIT_WARNING,
        "row_limit_exceeded": len(rows) > ROW_LIMIT_WARNING,
    }

    if page_break_map:
        html = design.get_preview_for_document(
            doc_name=None, params=params,
            page_break_map=page_break_map, row_heights=row_heights,
            shrink_map=shrink_map,
            inject_query_results=inject, report_filters=filters)
        return {
            "html": html,
            "precise": True,
            "paper_width": paper.width,
            "paper_height": paper.height,
            "margin_top": paper.margin_top or 0,
            "margin_bottom": paper.margin_bottom or 0,
            "margin_left": paper.margin_left or 0,
            "margin_right": paper.margin_right or 0,
            **meta,
        }

    if cint(measurement_only):
        measurement_html, m = design.get_measurement_for_document(
            doc_name=None, params=params,
            inject_query_results=inject, report_filters=filters)
        return {
            "html": None,
            "precise": False,
            "measurement_only": True,
            "measurement_html": measurement_html,
            "content_h_px": m.get("content_h_px"),
            "content_w_px": m.get("content_w_px"),
            "page_count": m.get("page_count"),
            "blocks": m.get("blocks"),
            "paper_width": paper.width,
            "paper_height": paper.height,
            "margin_top": paper.margin_top or 0,
            "margin_bottom": paper.margin_bottom or 0,
            "margin_left": paper.margin_left or 0,
            "margin_right": paper.margin_right or 0,
            **meta,
        }

    # 兜底:无实测分页参数时走估算渲染(与文档渲染同语义)
    html = design.get_preview_for_document(
        doc_name=None, params=params,
        inject_query_results=inject, report_filters=filters)
    return {
        "html": html,
        "precise": False,
        "paper_width": paper.width,
        "paper_height": paper.height,
        "margin_top": paper.margin_top or 0,
        "margin_bottom": paper.margin_bottom or 0,
        "margin_left": paper.margin_left or 0,
        "margin_right": paper.margin_right or 0,
        **meta,
    }


@frappe.whitelist()
def generate_report_pdf(report_name, filters, design_name, params=None,
                        page_break_map=None, row_heights=None, shrink_map=None):
    """报表 PDF 生成(POST 专用:报表筛选可能超 GET URL 长度限制)。

    复用 generate_print_pdf 的三引擎链 — doctype='Report'/docname=报表名,
    report_filters+inject_query_results 穿参至 _render_print_html 的报表分支。
    注意:draft_no_print 语义仍生效 — 报表 doc 恒为 docstatus=0,标记了
    draft_no_print 的报表设计会被拦截(视为草稿设计不可出正式件)。"""
    _check_license()
    _check_report_permission(report_name)

    filters = _as_dict(filters) or {}
    rows, _columns = _run_report(report_name, filters)

    from zhiz_print.api.print_designer import generate_print_pdf
    return generate_print_pdf(
        doctype='Report', docname=report_name, design_name=design_name,
        params=params, page_break_map=page_break_map, row_heights=row_heights,
        shrink_map=shrink_map,
        report_filters=filters,
        inject_query_results={REPORT_MAIN_KEY: {'data': rows}},
    )


@frappe.whitelist()
def export_report_excel(report_name, filters, design_name, params=None):
    """报表 Excel 导出(POST):复用 export_print_excel 的 HTML→openpyxl 管线,
    注入 __report_main__ 报表行渲染后解析表格写出。"""
    _check_license()
    _check_report_permission(report_name)

    filters = _as_dict(filters) or {}
    rows, _columns = _run_report(report_name, filters)

    from zhiz_print.api.print_designer import export_print_excel
    return export_print_excel(
        doctype='Report', docname=report_name, design_name=design_name,
        params=params,
        report_filters=filters,
        inject_query_results={REPORT_MAIN_KEY: {'data': rows}},
    )
