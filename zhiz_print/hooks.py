# -*- coding: utf-8 -*-
# Copyright (c) 2026, Zhiz Print and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import zhiz_print

app_name = "zhiz_print"
app_title = "Zhiz Print - Advanced Print Designer"
app_publisher = "Zhiz"
app_description = "Custom print template designer, enhanced print page, PDF/Excel export"
app_icon = "octicon octicon-print"
app_color = "#4CD964"
app_email = "hyowlion@gmail.com"
app_license = "MIT"


# JS file version parameter (bust browser cache)
app_version = zhiz_print.__version__

# Global CSS loading
app_include_css = [
    f"/assets/zhiz_print/css/print_designer.css?v={app_version}",
]

# Print page JS override
page_js = {
    "print": "public/js/page/print.js"
}

# Boot Session Hook
# Inject zhiz_print settings into frappe.boot on page load, avoiding runtime API calls
boot_session = "zhiz_print.api.print_setting.get_boot_settings"
