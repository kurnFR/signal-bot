"""
Authentication & Cryptographic Security Module.
- PBKDF2-HMAC-SHA256 with 200,000 iterations & 16-byte random salt.
- Constant-time verification against timing attacks.
- 100% Parameterized queries to strictly prevent SQL injection.
- Token-based session management.
"""
import os
import time
import json
import base64
import hmac
import hashlib
import secrets
import logging
from typing import Optional, Dict, Any
from fastapi import Request, HTTPException, Security, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from db.db import get_pool

logger = logging.getLogger("web.auth")

# Security key initialization
SECRET_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".app_secret")
if os.path.exists(SECRET_FILE):
    with open(SECRET_FILE, "r") as f:
        SECRET_KEY = f.read().strip()
else:
    SECRET_KEY = secrets.token_hex(32)
    with open(SECRET_FILE, "w") as f:
        f.write(SECRET_KEY)

TOKEN_EXPIRY_SECONDS = 86400  # 24 hours
security_bearer = HTTPBearer(auto_error=False)


# ============================================================================
# Password Hashing & Verification (PBKDF2-HMAC-SHA256)
# ============================================================================
def hash_password(password: str) -> str:
    """Creates a hardened, salted PBKDF2-HMAC-SHA256 password hash."""
    if not password or len(password) < 6:
        raise ValueError("Password must be at least 6 characters long")
    salt = secrets.token_bytes(16)
    iterations = 200000
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2:sha256:{iterations}${salt.hex()}${derived.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    """Verifies a password against a stored hash using constant-time comparison."""
    if not password or not hashed:
        return False
    try:
        scheme, salt_hex, hash_hex = hashed.split("$")
        parts = scheme.split(":")
        iterations = int(parts[2])
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(derived, expected)
    except Exception as e:
        logger.error(f"Password verification error: {e}")
        return False


# ============================================================================
# Stateless Signed Session Token (HMAC-SHA256)
# ============================================================================
def create_access_token(user_id: int, username: str, role: str) -> str:
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "exp": int(time.time()) + TOKEN_EXPIRY_SECONDS,
        "iat": int(time.time()),
    }
    payload_bytes = json.dumps(payload, separators=(',', ':')).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode("utf-8").rstrip("=")
    signature = hmac.new(SECRET_KEY.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(signature).decode("utf-8").rstrip("=")
    return f"{payload_b64}.{sig_b64}"


def verify_access_token(token: str) -> Optional[Dict[str, Any]]:
    if not token or "." not in token:
        return None
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        # Pad base64
        pad_payload = payload_b64 + "=" * (-len(payload_b64) % 4)
        pad_sig = sig_b64 + "=" * (-len(sig_b64) % 4)
        
        expected_sig = hmac.new(SECRET_KEY.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).digest()
        actual_sig = base64.urlsafe_b64decode(pad_sig.encode("utf-8"))
        if not hmac.compare_digest(expected_sig, actual_sig):
            return None

        payload_bytes = base64.urlsafe_b64decode(pad_payload.encode("utf-8"))
        payload = json.loads(payload_bytes.decode("utf-8"))
        if payload.get("exp", 0) < time.time():
            return None  # Token expired
        return payload
    except Exception:
        return None


# ============================================================================
# FastAPI Dependencies & Security Hardening
# ============================================================================
def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_bearer)
) -> Dict[str, Any]:
    token = None
    # 1. Try Authorization header
    if credentials:
        token = credentials.credentials
    # 2. Try cookie
    if not token and "auth_token" in request.cookies:
        token = request.cookies.get("auth_token")

    if not token:
        raise HTTPException(status_code=401, detail="Authentication credentials missing")

    payload = verify_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Session expired or invalid token")

    # Verify user is still active in DB (strict SQL parameterized query)
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        # Hardened SQL Injection protection: %s tuple parameter only
        cur.execute(
            "SELECT id, username, email, role, is_active FROM users WHERE id = %s AND is_active = 1",
            (payload["sub"],)
        )
        user = cur.fetchone()
        cur.close()
        if not user:
            raise HTTPException(status_code=401, detail="User account disabled or deleted")
        return user
    finally:
        conn.close()


def require_admin(current_user: Dict[str, Any] = Depends(get_current_user)):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return current_user


# ============================================================================
# Default Admin Seeding
# ============================================================================
def seed_default_admin():
    """Initializes admin/admin123 if users table is empty."""
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT COUNT(*) as count FROM users")
        count = cur.fetchone()["count"]
        if count == 0:
            default_pwd = "admin123"
            pwd_hash = hash_password(default_pwd)
            # 100% Parameterized query: no SQL injection possible
            cur.execute(
                """
                INSERT INTO users (username, email, password_hash, role, is_active)
                VALUES (%s, %s, %s, %s, %s)
                """,
                ("admin", "admin@crypto-signal-bot.local", pwd_hash, "admin", 1)
            )
            conn.commit()
            logger.info("Initialized default admin credentials: admin / admin123")
            print("\n" + "=" * 60)
            print(" [SECURITY] Default admin account created:")
            print("   Username: admin")
            print("   Password: admin123")
            print("   Please change this password after initial login!")
            print("=" * 60 + "\n")
        cur.close()
    finally:
        conn.close()
