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

# Global JS loading (list view batch print interceptor)
app_include_js = [
    f"/assets/zhiz_print/js/batch/intercept_bulk_print.js?v={app_version}",
    f"/assets/zhiz_print/js/batch/intercept_report_print.js?v={app_version}",
    f"/assets/zhiz_print/js/navbar_menu.js?v={app_version}",
]

# Print page JS override
page_js = {
    "print": "public/js/page/print.js",
    "batch-print": "public/js/page/batch_print.js",
}

# Desk (apps screen) tile. Frappe v16 renders the desktop grid only from apps
# declaring this hook (boot.py sets on_apps_screen=bool(hook)); v15 >= 15.39
# shows the same entry on the /apps switcher, older v15 ignores it inertly.
# Label is translated in translations/zh.csv; sequence_id sorts the tile
# ahead of the default (100) and the pinned Framework entry (1000). The
# /app/... route form works on both v15 (native) and v16 (aliased to /desk).
add_to_apps_screen = [
    {
        "name": app_name,
        "logo": "/assets/zhiz_print/images/print-template-platform.svg",
        "title": "Print Template Platform",
        "route": "/app/print-template-store",
        "sequence_id": 10,
    }
]

# Super Print Design list view — 模板商店入口按钮
doctype_list_js = {"Super Print Design": "public/js/super_print_design_list.js"}

# Boot Session Hook
# Inject zhiz_print settings into frappe.boot on page load, avoiding runtime API calls
boot_session = "zhiz_print.api.print_setting.get_boot_settings"

# After install - create preset papers and designs
after_install = "zhiz_print.zhiz_print.after_install.after_install"

# Scheduler (scan-print plan 7) - the FIRST cron hooks in this app, so a
# site upgrading to this version MUST restart the frappe-bench-workers
# group (web restart alone does not register new scheduler events).
#   every minute : receipt harvesting + stale-job/agent maintenance
#   daily 03:00  : retention purges
scheduler_events = {
    "cron": {
        "*/1 * * * *": [
            "zhiz_print.api.maintenance.expire_stale_jobs",
            "zhiz_print.api.maintenance.mark_offline_agents",
            "zhiz_print.zhiz_print.print_channels.ipp.harvest_ipp_states",
            "zhiz_print.zhiz_print.print_channels.cloud.harvest_vendor_receipts",
        ],
        "0 3 * * *": [
            "zhiz_print.api.maintenance.cleanup_pdf_debug_days",
            "zhiz_print.api.maintenance.purge_expired_jobs",
        ],
    }
}

# Pretty alias for the scanner web page (www/scan_print.html serves /scan_print)
website_redirect = [
    {"source": r"/scan-print", "target": r"/scan_print"}
]
