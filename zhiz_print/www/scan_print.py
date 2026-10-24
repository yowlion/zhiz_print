# -*- coding: utf-8 -*-
# Copyright (c) 2026, Zhiz Print and contributors
# For license information, please see license.txt

# Context for the /scan_print kiosk page (scan-print plan 5). The page is
# station-scoped by the session user: the station is looked up through its
# bound_user (the reverse of the desk Link), so a PDA browser never holds
# the station API secret. Guests are bounced to login with a return path.

from __future__ import unicode_literals

from urllib.parse import quote

import frappe
from frappe import _


no_cache = 1
sitemap = 0


def get_context(context):
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.local.flags.redirect_location = "/login?redirect-to=" + quote("/scan_print")
        raise frappe.Redirect

    from zhiz_print.api.scan_print import _session_station

    station = _session_station()
    context.title = _("Scan Print")
    context.csrf_token = frappe.sessions.get_csrf_token()
    context.station_id = station.name if station else ""
    context.station_name = (station.station_name if station else "") \
        or (station.name if station else "")
    context.wedge_suffix = (station.wedge_suffix if station else "enter") or "enter"
    return context
