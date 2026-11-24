"""Commercial license verification and white-label branding configuration.

License key format:
    SB1.<base64url(json_payload)>.<base64url(hmac_sha256_signature)>

Signed using SIGNAL_BOT_LICENSE_SIGNING_KEY (or APP_SECRET_KEY fallback when
not explicitly separated). In non-production environments without a configured
license key, the platform runs in unrestricted development mode so local dev
and CI suites continue without friction.
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import json
import os
import time
from typing import Any, Dict, Optional

LICENSE_PREFIX = "SB1"
VALID_TIERS = ("starter", "pro", "desk", "enterprise", "development")


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _b64url_decode(encoded: str) -> bytes:
    pad = "=" * (-len(encoded) % 4)
    return base64.urlsafe_b64decode((encoded + pad).encode("utf-8"))


def _get_signing_key() -> str:
    return (
        os.getenv("SIGNAL_BOT_LICENSE_SIGNING_KEY", "").strip()
        or os.getenv("APP_SECRET_KEY", "").strip()
    )


def sign_license_key(
    *,
    customer: str,
    tier: str = "pro",
    max_users: int = 10,
    expires_at: Optional[int] = None,
    signing_key: Optional[str] = None,
) -> str:
    """Create a signed SB1 commercial license key."""
    key = (signing_key if signing_key is not None else _get_signing_key()).strip()
    if not key:
        raise ValueError("License signing key is required")
    clean_customer = customer.strip()
    if not clean_customer:
        raise ValueError("Customer name is required")
    clean_tier = tier.strip().lower()
    if clean_tier not in VALID_TIERS:
        raise ValueError(f"Unsupported license tier: {tier}")
    if not isinstance(max_users, int) or max_users <= 0:
        raise ValueError("max_users must be a positive integer")

    payload: Dict[str, Any] = {
        "customer": clean_customer,
        "tier": clean_tier,
        "max_users": max_users,
        "issued_at": int(time.time()),
    }
    if expires_at is not None:
        payload["expires_at"] = int(expires_at)

    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_b64 = _b64url_encode(payload_bytes)
    sig = hmac.new(key.encode("utf-8"), f"{LICENSE_PREFIX}.{payload_b64}".encode("utf-8"), hashlib.sha256).digest()
    return f"{LICENSE_PREFIX}.{payload_b64}.{_b64url_encode(sig)}"


def verify_license_key(
    license_key: Optional[str] = None,
    *,
    signing_key: Optional[str] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Verify a commercial license key and return normalized status metadata."""
    raw_key = (
        license_key
        if license_key is not None
        else os.getenv("SIGNAL_BOT_LICENSE_KEY", "")
    ).strip()
    is_prod = os.getenv("SIGNAL_BOT_ENV", "").strip().lower() == "production"

    if not raw_key:
        if is_prod:
            return {
                "valid": False,
                "mode": "unlicensed",
                "customer": None,
                "tier": "unlicensed",
                "maxUsers": 1,
                "expiresAt": None,
                "reason": "SIGNAL_BOT_LICENSE_KEY is not configured",
            }
        return {
            "valid": True,
            "mode": "development",
            "customer": "Development Workspace",
            "tier": "development",
            "maxUsers": None,
            "expiresAt": None,
            "reason": None,
        }

    secret = (signing_key if signing_key is not None else _get_signing_key()).strip()
    if not secret:
        return {
            "valid": False,
            "mode": "invalid",
            "customer": None,
            "tier": "invalid",
            "maxUsers": 1,
            "expiresAt": None,
            "reason": "License signing key is not configured",
        }

    parts = raw_key.split(".")
    if len(parts) != 3 or parts[0] != LICENSE_PREFIX:
        return {
            "valid": False,
            "mode": "invalid",
            "customer": None,
            "tier": "invalid",
            "maxUsers": 1,
            "expiresAt": None,
            "reason": "Malformed license key format",
        }

    _, payload_b64, sig_b64 = parts
    try:
        expected_sig = hmac.new(
            secret.encode("utf-8"),
            f"{LICENSE_PREFIX}.{payload_b64}".encode("utf-8"),
            hashlib.sha256,
        ).digest()
        actual_sig = _b64url_decode(sig_b64)
        if not hmac.compare_digest(expected_sig, actual_sig):
            return {
                "valid": False,
                "mode": "invalid",
                "customer": None,
                "tier": "invalid",
                "maxUsers": 1,
                "expiresAt": None,
                "reason": "License signature verification failed",
            }
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Invalid payload")
    except Exception:
        return {
            "valid": False,
            "mode": "invalid",
            "customer": None,
            "tier": "invalid",
            "maxUsers": 1,
            "expiresAt": None,
            "reason": "Unable to decode license payload",
        }

    customer = str(payload.get("customer") or "").strip() or None
    tier = str(payload.get("tier") or "").strip().lower()
    max_users = payload.get("max_users")
    expires_at = payload.get("expires_at")
    current_ts = time.time() if now is None else float(now)

    if tier not in VALID_TIERS or not isinstance(max_users, int) or max_users <= 0:
        return {
            "valid": False,
            "mode": "invalid",
            "customer": customer,
            "tier": tier or "invalid",
            "maxUsers": 1,
            "expiresAt": None,
            "reason": "License payload contains invalid tier or user limit",
        }

    expires_iso: Optional[str] = None
    if expires_at is not None:
        try:
            exp_ts = float(expires_at)
            expires_iso = datetime.datetime.fromtimestamp(exp_ts, tz=datetime.timezone.utc).isoformat()
            if exp_ts <= current_ts:
                return {
                    "valid": False,
                    "mode": "expired",
                    "customer": customer,
                    "tier": tier,
                    "maxUsers": max_users,
                    "expiresAt": expires_iso,
                    "reason": "License key has expired",
                }
        except (TypeError, ValueError, OSError):
            return {
                "valid": False,
                "mode": "invalid",
                "customer": customer,
                "tier": tier,
                "maxUsers": max_users,
                "expiresAt": None,
                "reason": "Invalid license expiration timestamp",
            }

    return {
        "valid": True,
        "mode": "licensed",
        "customer": customer,
        "tier": tier,
        "maxUsers": max_users,
        "expiresAt": expires_iso,
        "reason": None,
    }


def get_branding_config() -> Dict[str, Optional[str]]:
    """Return white-label branding configuration for the web UI."""
    brand_name = os.getenv("SIGNAL_BOT_BRAND_NAME", "").strip() or "Signal Bot"
    logo_url = os.getenv("SIGNAL_BOT_LOGO_URL", "").strip() or None
    if logo_url and not (logo_url.startswith("https://") or logo_url.startswith("http://") or logo_url.startswith("/")):
        logo_url = None
    return {
        "brandName": brand_name[:80],
        "logoUrl": logo_url,
    }
