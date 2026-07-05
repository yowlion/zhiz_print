# -*- coding: utf-8 -*-
# Copyright (c) 2026, Zhiz Print and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe


def get_boot_settings(bootinfo):
    """boot_session hook: inject zhiz_print settings into frappe.boot.zhiz_print"""
    try:
        setting = frappe.get_single("Zprint Setting")
    except Exception:
        bootinfo["zhiz_print"] = {"print_designer": {"enabled": False}}
        return

    # Get license info (auto-creates trial if needed)
    license_info = {}
    try:
        from zhiz_print.api.license import get_license_info_for_boot
        license_info = get_license_info_for_boot()
    except Exception:
        pass

    bootinfo["zhiz_print"] = {
        "print_designer": _get_print_designer_boot_settings(setting),
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
                _dts = frappe.db.get_list("Super Print Design", filters={"enabled": 1},
                    fields=["target_doctype"], distinct=True, pluck="target_doctype")
                result["doctype_has_design"] = {dt: True for dt in _dts if dt}
            except Exception:
                result["doctype_has_design"] = {}

            # Export settings
            result["allow_export_pdf"] = bool(frappe.utils.cint(setting.get("allow_export_pdf")))
            result["allow_export_excel"] = bool(frappe.utils.cint(setting.get("allow_export_excel")))

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
