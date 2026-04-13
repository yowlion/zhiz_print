# -*- coding: utf-8 -*-
# Copyright (c) 2026, Zhiz Print and contributors
# For license information, please see license.txt

from __future__ import unicode_literals

import frappe
import hashlib
import hmac
import json
import os
import time
import uuid

# ==================== Configuration ====================

# License server address (hardcoded, will be compiled into .so)
LICENSE_SERVER = "https://gdzhiz.com:51818"

# Product code registered on the license server
PRODUCT_CODE = "zhiz_print"

# API secret shared with license server (will be compiled into .so)
# This will be replaced with the actual secret after Zlic Product is created on server
API_SECRET = "zhiz_print_secret_placeholder"

# Cache key for license validation result
LICENSE_CACHE_KEY = "zhiz_print:license_status"
LICENSE_CACHE_TTL = 86400  # 24 hours

# Maximum offline days before license is considered expired
MAX_OFFLINE_DAYS = 7


# ==================== Machine ID ====================

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


# ==================== Local Cache Signing ====================

def sign_data(data):
    """Sign data with HMAC-SHA256 for local cache integrity."""
    secret = "zhiz_print_lic_2026_" + hashlib.md5(b"zhiz_print_salt").hexdigest()[:8]
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def verify_signature(data, signature):
    """Verify local HMAC signature."""
    expected = sign_data(data)
    return hmac.compare_digest(expected, signature)


# ==================== Encrypted Communication with License Server ====================

def _sign_request(product_code, timestamp, nonce, body_str, secret):
    """Sign request with HMAC-SHA256."""
    message = "{product_code}{timestamp}{nonce}{body}".format(
        product_code=product_code,
        timestamp=timestamp,
        nonce=nonce,
        body=body_str,
    )
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def _build_request(body_dict):
    """Build a signed request payload for the license server."""
    timestamp = str(int(time.time()))
    nonce = os.urandom(8).hex()
    body_str = json.dumps(body_dict, separators=(",", ":"), ensure_ascii=False)
    signature = _sign_request(PRODUCT_CODE, timestamp, nonce, body_str, API_SECRET)

    return {
        "product_code": PRODUCT_CODE,
        "timestamp": timestamp,
        "nonce": nonce,
        "signature": signature,
        "body": body_str,
    }


def _decrypt_response(encrypted_data):
    """Decrypt response from the license server."""
    from base64 import b64decode
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad

    ct_b64 = encrypted_data.get("encrypted", "")
    iv_b64 = encrypted_data.get("iv", "")
    signature = encrypted_data.get("signature", "")

    # Verify HMAC signature
    expected_sig = hmac.new(
        API_SECRET.encode(),
        (ct_b64 + iv_b64).encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_sig, signature):
        raise ValueError("Response signature verification failed")

    # Decrypt with AES-256-CBC
    key = hashlib.sha256(API_SECRET.encode()).digest()
    iv = b64decode(iv_b64)
    ciphertext = b64decode(ct_b64)
    cipher = AES.new(key, AES.MODE_CBC, iv)
    plaintext = unpad(cipher.decrypt(ciphertext), AES.block_size)

    return json.loads(plaintext.decode("utf-8"))


def _call_license_api(endpoint, body_dict):
    """Call the license server API with encrypted communication.

    Args:
        endpoint: API method name (e.g. "validate", "activate")
        body_dict: Request body dict

    Returns:
        Decrypted response dict, or None on failure

    Raises:
        Exception on network errors (for offline detection)
    """
    import requests

    request_payload = _build_request(body_dict)
    url = "{0}/api/method/zhiz_licser.api.license_api.{1}".format(LICENSE_SERVER, endpoint)

    response = requests.post(url, json=request_payload, timeout=10)

    if response.status_code == 200:
        data = response.json()
        # If response has encrypted field, decrypt it
        if "encrypted" in data and "iv" in data:
            return _decrypt_response(data)
        # Fallback: unencrypted response (backward compatible)
        return data

    return None


# ==================== Core Validation ====================

