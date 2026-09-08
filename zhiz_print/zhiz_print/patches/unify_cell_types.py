# -*- coding: utf-8 -*-
# Copyright (c) 2026, Guangde Zhizhao Technology Co., Ltd.
# For license information, please see LICENSE
"""v15.22.42 存量单元格类型归一:data_query → static;logic → static + 值前置 '='。

- 渲染端本就与 cell_type 无关(占位符/表达式按值内容解析),改写后输出不变
- 渲染端 logic 遗留通道保留,patch 幂等可重入
- 执行前对 tabSuper Print Design Item 做列克隆快照表(仅首次)
"""

import frappe


def execute():
    # 快照(仅首次):列克隆,便于回退
    snap = "tabSuper Print Design Item_bak_20260908"
    if not frappe.db.sql("SHOW TABLES LIKE %s", (snap,)):
        frappe.db.sql_ddl(
            "CREATE TABLE `{0}` AS SELECT * FROM `tabSuper Print Design Item`".format(snap))

    # ① data_query → static(值不变)
    frappe.db.sql("""
        UPDATE `tabSuper Print Design Item`
        SET cell_type = 'static'
        WHERE cell_type = 'data_query'
    """)
    dq = frappe.db.sql("SELECT COUNT(name) FROM `tabSuper Print Design Item` WHERE cell_type='data_query'")[0][0]

    # ② logic → static + 值前置 '='(已 = 开头的不重复加)
    frappe.db.sql("""
        UPDATE `tabSuper Print Design Item`
        SET cell_value = CONCAT('=', TRIM(cell_value)), cell_type = 'static'
        WHERE cell_type = 'logic'
          AND IFNULL(cell_value, '') != ''
          AND TRIM(cell_value) NOT LIKE '=%'
    """)
    frappe.db.sql("""
        UPDATE `tabSuper Print Design Item`
        SET cell_type = 'static'
        WHERE cell_type = 'logic'
    """)
    lg = frappe.db.sql("SELECT COUNT(name) FROM `tabSuper Print Design Item` WHERE cell_type='logic'")[0][0]

    frappe.db.commit()
    print("[unify_cell_types] done. residual data_query={0}, logic={1}, snapshot={2}".format(dq, lg, snap))
