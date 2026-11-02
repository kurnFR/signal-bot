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
import threading
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
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as secret_file:
            fd = -1
            secret_file.write(secret)
            secret_file.flush()
            os.fsync(secret_file.fileno())

        # Linking a fully written temporary file avoids exposing an empty/partial key
        # if multiple application workers start at the same time. The first link wins.
        try:
            if hasattr(os, "link"):
                os.link(temp_path, path)
            else:
                os.replace(temp_path, path)
            return secret
        except FileExistsError:
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            with open(path, "r", encoding="utf-8") as secret_file:
                existing = secret_file.read().strip()
            if not existing:
                raise RuntimeError("Application secret file exists but is empty")
            return existing
        except OSError:
            if not os.path.exists(path):
                os.replace(temp_path, path)
                return secret
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


def create_access_token(user_id: int, username: str, role: str, session_version: int = 0) -> str:
    now = int(time.time())
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "session_version": int(session_version),
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
# Local revocation cache entries expire with their signed access tokens. The
# database remains authoritative across workers; this cache is only a fast local
# deny path and a fail-closed safeguard if a revocation write fails.
_revoked_tokens_cache: Dict[str, float] = {}
_revoked_tokens_cache_lock = threading.Lock()
_revoked_tokens_cache_last_prune = 0.0
_REVOCATION_CACHE_PRUNE_INTERVAL_SECONDS = 60


def _decode_token_payload_unverified(token: str) -> Optional[Dict[str, Any]]:
    """Decode JWT payload without verifying signature (for reading exp during revocation)."""
    if not token or "." not in token:
        return None
    try:
        payload_b64 = token.split(".", 1)[0]
        pad = payload_b64 + "=" * (-len(payload_b64) % 4)
        return json.loads(base64.urlsafe_b64decode(pad.encode("utf-8")).decode("utf-8"))
    except Exception:
        return None


def _prune_revoked_tokens_cache_locked(now: float) -> None:
    """Remove expired cache entries at a bounded cadence while holding the lock."""
    global _revoked_tokens_cache_last_prune
    if now - _revoked_tokens_cache_last_prune < _REVOCATION_CACHE_PRUNE_INTERVAL_SECONDS:
        return
    expired = [
        token_hash
        for token_hash, expires_at in _revoked_tokens_cache.items()
        if expires_at <= now
    ]
    for token_hash in expired:
        _revoked_tokens_cache.pop(token_hash, None)
    _revoked_tokens_cache_last_prune = now


def _remember_revoked_token(token: str) -> None:
    if not token:
        return
    now = time.time()
    payload = _decode_token_payload_unverified(token)
    try:
        expires_at = float(payload.get("exp")) if payload else now + TOKEN_EXPIRY_SECONDS
    except (TypeError, ValueError):
        expires_at = now + TOKEN_EXPIRY_SECONDS
    if expires_at <= now:
        return

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _revoked_tokens_cache_lock:
        _prune_revoked_tokens_cache_locked(now)
        _revoked_tokens_cache[token_hash] = expires_at


def _is_cached_revoked_token(token_hash: str) -> bool:
    now = time.time()
    with _revoked_tokens_cache_lock:
        _prune_revoked_tokens_cache_locked(now)
        expires_at = _revoked_tokens_cache.get(token_hash)
        if expires_at is None:
            return False
        if expires_at <= now:
            _revoked_tokens_cache.pop(token_hash, None)
            return False
        return True


REVOCATION_CLEANUP_LOCK_NAME = "signal_bot_revoked_expiry_cleanup"
REVOCATION_CLEANUP_BATCH_SIZE = 1000


