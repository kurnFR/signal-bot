"""
Authentication & User Management API Router.
Strictly parameterized SQL queries throughout to eliminate SQL injection vectors.
"""
import re
import logging
from typing import Optional, List, Callable, Any
from fastapi import APIRouter, HTTPException, Depends, status
from pydantic import BaseModel, Field

from db.db import get_pool
from web.auth import (
    hash_password, verify_password, create_access_token,
    get_current_user, require_admin,
    admit_login_attempt, finalize_login_attempt,
    record_failed_login, reset_login_attempts,
    log_audit, log_audit_cursor, _token_from_request, RateLimitBackendUnavailable,
    revoke_token_cursor, cache_revoked_token, cleanup_expired_revocations,
    create_ws_ticket, WS_TICKET_TTL_SECONDS, TOKEN_EXPIRY_SECONDS,
)
from fastapi import Request

router = APIRouter(prefix="/api", tags=["authentication-and-users"])
logger = logging.getLogger("web.auth_routes")

def _get_required_connection():
    """Acquire DB storage for a security-sensitive route or fail with a controlled 503."""
    try:
        return get_pool().get_connection()
    except Exception as exc:
        logger.error("Required security database connection unavailable (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="Security audit storage is temporarily unavailable. Please retry shortly.",
        ) from exc


def _write_required_audit(action: str, username: str, user_id: Optional[int] = None,
                          details: Optional[dict] = None, ip_address: Optional[str] = None) -> None:
    """Persist a mandatory security event before returning an auth outcome."""
    try:
        log_audit(action, username, user_id, details, ip_address, raise_on_error=True)
    except Exception as exc:
        logger.error("Required audit write failed for %s (%s)", action, type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="Security audit storage is temporarily unavailable. Please retry shortly.",
        )


# Test-only hooks for deterministic transaction synchronization tests.
# Must remain None in production.
_test_before_admin_lock_hook: Optional[Callable[[Any, Any, int], None]] = None
_test_after_admin_lock_hook: Optional[Callable[[Any, Any, int], None]] = None
_test_active_admin_filter: Optional[Callable[[dict], bool]] = None


def _commit_with_audit(conn, cur, action: str, username: str,
                       user_id: Optional[int] = None, details: Optional[dict] = None,
                       ip_address: Optional[str] = None) -> None:
    """Commit a DB mutation and its mandatory audit row atomically."""
    try:
        log_audit_cursor(cur, action, username, user_id, details, ip_address)
        conn.commit()
    except Exception as exc:
        try:
            conn.rollback()
            setattr(conn, "_rolled_back", True)
        except Exception:
            pass
        logger.error("Required audit transaction failed for %s (%s)", action, type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="Security audit storage is temporarily unavailable. Please retry shortly.",
        )

USERNAME_REGEX = re.compile(r"^[a-zA-Z0-9_-]{3,30}$")

# Bound untrusted credential fields before handler-level database lookups and PBKDF2 work.
MAX_USERNAME_LENGTH = 30
MAX_EMAIL_LENGTH = 254
MAX_PASSWORD_LENGTH = 1024


MIN_PASSWORD_LENGTH = 10
# Bases that stay weak with digits/symbols appended or simple leetspeak (password1234, P@ssw0rd!).
_COMMON_BASES = {
    "password", "passw0rd", "pass", "admin", "administrator", "root", "user", "login", "welcome",
    "letmein", "changeme", "qwerty", "qwertyuiop", "asdfgh", "asdfghjkl", "zxcvbn", "iloveyou",
    "monkey", "dragon", "master", "sunshine", "princess", "football", "baseball", "secret",
    "signalbot", "signal", "trader", "trading", "bitcoin", "crypto", "binance", "ethereum",
    "abc", "abcdef", "abcdefgh", "temp", "temporary", "default", "test", "guest", "demo",
}
_LEET = str.maketrans({"0": "o", "@": "a", "$": "s", "3": "e", "1": "l", "!": "i", "5": "s", "7": "t"})


_TRAILING = "0123456789!@#$%^&*()-_=+.,?"


def _password_base(password: str) -> str:
    """Lowercase, strip trailing digits/symbols, then undo simple leetspeak.

    Order matters: stripping first keeps "password1234" -> "password" (translating
    first would turn the digit 1 into "l" and hide the word).
    """
    stripped = password.lower().rstrip(_TRAILING)
    return stripped.translate(_LEET)


