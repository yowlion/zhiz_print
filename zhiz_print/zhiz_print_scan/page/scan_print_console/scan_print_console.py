# Scan Print Console (desk entry page for the scan-print kiosk)
# Shows feature switches, kiosk link/QR and an inline kiosk preview.

import base64
import io

import frappe
from frappe import _
from frappe.utils import cint


def _qr_data_uri(text):
    """Encode text as a PNG QR data-URI; None when no QR library exists."""
    buf = io.BytesIO()
    try:
        import qrcode

        qrcode.make(text).save(buf, format="PNG")
    except Exception:
        try:
            import pyqrcode

            pyqrcode.create(text).png(buf, scale=6)
        except Exception:
            return None
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


@frappe.whitelist()
def console_bootstrap(url=None):
    """Feature switches + kiosk URL (+QR) for the desk console."""
    if not frappe.session.user or frappe.session.user == "Guest":
        frappe.throw(_("Authentication failed"), frappe.AuthenticationError)
    from zhiz_print.api.license import has_silent_print_entitlement
    from zhiz_print.api.scan_print import _setting

    entitled, reason_code = has_silent_print_entitlement()
    url = (url or "").strip() or (frappe.utils.get_url() + "/scan_print")
    if len(url) > 512:
        frappe.throw(_("URL too long"))
    return {
        "scan_print_enabled": cint(_setting("scan_print_enabled", 0)),
        "entitled": entitled,
        "reason_code": reason_code,
        "kiosk_url": url,
        "kiosk_qr": _qr_data_uri(url),
    }
