# -*- coding: utf-8 -*-
# Copyright (c) 2026, Zhiz Print and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document


class ZprintSetting(Document):
    pass


@frappe.whitelist()
def get_contact_qr():
    from zhiz_print.zhiz_print.doctype.zprint_setting.contact_qr_data import WECHAT_QR, WHATSAPP_QR, WECHAT_WORK_QR, GROUP_QR
    return {"wechat_qr": WECHAT_QR, "wechat_work_qr": WECHAT_WORK_QR, "whatsapp_qr": WHATSAPP_QR, "group_qr": GROUP_QR}