def _is_sequence(password: str) -> bool:
    """True for pure runs like 1234567890 or abcdefghij."""
    if len(password) < 4:
        return False
    steps = {ord(b) - ord(a) for a, b in zip(password, password[1:])}
    return steps in ({1}, {-1}, {0})


def _validate_new_password(password: str, username: str) -> None:
    """Reject weak passwords chosen by a user changing their own password."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=400, detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    lowered = password.lower()
    base = _password_base(password)
    user = (username or "").lower()
    if (lowered == user or (user and base == _password_base(user)) or base == ""
            or base in _COMMON_BASES or _is_sequence(lowered)):
        raise HTTPException(status_code=400, detail="Password is too common or easy to guess. Choose something less predictable")
    if len(set(password)) < 5:
        raise HTTPException(status_code=400, detail="Password needs more variety of characters")


# ============================================================================
# Request / Response Schemas
# ============================================================================
class LoginRequest(BaseModel):
    username: str = Field(..., max_length=MAX_USERNAME_LENGTH, json_schema_extra={"example": "admin"})
    password: str = Field(..., max_length=MAX_PASSWORD_LENGTH, json_schema_extra={"example": "admin123"})


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., max_length=MAX_PASSWORD_LENGTH)
    new_password: str = Field(..., min_length=10, max_length=MAX_PASSWORD_LENGTH)


class CreateUserRequest(BaseModel):
    username: str = Field(..., max_length=MAX_USERNAME_LENGTH)
    email: Optional[str] = Field(None, max_length=MAX_EMAIL_LENGTH)
    password: str = Field(..., min_length=10, max_length=MAX_PASSWORD_LENGTH)
    role: str = Field("trader")  # admin, trader, viewer


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(..., min_length=10, max_length=MAX_PASSWORD_LENGTH)


class UpdateUserRequest(BaseModel):
    role: Optional[str] = Field(None)
    is_active: Optional[bool] = Field(None)


# ============================================================================
# Authentication Endpoints
# ============================================================================
@router.post("/auth/login")
def login(req: LoginRequest, request: Request):
    username = req.username.strip()
    if not username or not req.password:
        raise HTTPException(status_code=400, detail="Username and password are required")

    client_ip = request.client.host if request.client else "unknown"

    # Resolve the database identity before admission. Release this lookup
    # connection before the limiter opens its own connection and before the
    # expensive password hash verification runs.
    conn = None
    cur = None
    try:
        conn = _get_required_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT id, username, email, password_hash, role, is_active, must_change_password, session_version
            FROM users
            WHERE username = %s
            """,
            (username,)
        )
        user = cur.fetchone()
    except Exception as exc:
        logger.error("Login identity lookup failed; refusing login (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Login protection is temporarily unavailable. Please retry shortly.")
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                logger.warning("Could not close login identity cursor")
        if conn is not None:
            try:
                conn.close()
            except Exception:
                logger.warning("Could not return login identity connection to the pool")

    # Unknown usernames retain a per-IP normalized bucket. Known accounts use
    # the immutable DB identity, covering all DB-collation-equivalent spellings.
    account_key = f"user:{user['id']}" if user else f"unknown:{username.casefold()}"
    rate_key = f"{client_ip}:{account_key}"
    try:
        within_rate_limit, _, reservation_id = admit_login_attempt(rate_key)
    except RateLimitBackendUnavailable as exc:
        logger.error("Login blocked because shared rate limiting is unavailable: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Login protection is temporarily unavailable. Please retry shortly.")

    if not within_rate_limit:
        _write_required_audit("LOGIN_RATE_LIMITED", username, user["id"] if user else None, {"ip": client_ip}, client_ip)
        raise HTTPException(
            status_code=429,
            detail="Too many failed login attempts. Account temporarily locked. Please try again in 5 minutes."
        )

    if not user or not user["is_active"]:
        try:
            failed_count = (
                finalize_login_attempt(rate_key, reservation_id, success=False)
                if reservation_id is not None else record_failed_login(rate_key)
            )
        except RateLimitBackendUnavailable as exc:
            logger.error("Login failure could not be recorded in shared rate-limit storage: %s", type(exc).__name__)
            raise HTTPException(status_code=503, detail="Login protection is temporarily unavailable. Please retry shortly.")
        _write_required_audit("LOGIN_FAILED", username, user["id"] if user else None, {"attempt": failed_count, "reason": "user_not_found_or_inactive"}, client_ip)
        raise HTTPException(status_code=401, detail="Invalid username or password")

    if not verify_password(req.password, user["password_hash"]):
        try:
            failed_count = (
                finalize_login_attempt(rate_key, reservation_id, success=False)
                if reservation_id is not None else record_failed_login(rate_key)
            )
        except RateLimitBackendUnavailable as exc:
            logger.error("Login failure could not be recorded in shared rate-limit storage: %s", type(exc).__name__)
            raise HTTPException(status_code=503, detail="Login protection is temporarily unavailable. Please retry shortly.")
        _write_required_audit("LOGIN_FAILED", username, user["id"], {"attempt": failed_count, "reason": "bad_password"}, client_ip)
        raise HTTPException(status_code=401, detail="Invalid username or password")

    try:
        if reservation_id is not None:
            finalize_login_attempt(rate_key, reservation_id, success=True)
        else:
            reset_login_attempts(rate_key)
    except RateLimitBackendUnavailable as exc:
        logger.error("Login blocked because shared rate-limit reset failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Login protection is temporarily unavailable. Please retry shortly.")
    token = create_access_token(
        user["id"], user["username"], user["role"],
        session_version=int(user.get("session_version", 0)),
    )
    _write_required_audit("LOGIN_SUCCESS", user["username"], user["id"], {"role": user["role"]}, client_ip)

    return {
        "token": token,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "email": user["email"],
            "role": user["role"],
            "must_change_password": bool(user.get("must_change_password", 0)),
            "terms_accepted_at": str(user.get("terms_accepted_at")) if user.get("terms_accepted_at") else None,
        }
    }


