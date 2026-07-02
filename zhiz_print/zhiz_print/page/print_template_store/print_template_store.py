# -*- coding: utf-8 -*-
# Copyright (c) 2026, Zhiz Print
# License: MIT
"""智兆模板平台 Page 后端入口。业务逻辑在 zhiz_print/api/template_store.py。"""
from __future__ import unicode_literals
import frappe
from frappe import _


def get_context(context):
    context.title = _("模板平台")
    return context