def cleanup_expired_revocations() -> None:
    """Best-effort bounded cleanup on its own connection and transaction.

    This must stay outside the caller's revocation/audit transaction: a cleanup
    deadlock or statement failure must never roll back a successful revocation.
    """
    conn = None
    cur = None
    lock_attempted = False
    try:
        conn = get_pool().get_connection()
        cur = conn.cursor()
        lock_attempted = True
        cur.execute("SELECT GET_LOCK(%s, 0)", (REVOCATION_CLEANUP_LOCK_NAME,))
        row = cur.fetchone()
        if isinstance(row, dict):
            lock_result = next(iter(row.values()), None)
        elif isinstance(row, (tuple, list)):
            lock_result = row[0] if row else None
        else:
            lock_result = row
        if lock_result != 1:
            return

        cur.execute(
            "DELETE FROM revoked_tokens "
            "WHERE expires_at <= UTC_TIMESTAMP() LIMIT %s",
            (REVOCATION_CLEANUP_BATCH_SIZE,),
        )
        conn.commit()
    except Exception as exc:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.warning(
            "Expired token revocation cleanup skipped: %s", type(exc).__name__
        )
    finally:
        # RELEASE_LOCK is safe even if GET_LOCK returned 0/NULL, and also covers
        # an exception after the server acquired the lock but before fetchone().
        if lock_attempted and cur is not None:
            try:
                cur.execute("SELECT RELEASE_LOCK(%s)", (REVOCATION_CLEANUP_LOCK_NAME,))
                cur.fetchone()
            except Exception as exc:
                logger.warning(
                    "Expired token cleanup lock release failed: %s", type(exc).__name__
                )
        if cur is not None:
            try:
                cur.close()
            except Exception as exc:
                logger.warning(
                    "Expired token cleanup cursor close failed: %s", type(exc).__name__
                )
        if conn is not None:
            try:
                conn.close()
            except Exception as exc:
                logger.warning(
                    "Expired token cleanup connection close failed: %s", type(exc).__name__
                )


def revoke_token_cursor(cur, token: str) -> str:
    """Insert a token revocation using the caller's open transaction.

    Returns the SHA-256 token hash. The caller must commit before caching it.
    """
    if not token:
        return ""
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    payload = _decode_token_payload_unverified(token)
    exp = payload.get("exp", int(time.time()) + TOKEN_EXPIRY_SECONDS) if payload else int(time.time()) + TOKEN_EXPIRY_SECONDS
    exp_dt = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(exp))
    cur.execute(
        """
        INSERT INTO revoked_tokens (token_hash, expires_at)
        VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE expires_at = VALUES(expires_at)
        """,
        (token_hash, exp_dt),
    )
    return token_hash


def cache_revoked_token(token: str) -> None:
    """Cache a committed revocation until the signed token expires."""
    _remember_revoked_token(token)


def revoke_token(token: str) -> None:
    """Persist token revocation; raise if shared storage cannot commit it."""
    if not token:
        return
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    # Preserve fail-closed behavior on this worker if persistence subsequently fails.
    _remember_revoked_token(token)
    conn = None
    try:
        conn = get_pool().get_connection()
        cur = conn.cursor()
        revoke_token_cursor(cur, token)
        conn.commit()
        cur.close()
        conn.close()
        conn = None
        cleanup_expired_revocations()
        logger.info("Token %s... revoked and blacklisted", token_hash[:8])
    except Exception as exc:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.error("Failed to persist revoked token (%s)", type(exc).__name__)
        raise RuntimeError("Failed to persist revoked token to database") from exc
    finally:
        if conn is not None:
            conn.close()


def is_token_revoked(token: str) -> bool:
    """Check if token was revoked via database lookup and in-memory cache.
    
    Fails closed: if revocation status cannot be verified due to a database or
    connection error, the token is treated as revoked (True) to prevent
    unauthorized access.
    """
    if not token:
        return True
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if _is_cached_revoked_token(token_hash):
        return True

    try:
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
                _remember_revoked_token(token)
                return True
            return False
        finally:
            conn.close()
    except Exception as exc:
        logger.error("Error checking token revocation from storage; failing closed: %s", exc)
        return True


# ============================================================================
# Login Rate Limiting & Lockout
# ============================================================================
_login_attempts: Dict[str, list[float]] = {}
RATE_LIMIT_MAX_ATTEMPTS = 5
RATE_LIMIT_WINDOW_SECONDS = 300  # 5 minutes

# Durable backend (table `login_attempts`, migration 013). Once enabled by the
# application, shared storage is mandatory and failures never fall back to memory.
_durable_rate_limit = False
_durable_rate_limit_degraded = False