@router.post("/auth/logout")
def logout(request: Request, current_user: dict = Depends(get_current_user)):
    token = _token_from_request(request)
    client_ip = request.client.host if request.client else "unknown"
    conn = None
    try:
        conn = _get_required_connection()
        cur = conn.cursor()
        if token:
            try:
                revoke_token_cursor(cur, token)
            except Exception as exc:
                try:
                    conn.rollback()
                except Exception:
                    pass
                logger.error("Token revocation transaction failed (%s)", type(exc).__name__)
                raise HTTPException(
                    status_code=503,
                    detail="Session revocation is temporarily unavailable. Please retry shortly.",
                )
        # Invalidate all active sessions for this user by incrementing session_version
        try:
            cur.execute("UPDATE users SET session_version = session_version + 1 WHERE id = %s", (current_user["id"],))
        except Exception as exc:
            logger.warning("Could not increment session_version on logout (%s)", exc)
        _commit_with_audit(
            conn, cur, "LOGOUT", current_user["username"], current_user["id"], None, client_ip
        )
        if token:
            cache_revoked_token(token)
            cleanup_expired_revocations()
        return {"status": "success", "message": "Successfully logged out"}
    except HTTPException:
        raise
    except Exception as exc:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.error("Logout audit transaction unavailable (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="Security audit storage is temporarily unavailable. Please retry shortly.",
        )
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                logger.warning("Logout database connection close failed")


@router.post("/auth/ws-ticket")
def issue_ws_ticket(current_user: dict = Depends(get_current_user)):
    """Issue a 30-second single-use ticket for authenticating WebSockets without bearer token in query."""
    ticket = create_ws_ticket(current_user)
    return {"ticket": ticket, "expires_in": WS_TICKET_TTL_SECONDS}


@router.post("/auth/refresh")
def refresh_access_token(request: Request, current_user: dict = Depends(get_current_user)):
    """Refreshes active access token under current session version."""
    token = create_access_token(
        current_user["id"],
        current_user["username"],
        current_user["role"],
        session_version=int(current_user.get("session_version", 0)),
    )
    return {
        "token": token,
        "expires_in": TOKEN_EXPIRY_SECONDS,
        "user": {
            "id": current_user["id"],
            "username": current_user["username"],
            "email": current_user["email"],
            "role": current_user["role"],
            "must_change_password": bool(current_user.get("must_change_password", 0)),
            "terms_accepted_at": str(current_user.get("terms_accepted_at")) if current_user.get("terms_accepted_at") else None,
        },
    }


@router.post("/auth/accept-terms")
def accept_terms(request: Request, current_user: dict = Depends(get_current_user)):
    """Acknowledge financial risk disclaimer and compliance terms."""
    client_ip = request.client.host if request.client else "unknown"
    conn = _get_required_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET terms_accepted_at = UTC_TIMESTAMP() WHERE id = %s",
            (current_user["id"],),
        )
        _commit_with_audit(
            conn, cur, "TERMS_ACCEPTED", current_user["username"], current_user["id"], None, client_ip
        )
        return {"status": "success", "message": "Terms and conditions acknowledged"}
    finally:
        conn.close()


