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

    bootinfo["zhiz_print"] = {
        "print_designer": _get_print_designer_boot_settings(setting),
    }


@frappe.whitelist()
def refresh_boot_cache():
    """Clear cache and rebuild boot settings for current session"""
    frappe.only_for("System Manager")
    frappe.clear_cache()
    setting = frappe.get_single("Zprint Setting")
    return _get_print_designer_boot_settings(setting)


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

            # Export settings
            result["allow_export_pdf"] = bool(frappe.utils.cint(setting.get("allow_export_pdf")))
            result["allow_export_excel"] = bool(frappe.utils.cint(setting.get("allow_export_excel")))

        return result
    except Exception:
        return {"enabled": False}
