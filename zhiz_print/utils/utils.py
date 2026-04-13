import frappe
import json


@frappe.whitelist()
def get_print_format(doc):
    from frappe.utils.safe_exec import get_safe_globals

    if not frappe.db.has_column('Print Format', 'condition_for_default'):
        return

    try:
        if isinstance(doc, str):
            doc = json.loads(doc)
    except:
        return

    doc = frappe.get_doc(doc)
    print_format_list = frappe.get_all('Print Format',
                                       filters={'doc_type': doc.doctype,
                                                'disabled': 0},
                                       fields=[
                                           'name', 'condition_for_default'],
                                       order_by='priority', as_list=1)

    for (print_format, condition) in print_format_list:
        if condition and frappe.safe_eval(condition, get_safe_globals(), dict(doc=doc, get_roles=frappe.get_roles)):
            return print_format
