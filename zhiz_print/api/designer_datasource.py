# -*- coding: utf-8 -*-
# Copyright (c) 2026, Guangde Zhizhao Technology Co., Ltd.
# For license information, please see LICENSE
"""设计器「数据源」树 API(v15.23)。

返回设计可用数据的三级树:
  doc  → 目标单据字段 + 子表(子表字段为三级节点)
  rep  → 报表模式三类(报表字段/筛选字段/报表数据列)
  param→ 设计参数
  query→ 查询定义(执行样例取列)
"""

import json

import frappe
from frappe import _

SKIP_FIELDTYPES = ("Section Break", "Column Break", "Tab Break", "HTML",
                   "Button", "Fold", "Table", "Table MultiSelect", "Heading")


def _meta_fields(doctype):
    """DocType 字段清单(供绑定用的主表字段,排除布局型)。"""
    out = []
    try:
        meta = frappe.get_meta(doctype)
        for df in meta.fields:
            if df.fieldtype in SKIP_FIELDTYPES or df.fieldname.startswith("__"):
                continue
            out.append({"fieldname": df.fieldname,
                        "label": df.label or df.fieldname,
                        "fieldtype": df.fieldtype})
    except Exception:
        pass
    return out


@frappe.whitelist()
def get_datasource_tree(design_name=None, target_doctype=None, report_name=None):
    """数据源树。design_name 优先(读已存设计),否则用显式参数。"""
    if design_name:
        val = frappe.db.get_value("Super Print Design", design_name,
                                  ["design_target", "target_doctype", "report_name"],
                                  as_dict=True)
        if val:
            target_doctype = target_doctype or val.target_doctype
            report_name = report_name or val.report_name
            if (val.design_target or "DocType") != "Report":
                report_name = None

    tree = []

    # ① 单据 doc
    if target_doctype:
        children = []
        for f in _meta_fields(target_doctype):
            children.append({
                "key": "doc." + f["fieldname"],
                "label": "{0} ({1})".format(f["label"], f["fieldname"]),
                "leaf": True,
            })
        # 子表字段:doc.<child>.<field>(子表标识为 doc.<child>)
        try:
            meta = frappe.get_meta(target_doctype)
            for df in meta.fields:
                if df.fieldtype == "Table":
                    sub = [{
                        "key": "doc.{0}.{1}".format(df.fieldname, sf.fieldname),
                        "label": "{0} ({1})".format(sf.label or sf.fieldname, sf.fieldname),
                        "leaf": True,
                    } for sf in frappe.get_meta(df.options).fields
                        if sf.fieldtype not in SKIP_FIELDTYPES]
                    if sub:
                        children.append({
                            "key": "doc." + df.fieldname,
                            "label": "{0} [子表]".format(df.label or df.fieldname),
                            "children": sub,
                        })
        except Exception:
            pass
        tree.append({
            "key": "doc", "label": "单据字段 {0}".format(target_doctype),
            "icon": "fa-file-text-o", "children": children,
        })

    # ② 报表 rep(报表模式)
    if report_name:
        from zhiz_print.api.report_print import (
            _get_report_filter_fields, get_report_sample_data, _default_report_filters)
        rep_children = []
        # 报表本身字段
        try:
            rf = frappe.db.get_value("Report", report_name,
                                     ["name", "report_name", "ref_doctype", "module"], as_dict=True)
            rep_children.append({
                "key": "rep-meta", "label": "报表字段", "icon": "fa-info-circle",
                "children": [{"key": "rep." + k, "label": "{0} ({1})".format(k, v), "leaf": True}
                             for k, v in (("name", rf.name), ("report_name", rf.report_name),
                                          ("ref_doctype", rf.ref_doctype), ("module", rf.module))],
            })
        except Exception:
            pass
        # 筛选字段
        fl = _get_report_filter_fields(report_name)
        if fl:
            rep_children.append({
                "key": "rep-filters", "label": "筛选字段", "icon": "fa-filter",
                "children": [{"key": "rep.filters." + k, "label": k, "leaf": True} for k in fl],
            })
        # 报表数据列(取样)
        sample = get_report_sample_data(report_name, {}, 5)
        cols = sample.get("columns") or []
        if cols:
            rep_children.append({
                "key": "rep-items", "label": "报表数据列", "icon": "fa-table",
                "children": [{"key": "rep.items." + c["fieldname"],
                              "label": "{0} ({1})".format(c["label"], c["fieldname"]),
                              "leaf": True} for c in cols],
            })
        if rep_children:
            tree.append({
                "key": "rep", "label": "报表 {0}".format(report_name),
                "icon": "fa-table", "children": rep_children,
            })

    # ③ 设计参数 param
    if design_name:
        params = frappe.get_all("Super Print Design Parameter",
                                filters={"parent": design_name, "parenttype": "Super Print Design"},
                                fields=["param_name", "param_label"], order_by="idx")
        if params:
            tree.append({
                "key": "param", "label": "设计参数", "icon": "fa-sliders",
                "children": [{"key": "param." + p.param_name,
                              "label": "{0} ({1})".format(p.param_label or p.param_name, p.param_name),
                              "leaf": True} for p in params],
            })

    # ④ 查询定义 query
    if design_name:
        queries = frappe.get_all("Super Print Design Query",
                                 filters={"parent": design_name, "parenttype": "Super Print Design"},
                                 fields=["name", "query_name", "query_code", "parameters"], order_by="idx")
        q_children = []
        for q in queries:
            cols = _query_columns(q)
            q_children.append({
                "key": "q-" + q.query_name,
                "label": "{0} [查询]".format(q.query_name),
                "icon": "fa-database",
                "children": [{"key": "{0}.{1}".format(q.query_name, c),
                              "label": c, "leaf": True} for c in cols],
            })
        if q_children:
            tree.append({
                "key": "query", "label": "查询定义", "icon": "fa-database",
                "children": q_children,
            })

    return {"tree": tree}


def _query_columns(q):
    """查询列名:优先执行样例取列,失败时从代码静态抽取 result[0] 的键。"""
    try:
        from zhiz_print.utils.query_executor import execute_query_code, parse_parameters
        params = parse_parameters(q.parameters or "")
        res = execute_query_code(q.query_code, filters=params)
        if isinstance(res, list) and res and isinstance(res[0], dict):
            return [str(k) for k in res[0].keys()][:40]
    except Exception:
        pass
    # 静态兜底:'result' 字面量 dict 键
    import re
    keys = re.findall(r"[\"'](\w+)[\"']\s*:", q.query_code or "")
    seen, out = set(), []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out[:40]