@router.get("/auth/me")
def get_current_user_profile(current_user: dict = Depends(get_current_user)):
    return {
        "id": current_user["id"],
        "username": current_user["username"],
        "email": current_user["email"],
        "role": current_user["role"],
        "must_change_password": bool(current_user.get("must_change_password", 0)),
        "terms_accepted_at": str(current_user.get("terms_accepted_at")) if current_user.get("terms_accepted_at") else None,
    }


@router.post("/auth/change-password")
def change_password(req: ChangePasswordRequest, request: Request, current_user: dict = Depends(get_current_user)):
    conn = _get_required_connection()
    try:
        cur = conn.cursor(dictionary=True)
        # Fetch current hash using parameterized query
        cur.execute("SELECT password_hash FROM users WHERE id = %s", (current_user["id"],))
        row = cur.fetchone()
        if not row or not verify_password(req.old_password, row["password_hash"]):
            raise HTTPException(status_code=400, detail="Current password does not match")

        if verify_password(req.new_password, row["password_hash"]):
            raise HTTPException(status_code=400, detail="New password must be different from the current password")
        _validate_new_password(req.new_password, current_user["username"])

        new_hash = hash_password(req.new_password)
        next_session_version = int(current_user.get("session_version", 0)) + 1
        renewed_token = create_access_token(
            current_user["id"], current_user["username"], current_user["role"],
            session_version=next_session_version,
        )
        cur.execute(
            "UPDATE users SET password_hash = %s, must_change_password = 0, session_version = session_version + 1, updated_at = NOW() WHERE id = %s",
            (new_hash, current_user["id"])
        )
        client_ip = request.client.host if request.client else "unknown"
        _commit_with_audit(
            conn, cur, "PASSWORD_CHANGED", current_user["username"], current_user["id"], None, client_ip
        )
        return {
            "status": "success",
            "message": "Password changed successfully",
            "token": renewed_token,
        }
    finally:
        conn.close()


@router.get("/auth/audit-logs")
def get_audit_logs(limit: int = 50, current_user: dict = Depends(require_admin)):
    conn = _get_required_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT id, user_id, username, action, details, ip_address, created_at
            FROM audit_logs
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (max(1, min(100, limit)),)
        )
        rows = cur.fetchall()
        cur.close()
        for r in rows:
            r["created_at"] = str(r["created_at"])
            if isinstance(r.get("details"), str):
                try:
                    import json
                    r["details"] = json.loads(r["details"])
                except Exception:
                    pass
        return {"items": rows}
    finally:
        conn.close()


