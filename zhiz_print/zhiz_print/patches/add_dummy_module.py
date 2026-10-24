import frappe


def execute():
	"""侧边栏隐藏用的 Dummy Module 需有 Module Def 且 app_name 指向本 app,
	否则 Page.load_assets 的 get_module_path 解析到错误应用目录。
	注意 migrate 里文件同步先于 patches:Page 导入经 make_module_and_roles
	自动补建时 app_name 兜底成 "frappe",所以这里必须 upsert,不能只查 exists。"""
	if not frappe.db.exists("Module Def", "Dummy Module"):
		frappe.get_doc(
			{"doctype": "Module Def", "module_name": "Dummy Module", "app_name": "zhiz_print"}
		).db_insert()
		frappe.db.commit()
	elif frappe.db.get_value("Module Def", "Dummy Module", "app_name") != "zhiz_print":
		frappe.db.set_value("Module Def", "Dummy Module", "app_name", "zhiz_print")
		frappe.db.commit()
