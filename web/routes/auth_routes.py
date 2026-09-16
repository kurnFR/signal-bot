"""
Authentication & User Management API Router.
Strictly parameterized SQL queries throughout to eliminate SQL injection vectors.
"""
import re
import logging
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends, status
from pydantic import BaseModel, Field

from db.db import get_pool
from web.auth import (
    hash_password, verify_password, create_access_token,
    get_current_user, require_admin, revoke_token,
    check_login_rate_limit, record_failed_login, reset_login_attempts,
    log_audit, _token_from_request
)
from fastapi import Request

router = APIRouter(prefix="/api", tags=["authentication-and-users"])
logger = logging.getLogger("web.auth_routes")

USERNAME_REGEX = re.compile(r"^[a-zA-Z0-9_-]{3,30}$")


# ============================================================================
# Request / Response Schemas
# ============================================================================
class LoginRequest(BaseModel):
    username: str = Field(..., example="admin")
    password: str = Field(..., example="admin123")


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(...)
    new_password: str = Field(..., min_length=6)


class CreateUserRequest(BaseModel):
    username: str = Field(...)
    email: Optional[str] = Field(None)
    password: str = Field(..., min_length=6)
    role: str = Field("trader")  # admin, trader, viewer


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(..., min_length=6)


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
    rate_key = f"{client_ip}:{username}"
    if not check_login_rate_limit(rate_key):
        log_audit("LOGIN_RATE_LIMITED", username, None, {"ip": client_ip}, client_ip)
        raise HTTPException(
            status_code=429,
            detail="Too many failed login attempts. Account temporarily locked. Please try again in 5 minutes."
        )

    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        # 100% Parameterized query: immune to SQL injection
        cur.execute(
            """
            SELECT id, username, email, password_hash, role, is_active, must_change_password 
            FROM users 
            WHERE username = %s
            """,
            (username,)
        )
        user = cur.fetchone()
        cur.close()

        if not user or not user["is_active"]:
            failed_count = record_failed_login(rate_key)
            log_audit("LOGIN_FAILED", username, None, {"attempt": failed_count, "reason": "user_not_found_or_inactive"}, client_ip)
            raise HTTPException(status_code=401, detail="Invalid username or password")

        if not verify_password(req.password, user["password_hash"]):
            failed_count = record_failed_login(rate_key)
            log_audit("LOGIN_FAILED", username, user["id"], {"attempt": failed_count, "reason": "bad_password"}, client_ip)
            raise HTTPException(status_code=401, detail="Invalid username or password")

        reset_login_attempts(rate_key)
        token = create_access_token(user["id"], user["username"], user["role"])
        log_audit("LOGIN_SUCCESS", user["username"], user["id"], {"role": user["role"]}, client_ip)

        return {
            "token": token,
            "user": {
                "id": user["id"],
                "username": user["username"],
                "email": user["email"],
                "role": user["role"],
                "must_change_password": bool(user.get("must_change_password", 0)),
            }
        }
    finally:
        conn.close()


@router.post("/auth/logout")
def logout(request: Request, current_user: dict = Depends(get_current_user)):
    token = _token_from_request(request)
    if token:
        revoke_token(token)
    client_ip = request.client.host if request.client else "unknown"
    log_audit("LOGOUT", current_user["username"], current_user["id"], None, client_ip)
    return {"status": "success", "message": "Successfully logged out"}


@router.get("/auth/me")
def get_current_user_profile(current_user: dict = Depends(get_current_user)):
    return {
        "id": current_user["id"],
        "username": current_user["username"],
        "email": current_user["email"],
        "role": current_user["role"],
        "must_change_password": bool(current_user.get("must_change_password", 0)),
    }


