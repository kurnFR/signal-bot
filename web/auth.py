"""
Authentication & Cryptographic Security Module.
- PBKDF2-HMAC-SHA256 with 200,000 iterations & 16-byte random salt.
- Constant-time verification against timing attacks.
- Parameterized SQL queries to prevent SQL injection.
- Token-based session management.
- Central request authentication helper for API authorization middleware.
"""
import os
import time
import json
import base64
import hmac
import hashlib
import secrets
import tempfile
import logging
from typing import Optional, Dict, Any
from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from db.db import get_pool

logger = logging.getLogger("web.auth")

SECRET_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".app_secret")


def _load_or_create_secret(path: str) -> str:
    """Load the token-signing key, ensuring it is private and safe under concurrent startup."""
    try:
        os.chmod(path, 0o600)
    except FileNotFoundError:
        pass
    else:
        with open(path, "r", encoding="utf-8") as secret_file:
            existing = secret_file.read().strip()
        if existing:
            return existing

    directory = os.path.dirname(path) or "."
    secret = secrets.token_hex(32)
    fd, temp_path = tempfile.mkstemp(prefix=".app_secret.", dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as secret_file:
            fd = -1
            secret_file.write(secret)
            secret_file.flush()
            os.fsync(secret_file.fileno())

        # Linking a fully written temporary file avoids exposing an empty/partial key
        # if multiple application workers start at the same time. The first link wins.
        try:
            os.link(temp_path, path)
            return secret
        except FileExistsError:
            os.chmod(path, 0o600)
            with open(path, "r", encoding="utf-8") as secret_file:
                existing = secret_file.read().strip()
            if not existing:
                raise RuntimeError("Application secret file exists but is empty")
            return existing
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass


SECRET_KEY = _load_or_create_secret(SECRET_FILE)

TOKEN_EXPIRY_SECONDS = 86400
security_bearer = HTTPBearer(auto_error=False)


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


def create_access_token(user_id: int, username: str, role: str) -> str:
    now = int(time.time())
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "exp": now + TOKEN_EXPIRY_SECONDS,
        "iat": now,
    }
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode("utf-8").rstrip("=")
    signature = hmac.new(SECRET_KEY.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(signature).decode("utf-8").rstrip("=")
    return f"{payload_b64}.{sig_b64}"


def verify_access_token(token: str) -> Optional[Dict[str, Any]]:
    if not token or "." not in token:
        return None
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        pad_payload = payload_b64 + "=" * (-len(payload_b64) % 4)
        pad_sig = sig_b64 + "=" * (-len(sig_b64) % 4)

        expected_sig = hmac.new(SECRET_KEY.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).digest()
        actual_sig = base64.urlsafe_b64decode(pad_sig.encode("utf-8"))
        if not hmac.compare_digest(expected_sig, actual_sig):
            return None

        payload_bytes = base64.urlsafe_b64decode(pad_payload.encode("utf-8"))
        payload = json.loads(payload_bytes.decode("utf-8"))
        if payload.get("exp", 0) < time.time():
            return None
        if is_token_revoked(token):
            return None
        return payload
    except Exception:
        return None


# ============================================================================
# Token Revocation / Blacklisting (Server-side Session Revocation)
# ============================================================================
_revoked_tokens_cache = set()


def revoke_token(token: str) -> None:
    """Revokes an active token and blacklists it across the server."""
    if not token:
        return
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    _revoked_tokens_cache.add(token_hash)
    payload = verify_access_token(token)
    exp = payload.get("exp", int(time.time()) + TOKEN_EXPIRY_SECONDS) if payload else int(time.time()) + TOKEN_EXPIRY_SECONDS
    exp_dt = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(exp))

    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO revoked_tokens (token_hash, expires_at)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE expires_at = VALUES(expires_at)
            """,
            (token_hash, exp_dt),
        )
        conn.commit()
        cur.close()
        logger.info("Token %s... revoked and blacklisted", token_hash[:8])
    except Exception as exc:
        logger.error("Failed to persist revoked token: %s", exc)
    finally:
        conn.close()


def is_token_revoked(token: str) -> bool:
    """Check if token was revoked via database lookup and in-memory cache."""
    if not token:
        return True
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if token_hash in _revoked_tokens_cache:
        return True

    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT token_hash FROM revoked_tokens WHERE token_hash = %s AND expires_at > UTC_TIMESTAMP()",
            (token_hash,),
        )
        row = cur.fetchone()
        cur.close()
        if row:
            _revoked_tokens_cache.add(token_hash)
            return True
        return False
    except Exception as exc:
        logger.error("Error checking token revocation: %s", exc)
        return False
    finally:
        conn.close()


# ============================================================================
# Login Rate Limiting & Lockout
# ============================================================================
_login_attempts: Dict[str, list[float]] = {}
RATE_LIMIT_MAX_ATTEMPTS = 5
RATE_LIMIT_WINDOW_SECONDS = 300  # 5 minutes

# Durable backend (table `login_attempts`, migration 013). It is shared by every web
# worker and survives restarts. Enabled by the app at startup; unit tests and tools
# that never call enable_durable_rate_limit() keep the in-memory behaviour.
_durable_rate_limit = False


def enable_durable_rate_limit() -> bool:
    """Switch the limiter to the database if the table exists. Returns True if enabled."""
    global _durable_rate_limit
    try:
        conn = get_pool().get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM login_attempts LIMIT 1")
            cur.fetchall()
            cur.close()
        finally:
            conn.close()
        _durable_rate_limit = True
    except Exception as exc:
        _durable_rate_limit = False
        logger.warning("Durable login rate limiting unavailable, using in-memory limiter: %s", exc)
    return _durable_rate_limit


def _db_count_attempts(key: str, now: float) -> int:
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM login_attempts WHERE attempted_at < %s", (now - RATE_LIMIT_WINDOW_SECONDS,))
        cur.execute(
            "SELECT COUNT(*) FROM login_attempts WHERE rate_key = %s AND attempted_at >= %s",
            (key, now - RATE_LIMIT_WINDOW_SECONDS),
        )
        count = int(cur.fetchone()[0])
        conn.commit()
        cur.close()
        return count
    finally:
        conn.close()


def check_login_rate_limit(key: str) -> bool:
    """Returns True if within rate limit, False if locked out."""
    now = time.time()
    if _durable_rate_limit:
        try:
            return _db_count_attempts(key, now) < RATE_LIMIT_MAX_ATTEMPTS
        except Exception as exc:
            logger.warning("Login rate-limit store error, falling back to memory: %s", exc)
    attempts = [t for t in _login_attempts.get(key, []) if now - t < RATE_LIMIT_WINDOW_SECONDS]
    _login_attempts[key] = attempts
    return len(attempts) < RATE_LIMIT_MAX_ATTEMPTS


def record_failed_login(key: str) -> int:
    """Records a failed login attempt and returns the current count within the window."""
    now = time.time()
    if _durable_rate_limit:
        try:
            conn = get_pool().get_connection()
            try:
                cur = conn.cursor()
                cur.execute("INSERT INTO login_attempts (rate_key, attempted_at) VALUES (%s, %s)", (key, now))
                conn.commit()
                cur.close()
            finally:
                conn.close()
            return _db_count_attempts(key, now)
        except Exception as exc:
            logger.warning("Login rate-limit store error, falling back to memory: %s", exc)
    attempts = [t for t in _login_attempts.get(key, []) if now - t < RATE_LIMIT_WINDOW_SECONDS]
    attempts.append(now)
    _login_attempts[key] = attempts
    return len(attempts)


def reset_login_attempts(key: str) -> None:
    _login_attempts.pop(key, None)
    if _durable_rate_limit:
        try:
            conn = get_pool().get_connection()
            try:
                cur = conn.cursor()
                cur.execute("DELETE FROM login_attempts WHERE rate_key = %s", (key,))
                conn.commit()
                cur.close()
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("Login rate-limit reset error: %s", exc)


# ============================================================================
# Audit Logging (Immutable System Action History)
# ============================================================================
def log_audit(
    action: str,
    username: str,
    user_id: Optional[int] = None,
    details: Optional[Dict[str, Any]] = None,
    ip_address: Optional[str] = None,
) -> None:
    """Records an administrative, security, or trading action in audit_logs."""
    try:
        conn = get_pool().get_connection()
        try:
            cur = conn.cursor()
            details_json = json.dumps(details) if details else None
            cur.execute(
                """
                INSERT INTO audit_logs (user_id, username, action, details, ip_address)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (user_id, username, action, details_json, ip_address),
            )
            conn.commit()
            cur.close()
        finally:
            conn.close()
    except Exception as exc:
        logger.error("Failed to write audit log (%s by %s): %s", action, username, exc)


