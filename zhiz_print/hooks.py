# -*- coding: utf-8 -*-
# Copyright (c) 2026, Zhiz Print and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import zhiz_print

app_name = "zhiz_print"
app_title = "Zhiz Print - Advanced Print Designer"
app_publisher = "Zhiz"
app_description = "自定义打印模板设计器、打印页面增强、PDF/Excel导出"
app_icon = "octicon octicon-print"
app_color = "#4CD964"
app_email = "hyowlion@gmail.com"
app_license = "MIT"


# JS 文件版本号参数（破坏浏览器缓存）
app_version = zhiz_print.__version__

# 全局 CSS 加载
app_include_css = [
    f"/assets/zhiz_print/css/print_designer.css?v={app_version}",
]

# 打印页面 JS 覆盖
page_js = {
    "print": "public/js/page/print.js"
}

# Boot Session Hook
# 在页面加载时注入 zhiz_print 设置到 frappe.boot，避免 JS 运行时 API 调用
boot_session = "zhiz_print.api.print_setting.get_boot_settings"
