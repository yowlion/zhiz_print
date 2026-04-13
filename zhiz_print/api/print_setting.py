# -*- coding: utf-8 -*-
# Copyright (c) 2026, Zhiz Print and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe


def get_boot_settings(bootinfo):
    """boot_session hook: 注入 zhiz_print 设置到 frappe.boot.zhiz_print"""
    try:
        setting = frappe.get_single("Zprint Setting")
    except Exception:
        bootinfo["zhiz_print"] = {"print_designer": {"enabled": False}}
        return

    bootinfo["zhiz_print"] = {
        "print_designer": _get_print_designer_boot_settings(setting),
    }


def _get_print_designer_boot_settings(setting):
    """获取打印设计器boot配置"""
    try:
        enabled = bool(frappe.utils.cint(setting.get("enable_super_print_page")))
        result = {"enabled": enabled}

        if enabled:
            # 预加载纸张数据（含边距）
            papers = frappe.get_all(
                "Super Print Paper",
                filters={"enabled": 1},
                fields=["name", "width", "height",
                        "margin_top", "margin_bottom", "margin_left", "margin_right"],
            )
            result["papers"] = {p["name"]: p for p in papers}

            # 启用方式和指定单据列表
            mode = setting.get("print_enable_mode") or "全部单据启用"
            result["enable_mode"] = mode
            if mode == "指定单据启用":
                doctypes = []
                for item in setting.get("print_enabled_doctypes", []):
                    if item.enabled and item.doctype_name:
                        doctypes.append(item.doctype_name)
                result["enabled_doctypes"] = doctypes

        return result
    except Exception:
        return {"enabled": False}