class RateLimitBackendUnavailable(RuntimeError):
    """Shared login-rate-limit storage cannot currently enforce the policy."""


def is_rate_limiter_durable() -> bool:
    return _durable_rate_limit


def get_rate_limiter_health() -> Dict[str, Any]:
    mode = "in-memory" if not _durable_rate_limit else ("unavailable" if _durable_rate_limit_degraded else "database")
    return {"durable_enabled": _durable_rate_limit, "degraded": _durable_rate_limit_degraded, "mode": mode}


def enable_durable_rate_limit() -> bool:
    global _durable_rate_limit, _durable_rate_limit_degraded
    _durable_rate_limit = True
    _durable_rate_limit_degraded = True
    try:
        conn = get_pool().get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT attempt_state FROM login_attempts LIMIT 1")
            cur.fetchall()
            cur.close()
        finally:
            conn.close()
        _durable_rate_limit_degraded = False
    except Exception as exc:
        logger.error("Shared login rate limiting unavailable at startup (%s); login will fail closed", type(exc).__name__)
    return not _durable_rate_limit_degraded


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


def _login_rate_lock_name(key: str) -> str:
    return "sb_login:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:55]


def _cleanup_expired_login_attempts(cur, cutoff: float) -> None:
    """Delete a bounded batch of expired rows under one cross-worker cleanup lock."""
    cur.execute("SELECT GET_LOCK(%s, %s)", ("sb_login_gc", 0))
    lock_row = cur.fetchone()
    if not lock_row or int(lock_row[0] or 0) != 1:
        return

    try:
        cur.execute(
            "DELETE FROM login_attempts WHERE attempted_at < %s LIMIT 1000",
            (cutoff,),
        )
    finally:
        cur.execute("SELECT RELEASE_LOCK(%s)", ("sb_login_gc",))
        release_row = cur.fetchone()
        if not release_row or int(release_row[0] or 0) != 1:
            raise RuntimeError("Could not release expired login-attempt cleanup lock")


def admit_login_attempt(key: str) -> tuple[bool, int, Optional[int]]:
    """Atomically reserve a shared login attempt before password verification.

    Returns (allowed, current_count, reservation_id). A durable reservation starts
    as pending and is finalized after password verification. Pending reservations
    count toward the threshold, including across workers and process restarts.
    """
    global _durable_rate_limit_degraded
    if not _durable_rate_limit:
        return check_login_rate_limit(key), 0, None

    lock_name = _login_rate_lock_name(key)
    now = time.time()
    conn = None
    cur = None
    lock_acquired = False
    release_failed = False
    try:
        conn = get_pool().get_connection()
        cur = conn.cursor()
        cur.execute("SELECT GET_LOCK(%s, %s)", (lock_name, 5))
        lock_row = cur.fetchone()
        if not lock_row or int(lock_row[0] or 0) != 1:
            raise RuntimeError("Could not acquire shared login admission lock")
        lock_acquired = True

        _cleanup_expired_login_attempts(cur, now - RATE_LIMIT_WINDOW_SECONDS)
        cur.execute(
            "SELECT COUNT(*) FROM login_attempts WHERE rate_key = %s AND attempted_at >= %s",
            (key, now - RATE_LIMIT_WINDOW_SECONDS),
        )
        count = int(cur.fetchone()[0])
        if count >= RATE_LIMIT_MAX_ATTEMPTS:
            conn.commit()
            _durable_rate_limit_degraded = False
            return False, count, None

        cur.execute(
            "INSERT INTO login_attempts (rate_key, attempted_at, attempt_state) VALUES (%s, %s, 'pending')",
            (key, now),
        )
        reservation_id = int(cur.lastrowid or 0)
        if reservation_id <= 0:
            raise RuntimeError("Database did not return a login reservation id")
        conn.commit()
        _durable_rate_limit_degraded = False
        return True, count + 1, reservation_id
    except Exception as exc:
        _durable_rate_limit_degraded = True
        logger.error("Atomic shared login admission failed; refusing login (%s)", type(exc).__name__)
        raise RateLimitBackendUnavailable("Shared login rate-limit storage is unavailable") from exc
    finally:
        if cur is not None:
            if lock_acquired:
                try:
                    cur.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
                    release_row = cur.fetchone()
                    if not release_row or int(release_row[0] or 0) != 1:
                        _durable_rate_limit_degraded = True
                        raise RateLimitBackendUnavailable("Shared login admission lock release could not be confirmed")
                except Exception as exc:
                    _durable_rate_limit_degraded = True
                    release_failed = True
                    logger.error("Could not explicitly release login admission lock (%s)", type(exc).__name__)
            try:
                cur.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        if release_failed:
            raise RateLimitBackendUnavailable("Shared login rate-limit storage is unavailable")


