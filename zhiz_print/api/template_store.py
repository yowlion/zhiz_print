# -*- coding: utf-8 -*-
"""智兆模板平台 — 客户端(推送/安装)。

share_template: 序列化当前设计 + 渲染预览 + 推送到 zhiz_licser template_api。
install_template: 从 zhiz_licser 拉模板 + 本地重建(纸张/设计重名处理 + doctype 检查)。
"""
from __future__ import unicode_literals
import json
import frappe
from zhiz_print.api.license import _call_license_api, get_machine_id


# Frappe 内部字段(清洗时剔除)
_INTERNAL_FIELDS = ["name", "owner", "creation", "modified", "modified_by",
    "docstatus", "idx", "parent", "parentfield", "parenttype", "doctype",
    "__last_sync_on", "__unsaved", "_user_tags", "_assign", "_comments",
    "_liked_by", "lft", "rgt"]


def _clean_doc(d):
    """剔除 Frappe 内部字段(递归子表)。"""
    for f in _INTERNAL_FIELDS:
        d.pop(f, None)
    for k, v in list(d.items()):
        if isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    _clean_doc(item)
    return d


@frappe.whitelist()
def share_template(design_name):
    """序列化设计 + 渲染预览 + 推送到模板平台。"""
    if not design_name:
        frappe.throw("Design name required")

    design = frappe.get_doc("Super Print Design", design_name)
    design_data = _clean_doc(design.as_dict(no_nulls=True))

    # 渲染预览 HTML(with sample_doc,无则空)
    preview_html = ""
    try:
        if design.sample_doc:
            preview_html = design.get_preview_for_document(doc_name=design.sample_doc) or ""
    except Exception:
        preview_html = ""

    # 纸张配置
    paper_config = {}
    try:
        paper = frappe.get_doc("Super Print Paper", design.print_paper).as_dict()
        paper_config = {k: paper.get(k) for k in [
            "paper_name", "width", "height",
            "margin_top", "margin_bottom", "margin_left", "margin_right"]}
    except Exception:
        pass

    # 授权信息(author identity)
    lic = frappe.get_all("Zprint License", limit=1, order_by="activated_at desc",
        fields=["license_key", "company_name"])
    license_key = lic[0]["license_key"] if lic else ""
    company = lic[0]["company_name"] if lic else ""

    import zhiz_print
    body = {
        "template_name": design.design_name,
        "target_doctype": design.target_doctype,
        "print_paper_name": design.print_paper or "",
        "print_paper_config": json.dumps(paper_config, ensure_ascii=False),
        "design_data": json.dumps(design_data, ensure_ascii=False, default=str),
        "preview_html": preview_html,
        "zhiz_print_version": getattr(zhiz_print, "__version__", ""),
        "author_license_key": license_key,
        "author_machine_id": get_machine_id(),
        "author_company": company,
    }

    result = _call_license_api("push_template", body, module="template_api")
    if not result or not result.get("success"):
        return {"success": False, "error": (result or {}).get("error", "Push failed")}
    return {
        "success": True,
        "template_id": result.get("template_id"),
        "is_new": result.get("is_new"),
        "version": result.get("version"),
    }


@frappe.whitelist()
def list_templates(target_doctype=None):
    """从模板平台拉模板列表。"""
    body = {}
    if target_doctype:
        body["target_doctype"] = target_doctype
    result = _call_license_api("list_templates", body, module="template_api")
    if not result:
        return {"templates": [], "error": "Cannot reach template platform"}
    return {"templates": result.get("templates", []), "count": result.get("count", 0)}


@frappe.whitelist()
def get_template(template_id):
    """从模板平台拉单个模板完整数据。"""
    result = _call_license_api("get_template", {"template_id": template_id}, module="template_api")
    if not result or "template" not in result:
        return {"error": (result or {}).get("error", "Template not found")}
    return result.get("template")


@frappe.whitelist()
def add_template_comment(template_id, content):
    """提交评论。"""
    lic = frappe.get_all("Zprint License", limit=1, order_by="activated_at desc",
        fields=["license_key", "company_name"])
    license_key = lic[0]["license_key"] if lic else ""
    company = lic[0]["company_name"] if lic else ""
    result = _call_license_api("add_comment", {
        "template_id": template_id,
        "author_license_key": license_key,
        "author_display": company or "匿名",
        "content": content,
    }, module="template_api")
    return result or {"success": False}


