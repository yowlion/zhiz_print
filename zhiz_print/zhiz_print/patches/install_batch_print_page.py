import frappe


def execute():
    if not frappe.db.exists("Page", "batch-print"):
        page = frappe.get_doc({
            "doctype": "Page",
            "page_name": "batch-print",
            "title": "Batch Print",
            "icon": "fa fa-print",
            "module": "Zhiz Print",
            "standard": "Yes",
            "system_page": 1,
        })
        page.insert(ignore_permissions=True)