def finalize_login_attempt(key: str, reservation_id: int, *, success: bool) -> int:
    """Finalize one pending reservation without disturbing other in-flight logins.

    On failure, only this reservation becomes a counted failed attempt. On success,
    prior completed failures and this request's reservation are removed, while
    concurrent pending reservations remain counted.
    """
    global _durable_rate_limit_degraded
    if not _durable_rate_limit:
        raise RateLimitBackendUnavailable("Durable login reservation is not enabled")
    if not reservation_id:
        raise RateLimitBackendUnavailable("Login reservation is missing")

    lock_name = _login_rate_lock_name(key)
    now = time.time()
    conn = None
    cur = None
    lock_acquired = False
    release_failed = False
    failed_count = 0
    try:
        conn = get_pool().get_connection()
        cur = conn.cursor()
        cur.execute("SELECT GET_LOCK(%s, %s)", (lock_name, 5))
        lock_row = cur.fetchone()
        if not lock_row or int(lock_row[0] or 0) != 1:
            raise RuntimeError("Could not acquire shared login finalization lock")
        lock_acquired = True

        cur.execute(
            "DELETE FROM login_attempts WHERE rate_key = %s AND attempted_at < %s",
            (key, now - RATE_LIMIT_WINDOW_SECONDS),
        )
        if success:
            cur.execute(
                "DELETE FROM login_attempts WHERE rate_key = %s AND attempt_state = 'failed'",
                (key,),
            )
            cur.execute(
                "DELETE FROM login_attempts WHERE id = %s AND rate_key = %s AND attempt_state = 'pending'",
                (reservation_id, key),
            )
        else:
            cur.execute(
                "UPDATE login_attempts SET attempt_state = 'failed' "
                "WHERE id = %s AND rate_key = %s AND attempt_state = 'pending'",
                (reservation_id, key),
            )
            cur.execute(
                "SELECT COUNT(*) FROM login_attempts WHERE rate_key = %s AND attempted_at >= %s",
                (key, now - RATE_LIMIT_WINDOW_SECONDS),
            )
            failed_count = int(cur.fetchone()[0])
        conn.commit()
        _durable_rate_limit_degraded = False
        return failed_count
    except Exception as exc:
        _durable_rate_limit_degraded = True
        logger.error("Shared login reservation finalization failed (%s)", type(exc).__name__)
        raise RateLimitBackendUnavailable("Shared login rate-limit storage is unavailable") from exc
    finally:
        if cur is not None:
            if lock_acquired:
                try:
                    cur.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
                    release_row = cur.fetchone()
                    if not release_row or int(release_row[0] or 0) != 1:
                        _durable_rate_limit_degraded = True
                        raise RateLimitBackendUnavailable("Shared login finalization lock release could not be confirmed")
                except Exception as exc:
                    _durable_rate_limit_degraded = True
                    release_failed = True
                    logger.error("Could not explicitly release login finalization lock (%s)", type(exc).__name__)
            try:
                cur.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        if release_failed:
            raise RateLimitBackendUnavailable("Shared login rate-limit storage is unavailable")


def check_login_rate_limit(key: str) -> bool:
    global _durable_rate_limit_degraded
    now = time.time()
    if _durable_rate_limit:
        try:
            count = _db_count_attempts(key, now)
            _durable_rate_limit_degraded = False
            return count < RATE_LIMIT_MAX_ATTEMPTS
        except Exception as exc:
            _durable_rate_limit_degraded = True
            logger.error("Shared login rate-limit check failed; refusing login (%s)", type(exc).__name__)
            raise RateLimitBackendUnavailable("Shared login rate-limit storage is unavailable") from exc
    attempts = [t for t in _login_attempts.get(key, []) if now - t < RATE_LIMIT_WINDOW_SECONDS]
    _login_attempts[key] = attempts
    return len(attempts) < RATE_LIMIT_MAX_ATTEMPTS