@frappe.whitelist()
def list_template_comments(template_id):
    """拉模板评论。"""
    result = _call_license_api("list_comments", {"template_id": template_id}, module="template_api")
    return {"comments": (result or {}).get("comments", [])}


@frappe.whitelist()
def install_template(template_id, new_design_name=None, new_paper_name=None):
    """从模板平台拉模板 + 本地安装。

    1. doctype 检查(target_doctype 本地存在?)
    2. 纸张重名(同名同配置复用 / 同名异配置用 new_paper_name / 无则建)
    3. 设计重名(用 new_design_name 或原名)
    4. 剔 sample_doc
    5. 创建 Super Print Design
    6. 下载计数 +1
    """
    tpl = get_template(template_id)
    if not tpl or tpl.get("error"):
        frappe.throw((tpl or {}).get("error", "Cannot fetch template"))

    target_doctype = tpl.get("target_doctype")
    # 1. doctype 检查
    if not frappe.db.exists("DocType", target_doctype):
        frappe.throw("本地不存在 DocType「{0}」,无法安装此模板".format(target_doctype))

    # 2. 纸张处理
    paper_name = tpl.get("print_paper_name") or ""
    paper_config = {}
    try:
        paper_config = json.loads(tpl.get("print_paper_config") or "{}")
    except Exception:
        paper_config = {}

    final_paper = paper_name
    if paper_name and frappe.db.exists("Super Print Paper", paper_name):
        # 检查配置是否一致
        local_paper = frappe.db.get_value("Super Print Paper", paper_name, [
            "width", "height", "margin_top", "margin_bottom",
            "margin_left", "margin_right"], as_dict=True)
        config_match = all(
            (local_paper.get(k) == paper_config.get(k))
            for k in ["width", "height", "margin_top", "margin_bottom", "margin_left", "margin_right"])
        if not config_match:
            # 同名异配置 → 用 new_paper_name
            if not new_paper_name:
                frappe.throw("纸张「{0}」已存在但配置不同,请指定新纸张名(new_paper_name)".format(paper_name))
            final_paper = new_paper_name
            _create_paper(final_paper, paper_config)
    elif paper_name:
        # 无同名 → 建
        final_paper = paper_name
        _create_paper(final_paper, paper_config)

    # 3. 设计重名
    design_name = new_design_name or tpl.get("template_name")
    if frappe.db.exists("Super Print Design", design_name):
        frappe.throw("设计「{0}」已存在,请指定新名称(new_design_name)".format(design_name))

    # 4. 还原 design_data + 剔 sample_doc + 新名 + 新纸张
    try:
        design_data = json.loads(tpl.get("design_data") or "{}")
    except Exception:
        frappe.throw("模板设计数据损坏")

    design_data["doctype"] = "Super Print Design"
    design_data["design_name"] = design_name
    design_data["print_paper"] = final_paper
    design_data["sample_doc"] = ""  # 剔除演示单据
    design_data.pop("name", None)

    # 5. 创建
    doc = frappe.get_doc(design_data)
    frappe.flags.skip_zhiz_print_license = True
    try:
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
    finally:
        frappe.flags.skip_zhiz_print_license = False

    # 6. 下载计数 +1
    _call_license_api("increment_download", {"template_id": template_id}, module="template_api")

    return {"success": True, "design_name": design_name}


def _create_paper(paper_name, config):
    """用模板纸张配置创建本地纸张(若不存在)。"""
    if frappe.db.exists("Super Print Paper", paper_name):
        return
    doc = frappe.get_doc({
        "doctype": "Super Print Paper",
        "paper_name": paper_name,
        "width": config.get("width") or 210,
        "height": config.get("height") or 297,
        "margin_top": config.get("margin_top") or 10,
        "margin_bottom": config.get("margin_bottom") or 10,
        "margin_left": config.get("margin_left") or 10,
        "margin_right": config.get("margin_right") or 10,
    })
    doc.insert(ignore_permissions=True)