def check_license_valid():
    """Core license validation function.

    Returns:
        tuple: (is_valid: bool, license_info: dict)
    """
    # Step 1: Check cache (validates once per day)
    cached = frappe.cache().get_value(LICENSE_CACHE_KEY)
    if cached:
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
    last_validated = frappe.utils.get_datetime(lic.last_validated_at) if lic.last_validated_at else None
    offline_days = 0
    if last_validated:
        offline_days = (now - last_validated).days

    try:
        remote_result = _call_license_api("validate", {
            "license_key": lic.license_key,
            "machine_id": get_machine_id(),
            "site_name": frappe.local.site if hasattr(frappe.local, "site") else "",
        })

        if remote_result and remote_result.get("valid"):
            # Update last_validated_at
            frappe.db.set_value("Zprint License", lic.name, "last_validated_at", now)
            lic.last_validated_at = now
        elif remote_result and not remote_result.get("valid"):
            # Server explicitly rejected (e.g. revoked remotely)
            error = remote_result.get("error", "")
            if "revoked" in error.lower():
                _update_license_status(lic.name, "Revoked")
                result = {
                    "valid": False,
                    "status": "Revoked",
                    "plan": lic.plan,
                    "expired": True,
                    "expires_at": str(lic.expires_at),
                    "message": "License has been revoked by the server.",
                }
                _cache_result(result)
                return (False, result)
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


def _update_license_status(license_name, status):
    """Update license status in database."""
    try:
        frappe.db.set_value("Zprint License", license_name, "status", status)
    except Exception:
        pass


# ==================== Trial License ====================

def create_trial_license():
    """Create a trial license for new installations (30 days)."""
    existing = frappe.get_all("Zprint License", limit=1)
    if existing:
        return

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


# ==================== Activation ====================

@frappe.whitelist()
def activate_license(license_key):
    """Activate a license key on this server.

    Calls the license server to bind the machine, then creates a local record.
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

    if len(license_key) < 16:
        frappe.throw("Invalid license key format")

    # Try remote activation first
    remote_expires = None
    remote_plan = "Standard"
    try:
        result = _call_license_api("activate", {
            "license_key": license_key,
            "machine_id": machine_id,
            "site_name": frappe.local.site if hasattr(frappe.local, "site") else "",
        })
        if result and result.get("valid") and result.get("activated"):
            remote_expires = result.get("expires_at")
            remote_plan = result.get("plan", "Standard")
        elif result and not result.get("valid"):
            error = result.get("error", "Activation failed")
            frappe.throw("Server rejected activation: {0}".format(error))
    except Exception as e:
        # Network error — allow local-only activation as fallback
        frappe.log_error(str(e), "Remote activation failed, using local fallback")

    # Expire any existing trial license
    trials = frappe.get_all(
        "Zprint License",
        filters={"plan": "Trial", "status": "Active"},
    )
    for t in trials:
        frappe.db.set_value("Zprint License", t.name, "status", "Expired")

    # Create local license record
    now = frappe.utils.now_datetime()
    if remote_expires:
        expires = frappe.utils.get_datetime(remote_expires)
    else:
        expires = frappe.utils.add_days(now, 365)

    doc = frappe.get_doc({
        "doctype": "Zprint License",
        "license_key": license_key,
        "plan": remote_plan,
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


# ==================== Status Queries ====================

@frappe.whitelist()
def get_license_status():
    """Get current license status for frontend display."""
    valid, info = check_license_valid()
    info["machine_id"] = get_machine_id()
    return info


def get_license_info_for_boot():
    """Get license info to inject into boot settings."""
    licenses = frappe.get_all("Zprint License", limit=1)
    if not licenses:
        try:
            create_trial_license()
        except Exception:
            pass

    valid, info = check_license_valid()
    info["machine_id"] = get_machine_id()

    trial_licenses = frappe.get_all(
        "Zprint License",
        filters={"plan": "Trial", "status": "Active"},
        limit=1,
    )
    info["trial"] = bool(trial_licenses)

    return info
