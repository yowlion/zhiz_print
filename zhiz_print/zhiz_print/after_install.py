# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import json
import os
import frappe


def after_install():
    _install_presets()
    _install_batch_print_page()


def _install_presets():
    preset_path = os.path.join(os.path.dirname(__file__), "presets.json")
    if not os.path.exists(preset_path):
        return

    with open(preset_path, "r", encoding="utf-8") as f:
        presets = json.load(f)

    paper_data = presets.get("paper")
    if paper_data and not frappe.db.exists("Super Print Paper", paper_data["paper_name"]):
        paper = frappe.get_doc(paper_data)
        paper.insert(ignore_permissions=True, ignore_mandatory=True)
        frappe.db.commit()
        frappe.clear_cache(doctype="Super Print Paper")

    design_data = presets.get("design")
    if design_data and not frappe.db.exists("Super Print Design", design_data["design_name"]):
        design_data_copy = dict(design_data)
        design_items = presets.get("design_items", [])
        design_data_copy["design_items"] = []
        for item in design_items:
            clean = {k: v for k, v in item.items() if k != "doctype"}
            design_data_copy["design_items"].append(clean)

        frappe.flags.skip_zhiz_print_license = True
        try:
            design = frappe.get_doc(design_data_copy)
            design.insert(ignore_permissions=True, ignore_mandatory=True)
            frappe.db.commit()
            frappe.clear_cache(doctype="Super Print Design")
        finally:
            frappe.flags.skip_zhiz_print_license = False


def _install_batch_print_page():
    if frappe.db.exists("Page", "batch-print"):
        return

    developer_mode = frappe.conf.get("developer_mode")
    frappe.conf.developer_mode = 1
    try:
        page = frappe.get_doc({
            "doctype": "Page",
            "page_name": "batch-print",
            "title": "Batch Print",
            "icon": "fa fa-print",
            "module": "Zhiz Print",
            "standard": "Yes",
            "system_page": 1,
        })
        page.insert(ignore_permissions=True, ignore_mandatory=True)
        frappe.db.commit()
    finally:
        frappe.conf.developer_mode = developer_mode
