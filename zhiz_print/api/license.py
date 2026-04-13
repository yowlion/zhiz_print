# -*- coding: utf-8 -*-
# Copyright (c) 2026, Zhiz Print and contributors
# For license information, please see license.txt

from __future__ import unicode_literals

import frappe
import hashlib
import hmac
import json
import time
import uuid

# Cache key for license validation result
LICENSE_CACHE_KEY = "zhiz_print:license_status"
LICENSE_CACHE_TTL = 86400  # 24 hours

# Maximum offline days before license is considered expired
MAX_OFFLINE_DAYS = 7


def get_machine_id():
    """Generate a unique machine fingerprint based on hardware info."""
    parts = []

    # Try motherboard UUID
    try:
        with open("/sys/class/dmi/id/board_uuid", "r") as f:
            parts.append(f.read().strip())
    except Exception:
        pass

    # MAC address
    parts.append(hex(uuid.getnode()))

    # Hostname
    import socket
    parts.append(socket.gethostname())

    return hashlib.sha256(":".join(parts).encode()).hexdigest()[:32]


def sign_data(data):
    """Sign data with HMAC-SHA256. Secret is embedded in compiled .so."""
    secret = "zhiz_print_lic_2026_" + hashlib.md5(b"zhiz_print_salt").hexdigest()[:8]
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def verify_signature(data, signature):
    """Verify HMAC signature."""
    expected = sign_data(data)
    return hmac.compare_digest(expected, signature)


def check_license_valid():
    """Core license validation function.

    Returns:
        tuple: (is_valid: bool, license_info: dict)

    License info contains: status, plan, expires_at, expired, message
    """
    # Step 1: Check cache (validates once per day)
    cached = frappe.cache().get_value(LICENSE_CACHE_KEY)
    if cached:
        # Verify cache signature integrity
        if verify_signature(cached.get("data", {}), cached.get("signature", "")):
            data = cached["data"]
            return (data.get("valid", False), data)

    # Step 2: Load license from database
    licenses = frappe.get_all(
        "Zprint License",
        filters={"status": "Active"},
        fields=["name", "license_key", "plan", "status", "expires_at",
                "activated_at", "machine_id", "site_name", "last_validated_at"],
        order_by="activated_at desc",
        limit=1,
    )

    if not licenses:
        # No license at all — will be handled by boot_settings creating trial
        result = {
            "valid": False,
            "status": "No License",
            "plan": None,
            "expired": True,
            "message": "No license found. Please activate a license key.",
        }
        _cache_result(result)
        return (False, result)

    lic = licenses[0]

    # Step 3: Local validation — check expiry
    now = frappe.utils.now_datetime()
    expires_at = frappe.utils.get_datetime(lic.expires_at) if lic.expires_at else now

    if now > expires_at:
        # License expired
        _update_license_status(lic.name, "Expired")
        result = {
            "valid": False,
            "status": "Expired",
            "plan": lic.plan,
            "expired": True,
            "expires_at": str(lic.expires_at),
            "message": "License expired on {0}. Please renew your subscription.".format(
                frappe.utils.format_datetime(lic.expires_at)
            ),
        }
        _cache_result(result)
        return (False, result)

    # Step 4: Remote validation attempt
    remote_ok = False
    last_validated = frappe.utils.get_datetime(lic.last_validated_at) if lic.last_validated_at else None
    offline_days = 0
    if last_validated:
        offline_days = (now - last_validated).days

    try:
        remote_ok = _remote_validate(lic)
        if remote_ok:
            # Update last_validated_at
            frappe.db.set_value("Zprint License", lic.name, "last_validated_at", now)
            lic.last_validated_at = now
    except Exception:
        # Network error — allow offline usage up to MAX_OFFLINE_DAYS
        if offline_days > MAX_OFFLINE_DAYS:
            result = {
                "valid": False,
                "status": "Offline Expired",
                "plan": lic.plan,
                "expired": True,
                "expires_at": str(lic.expires_at),
                "message": "Unable to verify license for {0} days. Please connect to internet.".format(
                    offline_days
                ),
            }
            _cache_result(result)
            return (False, result)

    # Step 5: All checks passed
    result = {
        "valid": True,
        "status": lic.status,
        "plan": lic.plan,
        "expired": False,
        "expires_at": str(lic.expires_at),
        "message": "",
    }
    _cache_result(result)
    return (True, result)