@router.post("/auth/change-password")
def change_password(req: ChangePasswordRequest, request: Request, current_user: dict = Depends(get_current_user)):
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        # Fetch current hash using parameterized query
        cur.execute("SELECT password_hash FROM users WHERE id = %s", (current_user["id"],))
        row = cur.fetchone()
        if not row or not verify_password(req.old_password, row["password_hash"]):
            raise HTTPException(status_code=400, detail="Current password does not match")

        new_hash = hash_password(req.new_password)
        cur.execute(
            "UPDATE users SET password_hash = %s, must_change_password = 0, updated_at = NOW() WHERE id = %s",
            (new_hash, current_user["id"])
        )
        conn.commit()
        cur.close()
        client_ip = request.client.host if request.client else "unknown"
        log_audit("PASSWORD_CHANGED", current_user["username"], current_user["id"], None, client_ip)
        return {"status": "success", "message": "Password changed successfully"}
    finally:
        conn.close()


@router.get("/auth/audit-logs")
def get_audit_logs(limit: int = 50, current_user: dict = Depends(require_admin)):
    conn = get_pool().get_connection()
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
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT id, username, email, role, is_active, created_at, updated_at
            FROM users
            ORDER BY id ASC
            """
        )
        users = cur.fetchall()
        cur.close()
        for u in users:
            u["is_active"] = bool(u["is_active"])
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
    new_hash = hash_password(req.password)

    conn = get_pool().get_connection()
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
        conn.commit()
        new_id = cur.lastrowid
        cur.close()

        log_audit("USER_CREATED", admin["username"], admin["id"], {"created_user": clean_username, "role": clean_role})

        return {
            "status": "success",
            "user": {
                "id": new_id,
                "username": clean_username,
                "email": clean_email,
                "role": clean_role,
                "is_active": True,
            }
        }
    finally:
        conn.close()


@router.post("/users/{user_id}/reset-password")
def reset_user_password(user_id: int, req: ResetPasswordRequest, admin: dict = Depends(require_admin)):
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id, username FROM users WHERE id = %s", (user_id,))
        target = cur.fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="Target user not found")

        new_hash = hash_password(req.new_password)
        cur.execute(
            "UPDATE users SET password_hash = %s, must_change_password = 1, updated_at = NOW() WHERE id = %s",
            (new_hash, user_id)
        )
        conn.commit()
        cur.close()

        log_audit("USER_PASSWORD_RESET", admin["username"], admin["id"], {"target_user": target["username"], "target_id": user_id})

        return {
            "status": "success",
            "message": f"Password reset successfully for user '{target['username']}'"
        }
    finally:
        conn.close()


@router.put("/users/{user_id}")
def update_user(user_id: int, req: UpdateUserRequest, admin: dict = Depends(require_admin)):
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id, username, role, is_active FROM users WHERE id = %s", (user_id,))
        target = cur.fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="Target user not found")

        updates = []
        params = []
        if req.role is not None:
            if req.role not in ("admin", "trader", "viewer"):
                raise HTTPException(status_code=400, detail="Invalid role")
            updates.append("role = %s")
            params.append(req.role)

        if req.is_active is not None:
            # Prevent admin deactivating self
            if target["id"] == admin["id"] and not req.is_active:
                raise HTTPException(status_code=400, detail="Cannot deactivate your own admin account")
            updates.append("is_active = %s")
            params.append(1 if req.is_active else 0)

        if updates:
            updates.append("updated_at = NOW()")
            params.append(user_id)
            cur.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = %s", tuple(params))
            conn.commit()

        cur.close()
        log_audit("USER_UPDATED", admin["username"], admin["id"], {"target_user": target["username"], "role": req.role, "is_active": req.is_active})
        return {"status": "success", "message": f"User '{target['username']}' updated"}
    finally:
        conn.close()


@router.delete("/users/{user_id}")
def delete_user(user_id: int, admin: dict = Depends(require_admin)):
    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="Cannot delete your own admin account")

    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT username FROM users WHERE id = %s", (user_id,))
        target = cur.fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="User not found")

        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.commit()
        cur.close()
        return {"status": "success", "message": f"User '{target['username']}' deleted"}
    finally:
        conn.close()
