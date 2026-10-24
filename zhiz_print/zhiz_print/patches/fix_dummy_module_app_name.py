import frappe


def execute():
	"""add_dummy_module 的 exists-noop 版已随 v15.23.27 记入 patch log(同路径不再重跑),
	而文件同步经 make_module_and_roles 自动补建的 Module Def app_name 兜底成 "frappe",
	batch_print 页 get_module_path 因此解析到 frappe 目录仍 404。此处按新补丁名纠正指向。"""
	if frappe.db.exists("Module Def", "Dummy Module"):
		if frappe.db.get_value("Module Def", "Dummy Module", "app_name") != "zhiz_print":
			frappe.db.set_value("Module Def", "Dummy Module", "app_name", "zhiz_print")
			frappe.db.commit()
	else:
		frappe.get_doc(
			{"doctype": "Module Def", "module_name": "Dummy Module", "app_name": "zhiz_print"}
		).db_insert()
		frappe.db.commit()