def _cache_result(result):
    """Cache license validation result with signature."""
    cached = {
        "data": result,
        "signature": sign_data(result),
    }
    frappe.cache().set_value(LICENSE_CACHE_KEY, cached, expires_in_sec=LICENSE_CACHE_TTL)


def _remote_validate(license_data):
    """Remote validation against license server.

    Returns True if validation succeeds, False otherwise.
    Raises Exception on network errors.
    """
    import requests

    # License server URL — will be configured later
    license_server = frappe.get_conf().get("zhiz_print_license_server") or "https://license.zhiz.com"

    machine_id = get_machine_id()
    payload = {
        "license_key": license_data.license_key,
        "machine_id": machine_id,
        "site_name": frappe.local.site if hasattr(frappe.local, "site") else "",
    }

    response = requests.post(
        "{0}/api/method/validate".format(license_server),
        json=payload,
        timeout=10,
    )

    if response.status_code == 200:
        data = response.json()
        return data.get("valid", False)

    return False


def _update_license_status(license_name, status):
    """Update license status in database."""
    try:
        frappe.db.set_value("Zprint License", license_name, "status", status)
    except Exception:
        pass


def create_trial_license():
    """Create a trial license for new installations (30 days)."""
    # Check if any license already exists
    existing = frappe.get_all("Zprint License", limit=1)
    if existing:
        return

    # Generate a trial license key
    trial_key = "TRIAL-" + hashlib.sha256(
        (get_machine_id() + str(time.time())).encode()
    ).hexdigest()[:24].upper()

    now = frappe.utils.now_datetime()
    expires = frappe.utils.add_days(now, 30)

    doc = frappe.get_doc({
        "doctype": "Zprint License",
        "license_key": trial_key,
        "plan": "Trial",
        "status": "Active",
        "activated_at": now,
        "expires_at": expires,
        "machine_id": get_machine_id(),
        "site_name": frappe.local.site if hasattr(frappe.local, "site") else "",
        "company_name": "Trial",
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()

    return doc


@frappe.whitelist()
def activate_license(license_key):
    """Activate a license key on this server.

    Args:
        license_key: The license key provided by the vendor
    """
    frappe.only_for("System Manager")

    if not license_key:
        frappe.throw("License key is required")

    license_key = license_key.strip()
    machine_id = get_machine_id()

    # Check if this license is already activated on this machine
    existing = frappe.get_all(
        "Zprint License",
        filters={"license_key": license_key, "machine_id": machine_id, "status": "Active"},
        limit=1,
    )
    if existing:
        return {"success": True, "message": "License is already active on this machine"}

    # Validate license key format (basic check)
    if len(license_key) < 16:
        frappe.throw("Invalid license key format")

    # Expire any existing trial license
    trials = frappe.get_all(
        "Zprint License",
        filters={"plan": "Trial", "status": "Active"},
    )
    for t in trials:
        frappe.db.set_value("Zprint License", t.name, "status", "Expired")

    # Create new license record
    now = frappe.utils.now_datetime()
    expires = frappe.utils.add_days(now, 365)  # 1 year from activation

    doc = frappe.get_doc({
        "doctype": "Zprint License",
        "license_key": license_key,
        "plan": "Standard",
        "status": "Active",
        "activated_at": now,
        "expires_at": expires,
        "machine_id": machine_id,
        "site_name": frappe.local.site if hasattr(frappe.local, "site") else "",
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()

    # Clear cached license status
    frappe.cache().delete_value(LICENSE_CACHE_KEY)

    return {
        "success": True,
        "message": "License activated successfully. Expires on {0}".format(
            frappe.utils.format_date(expires)
        ),
        "expires_at": str(expires),
    }


@frappe.whitelist()
def get_license_status():
    """Get current license status for frontend display."""
    valid, info = check_license_valid()
    info["machine_id"] = get_machine_id()
    return info


def get_license_info_for_boot():
    """Get license info to inject into boot settings.

    Returns dict with: valid, expired, plan, expires_at, message, trial
    """
    # Ensure trial license exists
    licenses = frappe.get_all("Zprint License", limit=1)
    if not licenses:
        try:
            create_trial_license()
        except Exception:
            pass

    valid, info = check_license_valid()

    # Add machine_id for frontend display
    info["machine_id"] = get_machine_id()

    # Determine if this is a trial
    trial_licenses = frappe.get_all(
        "Zprint License",
        filters={"plan": "Trial", "status": "Active"},
        limit=1,
    )
    info["trial"] = bool(trial_licenses)

    return info
