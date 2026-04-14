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
LICENSE_SERVER = "http://gdzhiz.com:51818"

# Product code registered on the license server
PRODUCT_CODE = "zhiz_print"

# API secret shared with license server (will be compiled into .so)
# This will be replaced with the actual secret after Zlic Product is created on server
API_SECRET = "1e4266377127627a8782327e4d7054bcf8862e6ebd94c3885d14decfcf4ef21d"

# Cache key for license validation result
LICENSE_CACHE_KEY = "zhiz_print:license_status"
LICENSE_CACHE_TTL = 86400  # 24 hours

# Maximum offline days before license is considered expired
MAX_OFFLINE_DAYS = 7

# Local license signing key (derived from API_SECRET, will be compiled into .so)
_LOCAL_SIGN_SECRET = hashlib.sha256((API_SECRET + ":local_license_sign:v1").encode()).hexdigest()


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


# ==================== Local License Integrity ====================

def _compute_license_hash(license_key, plan, status, expires_at, machine_id):
    """Compute HMAC hash of local license key fields."""
    payload = "{license_key}|{plan}|{status}|{expires_at}|{machine_id}".format(
        license_key=license_key or "",
        plan=plan or "",
        status=status or "",
        expires_at=str(expires_at) if expires_at else "",
        machine_id=machine_id or "",
    )
    return hmac.new(_LOCAL_SIGN_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _sign_local_license(doc):
    """Sign a local Zprint License document."""
    doc.license_hash = _compute_license_hash(
        doc.license_key, doc.plan, doc.status, doc.expires_at, doc.machine_id
    )


def _verify_local_license(lic):
    """Verify local license integrity. Returns True if valid."""
    if not lic.get("license_hash"):
        return False
    expected = _compute_license_hash(
        lic.get("license_key"), lic.get("plan"), lic.get("status"),
        lic.get("expires_at"), lic.get("machine_id"),
    )
    return hmac.compare_digest(expected, lic.get("license_hash", ""))


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
        # Frappe wraps whitelisted method response in {"message": ...}
        if "message" in data:
            data = data["message"]
        # If response has encrypted field, decrypt it
        if isinstance(data, dict) and "encrypted" in data and "iv" in data:
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
                "activated_at", "machine_id", "site_name", "last_validated_at",
                "license_hash"],
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

    # Step 2.1: Verify local license integrity
    if not _verify_local_license(lic):
        frappe.db.set_value("Zprint License", lic.name, "status", "Expired")
        result = {
            "valid": False,
            "status": "Tampered",
            "plan": None,
            "expired": True,
            "message": "License integrity check failed. Please reactivate.",
        }
        _cache_result(result)
        return (False, result)

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
            # Server explicitly rejected (e.g. locked/expired)
            error = remote_result.get("error", "")
            if "locked" in error.lower():
                result = {
                    "valid": False,
                    "status": "Locked",
                    "plan": lic.plan,
                    "expired": True,
                    "expires_at": str(lic.expires_at),
                    "message": "License has been locked. Please contact vendor.",
                }
                _cache_result(result)
                return (False, result)
            if "expired" in error.lower():
                _update_license_status(lic.name, "Expired")
                result = {
                    "valid": False,
                    "status": "Expired",
                    "plan": lic.plan,
                    "expired": True,
                    "expires_at": str(lic.expires_at),
                    "message": "License has been expired by the server.",
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
    """Update license status in database with hash recalculation."""
    try:
        lic = frappe.get_all(
            "Zprint License",
            filters={"name": license_name},
            fields=["name", "license_key", "plan", "status", "expires_at", "machine_id"],
            limit=1,
        )
        if not lic:
            return
        lic = lic[0]
        lic["status"] = status
        new_hash = _compute_license_hash(
            lic["license_key"], lic["plan"], status, lic["expires_at"], lic["machine_id"]
        )
        frappe.db.set_value("Zprint License", license_name, {
            "status": status,
            "license_hash": new_hash,
        })
    except Exception:
        pass


# ==================== Trial License ====================

def create_trial_license():
    """Request a trial license from the server (once per machine).

    The server enforces one trial per machine_id per product.
    If already issued, returns the existing license with remaining time.
    """
    existing = frappe.get_all("Zprint License", limit=1)
    if existing:
        return existing[0]

    machine_id = get_machine_id()
    site_name = frappe.local.site if hasattr(frappe.local, "site") else ""

    # Request trial from server
    try:
        result = _call_license_api("request_trial", {
            "product_code": PRODUCT_CODE,
            "machine_id": machine_id,
            "site_name": site_name,
        })
    except Exception as e:
        frappe.log_error(str(e), "Trial license request failed")
        return None

    if not result or not result.get("valid"):
        error = (result or {}).get("error", "Unknown error")
        frappe.log_error("Trial rejected: {0}".format(error), "Trial License Denied")
        return None

    # Server issued or reissued trial — create local record with server data
    license_key = result.get("license_key")
    expires_at = result.get("expires_at")
    server_status = result.get("status", "Active")
    now = frappe.utils.now_datetime()
    expires = frappe.utils.get_datetime(expires_at) if expires_at else frappe.utils.add_days(now, 30)

    # If server says expired, mark accordingly
    if server_status == "Expired" or (expires_at and frappe.utils.get_datetime(expires_at) < now):
        status = "Expired"
    else:
        status = "Active"

    doc = frappe.get_doc({
        "doctype": "Zprint License",
        "license_key": license_key,
        "plan": "Trial",
        "status": status,
        "activated_at": now,
        "expires_at": expires,
        "machine_id": machine_id,
        "site_name": site_name,
        "company_name": "Trial",
    })
    _sign_local_license(doc)
    doc.insert(ignore_permissions=True)
    frappe.db.commit()

    return doc


def _sync_trial_from_server(license_key, machine_id, site_name):
    """Sync an existing server-issued trial to local DB if not already present."""
    existing = frappe.get_all("Zprint License", filters={"license_key": license_key}, limit=1)
    if existing:
        return

    # Get details from server
    try:
        result = _call_license_api("get_license_info", {"license_key": license_key})
    except Exception:
        return

    if not result or not result.get("valid"):
        return

    now = frappe.utils.now_datetime()
    expires_at = result.get("expires_at")
    expires = frappe.utils.get_datetime(expires_at) if expires_at else frappe.utils.add_days(now, 30)
    server_status = result.get("status", "Expired")
    if server_status == "Expired" or (expires_at and frappe.utils.get_datetime(expires_at) < now):
        status = "Expired"
    else:
        status = "Active"

    doc = frappe.get_doc({
        "doctype": "Zprint License",
        "license_key": license_key,
        "plan": result.get("plan", "Trial"),
        "status": status,
        "activated_at": result.get("activated_at") or now,
        "expires_at": expires,
        "machine_id": machine_id,
        "site_name": site_name,
        "company_name": "Trial",
    })
    _sign_local_license(doc)
    doc.insert(ignore_permissions=True)
    frappe.db.commit()


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

    # Remote activation required — no local fallback
    try:
        result = _call_license_api("activate", {
            "license_key": license_key,
            "machine_id": machine_id,
            "site_name": frappe.local.site if hasattr(frappe.local, "site") else "",
        })
    except Exception as e:
        frappe.log_error(str(e), "License activation network error")
        frappe.throw("Unable to connect to license server. Please check your network connection.")

    if not result or not result.get("valid") or not result.get("activated"):
        error = (result or {}).get("error", "Activation failed")
        frappe.throw("Server rejected activation: {0}".format(error))

    remote_expires = result.get("expires_at")
    remote_plan = result.get("plan", "Standard")

    # Expire any existing trial license
    trials = frappe.get_all(
        "Zprint License",
        filters={"plan": "Trial", "status": "Active"},
    )
    for t in trials:
        _update_license_status(t.name, "Expired")

    # Create local license record with HMAC signature
    now = frappe.utils.now_datetime()
    expires = frappe.utils.get_datetime(remote_expires) if remote_expires else frappe.utils.add_days(now, 365)

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
    _sign_local_license(doc)
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
    """Get current license status for frontend display (real-time, bypasses cache)."""
    licenses = frappe.get_all(
        "Zprint License",
        fields=["name", "license_key", "plan", "status", "expires_at", "machine_id", "license_hash"],
        order_by="activated_at desc",
        limit=1,
    )

    if not licenses:
        return {
            "valid": False,
            "status": "No License",
            "machine_id": get_machine_id(),
            "message": "",
        }

    lic = licenses[0]

    # Verify integrity
    if not _verify_local_license(lic):
        return {
            "valid": False,
            "status": "Tampered",
            "machine_id": get_machine_id(),
            "message": "License integrity check failed.",
        }

    # Check locked from server (remote check)
    is_locked = False
    try:
        remote_result = _call_license_api("validate", {
            "license_key": lic.license_key,
            "machine_id": get_machine_id(),
            "site_name": frappe.local.site if hasattr(frappe.local, "site") else "",
        })
        if remote_result and not remote_result.get("valid"):
            error = remote_result.get("error", "")
            if "locked" in error.lower():
                is_locked = True
    except Exception:
        pass

    if is_locked:
        return {
            "valid": False,
            "status": "Locked",
            "plan": lic.plan,
            "expires_at": str(lic.expires_at) if lic.expires_at else "",
            "machine_id": lic.machine_id or get_machine_id(),
            "message": "License has been locked. Please contact vendor to unlock.",
        }

    # Check expiry
    now = frappe.utils.now_datetime()
    expires_at = frappe.utils.get_datetime(lic.expires_at) if lic.expires_at else now

    if now > expires_at:
        return {
            "valid": False,
            "status": "Expired",
            "plan": lic.plan,
            "expires_at": str(lic.expires_at),
            "machine_id": get_machine_id(),
            "message": "License expired.",
        }

    is_trial = (lic.plan == "Trial")
    return {
        "valid": True,
        "status": lic.status,
        "plan": lic.plan,
        "expired": False,
        "trial": is_trial,
        "expires_at": str(lic.expires_at),
        "machine_id": lic.machine_id or get_machine_id(),
    }


def _sync_license_from_server(lic):
    """Sync local license status from server. Used when local status is not Active."""
    if not lic.get("license_key"):
        return

    try:
        result = _call_license_api("validate", {
            "license_key": lic.get("license_key"),
            "machine_id": get_machine_id(),
            "site_name": frappe.local.site if hasattr(frappe.local, "site") else "",
        })
    except Exception:
        return

    if not result:
        return

    # Server says valid — restore to Active
    if result.get("valid"):
        new_status = "Active"
        new_expires = result.get("expires_at") or lic.get("expires_at")
        new_hash = _compute_license_hash(
            lic.get("license_key"), lic.get("plan"), new_status, new_expires, lic.get("machine_id")
        )
        update_data = {
            "status": new_status,
            "license_hash": new_hash,
            "last_validated_at": frappe.utils.now_datetime(),
        }
        if result.get("expires_at"):
            update_data["expires_at"] = result.get("expires_at")
        frappe.db.set_value("Zprint License", lic.get("name"), update_data)
        frappe.db.commit()
        frappe.cache().delete_value(LICENSE_CACHE_KEY)
    else:
        # Server says invalid — sync status
        error = result.get("error", "")
        new_status = lic.get("status")
        if "locked" in error.lower():
            # Keep local Active, server controls lock state
            return
        elif "expired" in error.lower():
            new_status = "Expired"

        if new_status != lic.get("status"):
            new_hash = _compute_license_hash(
                lic.get("license_key"), lic.get("plan"), new_status, lic.get("expires_at"), lic.get("machine_id")
            )
            frappe.db.set_value("Zprint License", lic.get("name"), {
                "status": new_status,
                "license_hash": new_hash,
            })
            frappe.db.commit()
            frappe.cache().delete_value(LICENSE_CACHE_KEY)


def get_license_info_for_boot():
    """Get license info to inject into boot settings."""
    licenses = frappe.get_all(
        "Zprint License",
        fields=["name", "license_key", "plan", "status", "expires_at", "machine_id"],
        order_by="activated_at desc",
        limit=1,
    )
    if not licenses:
        try:
            create_trial_license()
        except Exception:
            pass
    else:
        # Always sync status from server on boot cache refresh
        _sync_license_from_server(licenses[0])

    valid, info = check_license_valid()
    info["machine_id"] = get_machine_id()

    trial_licenses = frappe.get_all(
        "Zprint License",
        filters={"plan": "Trial", "status": "Active"},
        limit=1,
    )
    info["trial"] = bool(trial_licenses)

    return info
