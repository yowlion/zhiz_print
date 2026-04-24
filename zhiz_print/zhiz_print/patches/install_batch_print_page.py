import frappe


def execute():
    if frappe.db.exists("Page", "batch-print"):
        return

    # Temporarily enable developer mode to insert Page record
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
        page.insert(ignore_permissions=True)
    finally:
        frappe.conf.developer_mode = developer_mode
