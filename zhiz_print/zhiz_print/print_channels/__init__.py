# -*- coding: utf-8 -*-
# Copyright (c) 2026, Zhiz Print and contributors
# For license information, please see license.txt

# Pluggable print-channel registry (scan-print plan 1 step 7).
# Every channel implements the same dispatch(job, route_ctx) contract:
#   A  print_channels.agent    - mark ready, wait for the agent to claim
#   B  print_channels.ipp      - async IPP Print-Job to a LAN printer
#   C  print_channels.cloud    - async submit to a cloud printer/box
# dispatch() MUST return quickly (the render worker calls it); any network
# work belongs in an frappe.enqueue task inside the channel implementation.

from __future__ import unicode_literals

import frappe

CHANNEL_A = "zhiz_print.zhiz_print.print_channels.agent"
CHANNEL_B = "zhiz_print.zhiz_print.print_channels.ipp"
CHANNEL_C = "zhiz_print.zhiz_print.print_channels.cloud"


def get_channel_impl(channel):
    """Resolve a channel letter to its implementation module."""
    paths = {"A": CHANNEL_A, "B": CHANNEL_B, "C": CHANNEL_C}
    path = paths.get(channel)
    if not path:
        frappe.throw("Unknown print channel: {0}".format(channel))
    return frappe.get_module(path)


def dispatch_to_channel(job, route_ctx):
    """Trunk fork: hand a rendered, artifact-backed job to its channel.

    job: Zprint Print Job db row (frappe._dict). route_ctx: trunk routing
    envelope {channel, printer, fallback_channel, fallback_printer, station,
    rule}. Raises on synchronous misconfiguration (no printer, bad channel);
    async channels convert remote failures into job fallback/failed.
    """
    channel = (route_ctx or {}).get("channel")
    impl = get_channel_impl(channel)
    if not hasattr(impl, "dispatch"):
        frappe.throw("Channel {0} implementation has no dispatch()".format(channel))
    impl.dispatch(job, route_ctx)


def update_job(job_name, updates, commit=False):
    """Shared status-write helper for channel implementations."""
    frappe.db.set_value("Zprint Print Job", job_name, updates, update_modified=True)
    if commit:
        frappe.db.commit()
