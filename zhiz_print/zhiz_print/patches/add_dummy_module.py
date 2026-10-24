import frappe


def execute():
	"""侧边栏隐藏用的 Dummy Module 需有 Module Def,否则 Page.load_assets
	解析模块路径时抛 DoesNotExistError(未找到模块 Dummy Module)。
	python 包 zhiz_print/dummy_module 随码提供,这里只补 DB 行。"""
	if not frappe.db.exists("Module Def", "Dummy Module"):
		frappe.get_doc(
			{"doctype": "Module Def", "module_name": "Dummy Module", "app_name": "zhiz_print"}
		).db_insert()
		frappe.db.commit()