# ============================================================================
# User Management Endpoints (Admin Only)
# ============================================================================
@router.get("/users")
def list_users(admin: dict = Depends(require_admin)):
    conn = _get_required_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT id, username, email, role, is_active, must_change_password, created_at, updated_at
            FROM users
            ORDER BY id ASC
            """
        )
        users = cur.fetchall()
        cur.close()
        for u in users:
            u["is_active"] = bool(u["is_active"])
            u["must_change_password"] = bool(u.get("must_change_password", 0))
            u["created_at"] = str(u["created_at"])
            u["updated_at"] = str(u["updated_at"])
        return {"users": users}
    finally:
        conn.close()


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(req: CreateUserRequest, admin: dict = Depends(require_admin)):
    clean_username = req.username.strip().lower()
    if not USERNAME_REGEX.match(clean_username):
        raise HTTPException(
            status_code=400,
            detail="Username must be 3-30 characters and contain only letters, numbers, hyphens, and underscores."
        )

    allowed_roles = ("admin", "trader", "viewer")
    clean_role = req.role.strip().lower()
    if clean_role not in allowed_roles:
        raise HTTPException(status_code=400, detail=f"Role must be one of: {', '.join(allowed_roles)}")

    clean_email = req.email.strip().lower() if req.email else None
    _validate_new_password(req.password, clean_username)
    new_hash = hash_password(req.password)

    conn = _get_required_connection()
    try:
        cur = conn.cursor(dictionary=True)
        # Check collision using parameterized query
        cur.execute("SELECT id FROM users WHERE username = %s", (clean_username,))
        if cur.fetchone():
            raise HTTPException(status_code=409, detail=f"Username '{clean_username}' already exists")

        cur.execute(
            """
            INSERT INTO users (username, email, password_hash, role, is_active)
            VALUES (%s, %s, %s, %s, 1)
            """,
            (clean_username, clean_email, new_hash, clean_role)
        )
        new_id = cur.lastrowid

        cur.execute(
            """
            SELECT id, username, email, role, is_active, must_change_password, created_at, updated_at
            FROM users
            WHERE id = %s
            """,
            (new_id,)
        )
        created_user = cur.fetchone()
        if not created_user:
            created_user = {
                "id": new_id,
                "username": clean_username,
                "email": clean_email,
                "role": clean_role,
                "is_active": True,
                "must_change_password": False,
                "created_at": "",
                "updated_at": "",
            }
        else:
            created_user["is_active"] = bool(created_user.get("is_active", 1))
            created_user["must_change_password"] = bool(created_user.get("must_change_password", 0))
            created_user["created_at"] = str(created_user["created_at"]) if created_user.get("created_at") is not None else ""
            created_user["updated_at"] = str(created_user["updated_at"]) if created_user.get("updated_at") is not None else ""

        _commit_with_audit(
            conn, cur, "USER_CREATED", admin["username"], admin["id"],
            {"created_user": clean_username, "created_user_id": new_id, "role": clean_role},
        )

        return {
            "status": "success",
            "user": created_user,
        }
    except HTTPException:
        if conn is not None and not getattr(conn, "_rolled_back", False):
            try:
                conn.rollback()
                setattr(conn, "_rolled_back", True)
            except Exception:
                pass
        raise
    except Exception:
        if conn is not None and not getattr(conn, "_rolled_back", False):
            try:
                conn.rollback()
                setattr(conn, "_rolled_back", True)
            except Exception:
                pass
        raise
    finally:
        if conn is not None:
            conn.close()


@router.post("/users/{user_id}/reset-password")
def reset_user_password(user_id: int, req: ResetPasswordRequest, admin: dict = Depends(require_admin)):
    conn = _get_required_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id, username FROM users WHERE id = %s", (user_id,))
        target = cur.fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="Target user not found")

        _validate_new_password(req.new_password, target["username"])
        new_hash = hash_password(req.new_password)
        cur.execute(
            "UPDATE users SET password_hash = %s, must_change_password = 1, session_version = session_version + 1, updated_at = NOW() WHERE id = %s",
            (new_hash, user_id)
        )
        _commit_with_audit(
            conn, cur, "USER_PASSWORD_RESET", admin["username"], admin["id"],
            {"target_user": target["username"], "target_id": user_id},
        )

        return {
            "status": "success",
            "message": f"Password reset successfully for user '{target['username']}'"
        }
    except HTTPException:
        if conn is not None and not getattr(conn, "_rolled_back", False):
            try:
                conn.rollback()
                setattr(conn, "_rolled_back", True)
            except Exception:
                pass
        raise
    except Exception:
        if conn is not None and not getattr(conn, "_rolled_back", False):
            try:
                conn.rollback()
                setattr(conn, "_rolled_back", True)
            except Exception:
                pass
        raise
    finally:
        if conn is not None:
            conn.close()


@router.put("/users/{user_id}")
def update_user(user_id: int, req: UpdateUserRequest, admin: dict = Depends(require_admin)):
    if req.role is not None and req.role not in ("admin", "trader", "viewer"):
        raise HTTPException(status_code=400, detail="Invalid role")

    conn = _get_required_connection()
    try:
        cur = conn.cursor(dictionary=True)
        # Consistent serialization protocol: serialize role changes, active-state changes,
        # and deletions against the active-admin set.
        # Acquire locks on all active administrator rows with SELECT ... FOR UPDATE
        # in deterministic ascending ID order. Retain locks until commit/rollback.
        if _test_before_admin_lock_hook is not None:
            _test_before_admin_lock_hook(conn, cur, user_id)
        cur.execute(
            "SELECT id, username FROM users WHERE role = 'admin' AND is_active = 1 ORDER BY id FOR UPDATE"
        )
        if _test_after_admin_lock_hook is not None:
            _test_after_admin_lock_hook(conn, cur, user_id)
        active_admins = cur.fetchall() or []
        if _test_active_admin_filter is not None:
            active_admins = [u for u in active_admins if _test_active_admin_filter(u)]

        # Read the target user state under lock
        cur.execute("SELECT id, username, role, is_active FROM users WHERE id = %s FOR UPDATE", (user_id,))
        target = cur.fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="Target user not found")

        # 1. Admin self-demotion protection: an admin cannot change their own role
        if target["id"] == admin["id"] and req.role is not None and req.role != "admin":
            raise HTTPException(status_code=400, detail="Cannot change your own role from admin")

        # 2. Admin self-deactivation protection: an admin cannot deactivate their own account
        if target["id"] == admin["id"] and req.is_active is False:
            raise HTTPException(status_code=400, detail="Cannot deactivate your own admin account")

        # 3. Last-admin protection: prevent any mutation from leaving zero active administrators.
        target_is_active_admin = (target.get("role") == "admin" and bool(target.get("is_active")))
        demoting_target = (req.role is not None and req.role != "admin")
        deactivating_target = (req.is_active is False)
        if target_is_active_admin and (demoting_target or deactivating_target):
            other_active_admins = [u for u in active_admins if u["id"] != user_id]
            if len(other_active_admins) == 0:
                raise HTTPException(status_code=400, detail="Cannot demote or deactivate the last active administrator")

        updates = []
        params = []
        if req.role is not None:
            updates.append("role = %s")
            params.append(req.role)

        if req.is_active is not None:
            updates.append("is_active = %s")
            params.append(1 if req.is_active else 0)

        if updates:
            # Role/active-state changes invalidate previously issued sessions.
            updates.append("session_version = session_version + 1")
            updates.append("updated_at = NOW()")
            params.append(user_id)
            cur.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = %s", tuple(params))

        _commit_with_audit(
            conn, cur, "USER_UPDATED", admin["username"], admin["id"],
            {"target_user": target["username"], "target_id": user_id, "role": req.role, "is_active": req.is_active},
        )
        return {"status": "success", "message": f"User '{target['username']}' updated"}
    except HTTPException:
        if conn is not None and not getattr(conn, "_rolled_back", False):
            try:
                conn.rollback()
                setattr(conn, "_rolled_back", True)
            except Exception:
                pass
        raise
    except Exception:
        if conn is not None and not getattr(conn, "_rolled_back", False):
            try:
                conn.rollback()
                setattr(conn, "_rolled_back", True)
            except Exception:
                pass
        raise
    finally:
        if conn is not None:
            conn.close()


@router.delete("/users/{user_id}")
def delete_user(user_id: int, admin: dict = Depends(require_admin)):
    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="Cannot delete your own admin account")

    conn = _get_required_connection()
    try:
        cur = conn.cursor(dictionary=True)
        # Consistent serialization protocol: serialize deletion against the active-admin set.
        # Acquire locks on all active administrator rows with SELECT ... FOR UPDATE
        # in deterministic ascending ID order. Retain locks until commit/rollback.
        if _test_before_admin_lock_hook is not None:
            _test_before_admin_lock_hook(conn, cur, user_id)
        cur.execute(
            "SELECT id, username FROM users WHERE role = 'admin' AND is_active = 1 ORDER BY id FOR UPDATE"
        )
        if _test_after_admin_lock_hook is not None:
            _test_after_admin_lock_hook(conn, cur, user_id)
        active_admins = cur.fetchall() or []
        if _test_active_admin_filter is not None:
            active_admins = [u for u in active_admins if _test_active_admin_filter(u)]

        # Read the target state under lock
        cur.execute("SELECT id, username, role, is_active FROM users WHERE id = %s FOR UPDATE", (user_id,))
        target = cur.fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="User not found")

        # Last-admin protection: cannot delete an active admin if no other active admin exists.
        target_is_active_admin = (target.get("role") == "admin" and bool(target.get("is_active")))
        if target_is_active_admin:
            other_active_admins = [u for u in active_admins if u["id"] != user_id]
            if len(other_active_admins) == 0:
                raise HTTPException(status_code=400, detail="Cannot delete the last active administrator")

        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        _commit_with_audit(
            conn, cur, "USER_DELETED", admin["username"], admin["id"],
            {"deleted_user": target["username"], "deleted_id": user_id},
        )
        cur.close()
        return {"status": "success", "message": f"User '{target['username']}' deleted"}
    except HTTPException:
        if conn is not None and not getattr(conn, "_rolled_back", False):
            try:
                conn.rollback()
                setattr(conn, "_rolled_back", True)
            except Exception:
                pass
        raise
    except Exception:
        if conn is not None and not getattr(conn, "_rolled_back", False):
            try:
                conn.rollback()
                setattr(conn, "_rolled_back", True)
            except Exception:
                pass
        raise
    finally:
        if conn is not None:
            conn.close()
