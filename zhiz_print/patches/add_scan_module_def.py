import frappe


def execute():
	"""扫码打印独立模块:确保 Module Def 存在(doctype JSON 的 module Link 依赖它)。"""
	if not frappe.db.exists("Module Def", "Zhiz Print Scan"):
		frappe.get_doc(
			{"doctype": "Module Def", "module_name": "Zhiz Print Scan", "app_name": "zhiz_print"}
		).db_insert()
		frappe.db.commit()
