import frappe


def execute():
    # Page 由 dummy_module 的 JSON 同步创建(docname=batch_print,下划线)。
    # 旧版曾在此手插连字符名 "batch-print" 的 Page——路由=docname 原文,那页永远打不开。
    # 老站此补丁路径已在 patch log 里,改文件不会重跑;新站跑到这里时同步已完成,直接返回。
    if frappe.db.exists("Page", "batch_print"):
        return
