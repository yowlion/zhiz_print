# -*- coding: utf-8 -*-
# Copyright (c) 2026, Zhiz Print and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe


def get_boot_settings(bootinfo):
    """boot_session hook: inject zhiz_print settings into frappe.boot.zhiz_print"""
    # 系统级打印配置对所有登录用户生效。Zprint Setting/Super Print Paper/Super Print Design
    # 默认仅 System Manager 可读,普通用户查询会抛 PermissionError 被外层 try-except 吞,
    # 导致其 boot.print_designer.enabled=False / doctype_has_design={},进而走原生打印界面。
    # 此处临时关闭权限校验,仅读取公共配置(无写入),用完立即恢复。
    prev = frappe.flags.ignore_permission
    frappe.flags.ignore_permission = True
    try:
        setting = frappe.get_single("Zprint Setting")
        result = _get_print_designer_boot_settings(setting)
    except Exception:
        result = {"enabled": False}
    finally:
        frappe.flags.ignore_permission = prev

    # Get license info (auto-creates trial if needed)
    license_info = {}
    try:
        from zhiz_print.api.license import get_license_info_for_boot
        license_info = get_license_info_for_boot()
    except Exception:
        pass

    bootinfo["zhiz_print"] = {
        "print_designer": result,
        "license": license_info,
    }


@frappe.whitelist()
def refresh_boot_cache():
    """Clear cache and rebuild boot settings for current session"""
    frappe.only_for("System Manager")
    frappe.clear_cache()
    setting = frappe.get_single("Zprint Setting")

    license_info = {}
    try:
        from zhiz_print.api.license import get_license_info_for_boot
        license_info = get_license_info_for_boot()
    except Exception:
        pass

    return {
        "print_designer": _get_print_designer_boot_settings(setting),
        "license": license_info,
    }


def _get_print_designer_boot_settings(setting):
    """Get print designer boot configuration"""
    try:
        enabled = bool(frappe.utils.cint(setting.get("enable_super_print_page")))
        result = {"enabled": enabled}

        if enabled:
            # Preload paper data (with margins)
            papers = frappe.get_all(
                "Super Print Paper",
                filters={"enabled": 1},
                fields=["name", "width", "height",
                        "margin_top", "margin_bottom", "margin_left", "margin_right"],
            )
            result["papers"] = {p["name"]: p for p in papers}

            # Enable mode and specific doctype list
            mode = setting.get("print_enable_mode") or "Enable for All"
            result["enable_mode"] = mode
            if mode == "Enable for Specific":
                doctypes = []
                for item in setting.get("print_enabled_doctypes", []):
                    if item.enabled and item.doctype_name:
                        doctypes.append(item.doctype_name)
                result["enabled_doctypes"] = doctypes

            # 精准判定:注入哪些 doctype 有可用的 Super Print Design
            # print.js make() 据此判定是否进高级打印,避免无设计的 doctype 误入(Enable for All 模式下)
            try:
                # 注意:frappe.flags.ignore_permission 对 db.get_list 无效(DatabaseQuery 自带权限层),
                # 必须用 get_all + 显式 ignore_permissions=True 才能让普通用户也拿到设计列表
                _dts = frappe.get_all("Super Print Design", filters={"enabled": 1},
                    fields=["target_doctype"], distinct=True, pluck="target_doctype",
                    ignore_permissions=True)
                result["doctype_has_design"] = {dt: True for dt in _dts if dt}
            except Exception:
                result["doctype_has_design"] = {}

            # Export settings
            result["allow_export_pdf"] = bool(frappe.utils.cint(setting.get("allow_export_pdf")))
            result["allow_export_excel"] = bool(frappe.utils.cint(setting.get("allow_export_excel")))

            # Native Print Formats toggle (sidebar collapsible section)
            result["enable_native_print_formats"] = bool(frappe.utils.cint(setting.get("enable_native_print_formats")))

            # 报表打印配置(v15.23):总开关 + Enable for All/Specific + 命中报表列表 +
        # 有设计的报表列表(拦截层判定"启用即接管"用,与 doctype_has_design 同语义)
        report_enabled = bool(frappe.utils.cint(setting.get("report_print_enabled")))
        result["report_enabled"] = report_enabled
        if report_enabled:
            rmode = setting.get("report_enable_mode") or "Enable for All"
            result["report_enable_mode"] = rmode
            rlist = []
            if rmode == "Enable for Specific":
                for item in setting.get("report_enabled_reports", []):
                    if item.enabled and item.report_name:
                        rlist.append(item.report_name)
            result["report_enabled_list"] = rlist
            try:
                _rns = frappe.get_all("Super Print Design",
                    filters={"enabled": 1, "design_target": "Report"},
                    fields=["report_name"], distinct=True, pluck="report_name",
                    ignore_permissions=True)
                result["report_has_design"] = {rn: True for rn in _rns if rn}
            except Exception:
                result["report_has_design"] = {}

        # PDF engine mode (avoid frontend DB call)
            result["pdf_engine_mode"] = setting.get("pdf_engine_mode") or "wkhtmltopdf"

            # License expired flag
            try:
                from zhiz_print.api.license import check_license_valid
                valid, info = check_license_valid()
                result["expired"] = info.get("expired", False)
                result["license_message"] = info.get("message", "")
            except Exception:
                pass

        return result
    except Exception:
        return {"enabled": False}