def record_failed_login(key: str) -> int:
    global _durable_rate_limit_degraded
    now = time.time()
    if _durable_rate_limit:
        try:
            conn = get_pool().get_connection()
            try:
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO login_attempts (rate_key, attempted_at, attempt_state) VALUES (%s, %s, 'failed')",
                    (key, now),
                )
                conn.commit()
                cur.close()
            finally:
                conn.close()
            count = _db_count_attempts(key, now)
            _durable_rate_limit_degraded = False
            return count
        except Exception as exc:
            _durable_rate_limit_degraded = True
            logger.error("Shared login rate-limit write failed; refusing login (%s)", type(exc).__name__)
            raise RateLimitBackendUnavailable("Shared login rate-limit storage is unavailable") from exc
    attempts = [t for t in _login_attempts.get(key, []) if now - t < RATE_LIMIT_WINDOW_SECONDS]
    attempts.append(now)
    _login_attempts[key] = attempts
    return len(attempts)


def reset_login_attempts(key: str) -> None:
    global _durable_rate_limit_degraded
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
            _durable_rate_limit_degraded = False
        except Exception as exc:
            _durable_rate_limit_degraded = True
            logger.error("Shared login rate-limit reset failed; refusing login (%s)", type(exc).__name__)
            raise RateLimitBackendUnavailable("Shared login rate-limit storage is unavailable") from exc


# ============================================================================
# Audit Logging (Immutable System Action History)
# ============================================================================
def log_audit_cursor(
    cur,
    action: str,
    username: str,
    user_id: Optional[int] = None,
    details: Optional[Dict[str, Any]] = None,
    ip_address: Optional[str] = None,
) -> None:
    """Records an action in audit_logs using an existing open database cursor."""
    details_json = json.dumps(details) if details else None
    cur.execute(
        """
        INSERT INTO audit_logs (user_id, username, action, details, ip_address)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (user_id, username, action, details_json, ip_address),
    )


def log_audit(
    action: str,
    username: str,
    user_id: Optional[int] = None,
    details: Optional[Dict[str, Any]] = None,
    ip_address: Optional[str] = None,
    raise_on_error: bool = False,
) -> bool:
    """Records an administrative, security, or trading action in audit_logs.
    
    Returns True if successfully written. If raise_on_error is True, raises
    RuntimeError on database persistence failure so security-critical operations
    do not proceed silently without an audit trail.
    """
    try:
        conn = get_pool().get_connection()
        try:
            cur = conn.cursor()
            log_audit_cursor(cur, action, username, user_id, details, ip_address)
            conn.commit()
            cur.close()
            return True
        finally:
            conn.close()
    except Exception as exc:
        logger.error("Failed to write audit log (%s by %s): %s", action, username, exc)
        if raise_on_error:
            raise RuntimeError(f"Critical audit logging failed for {action}: {exc}") from exc
        return False


def _token_from_request(request: Request) -> Optional[str]:
    """Extract the bearer token; browser cookies are not an authentication mechanism."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
        if token:
            return token
    return None


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
            "SELECT id, username, email, role, is_active, must_change_password, session_version FROM users WHERE id = %s AND is_active = 1",
            (user_id,)
        )
        user = cur.fetchone()
        cur.close()
    finally:
        conn.close()

    if not user:
        raise HTTPException(status_code=401, detail="User account disabled or deleted")

    try:
        token_session_version = int(payload.get("session_version", 0))
        current_session_version = int(user.get("session_version", 0))
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Session expired or invalid token")
    if token_session_version != current_session_version:
        raise HTTPException(status_code=401, detail="Session expired or invalid token")

    return user


def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_bearer)
) -> Dict[str, Any]:
    """FastAPI dependency. Uses the supplied bearer credentials."""
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