def _token_from_request(request: Request) -> Optional[str]:
    """Extract bearer token first, then the legacy auth cookie."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
        if token:
            return token
    return request.cookies.get("auth_token")


def authenticate_request(request: Request) -> Dict[str, Any]:
    """Authenticate an HTTP request and return the current active DB user."""
    token = _token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication credentials missing")

    payload = verify_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Session expired or invalid token")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid session subject")

    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT id, username, email, role, is_active, must_change_password FROM users WHERE id = %s AND is_active = 1",
            (user_id,)
        )
        user = cur.fetchone()
        cur.close()
    finally:
        conn.close()

    if not user:
        raise HTTPException(status_code=401, detail="User account disabled or deleted")

    return user


def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_bearer)
) -> Dict[str, Any]:
    """FastAPI dependency. Uses the supplied bearer credentials or request cookie."""
    if credentials:
        token = credentials.credentials
        request.state.auth_token = token
    return authenticate_request(request)


def require_role(*allowed_roles: str):
    """Create a FastAPI dependency requiring one of the supplied roles."""
    def _require_role(current_user: Dict[str, Any] = Depends(get_current_user)):
        if current_user.get("role") not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=f"Required role: {', '.join(allowed_roles)}"
            )
        return current_user
    return _require_role


def require_admin(current_user: Dict[str, Any] = Depends(get_current_user)):
    return _require_admin(current_user)


def _require_admin(current_user: Dict[str, Any]):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return current_user


def seed_default_admin():
    """Initializes admin account if users table is empty.

    Prioritizes ADMIN_DEFAULT_PASSWORD env var; if omitted, generates a cryptographically
    secure one-time password and enforces must_change_password flag upon first login.
    """
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT COUNT(*) as count FROM users")
        count = cur.fetchone()["count"]
        if count == 0:
            env_pwd = os.getenv("ADMIN_DEFAULT_PASSWORD", "").strip()
            if env_pwd:
                default_pwd = env_pwd
                must_change = 0
            else:
                default_pwd = secrets.token_urlsafe(12)
                must_change = 1

            pwd_hash = hash_password(default_pwd)
            cur.execute(
                """
                INSERT INTO users (username, email, password_hash, role, is_active, must_change_password)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                ("admin", "admin@crypto-signal-bot.local", pwd_hash, "admin", 1, must_change)
            )
            conn.commit()

            # Store credentials securely in local file
            cred_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".initial_admin_credential")
            with open(cred_file, "w") as f:
                f.write(f"username: admin\npassword: {default_pwd}\nmust_change_password: {must_change}\ncreated_at: {time.time()}\n")
            try:
                os.chmod(cred_file, 0o600)
            except Exception:
                pass
            logger.info("Admin user bootstrap complete. Initial credentials recorded in %s", cred_file)
        cur.close()
    finally:
        conn.close()
