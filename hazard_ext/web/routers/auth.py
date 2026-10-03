"""Sign-in, own password, and user administration."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..deps import csrf_guard, current_user, get_db, require_admin
from ..models import iso, User
from ..security import COOKIE_NAME, hash_password, password_problem, verify_password

router = APIRouter(prefix="/api", dependencies=[Depends(csrf_guard)])


def user_out(u: User) -> dict:
    return {"id": u.id, "email": u.email, "name": u.name, "is_admin": u.is_admin,
            "is_active": u.is_active, "created_at": iso(u.created_at)}


def norm_email(email: str) -> str:
    email = email.strip().lower()
    if "@" not in email or len(email) < 3 or " " in email:
        raise HTTPException(422, "Enter a valid email address")
    return email


class LoginIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=1000)


@router.post("/auth/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    throttle = request.app.state.throttle
    # Counted per account, and separately per address with a higher limit: the address can be
    # a shared proxy, and a forwarded address can be forged, so the account count is the real guard.
    addr_throttle = request.app.state.addr_throttle
    email_key = f"email:{email}"
    addr_key = f"addr:{request.client.host if request.client else ''}"
    if throttle.blocked(email_key) or addr_throttle.blocked(addr_key):
        raise HTTPException(429, "Too many failed attempts. Try again in a few minutes")
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    ok = verify_password(body.password, user.password_hash if user else None)
    if not ok or user is None or not user.is_active:
        throttle.fail(email_key)
        addr_throttle.fail(addr_key)
        raise HTTPException(401, "Email or password is incorrect")
    throttle.reset(email_key)
    settings = request.app.state.settings
    response.set_cookie(
        COOKIE_NAME, request.app.state.signer.make(user.id, user.password_hash, user.session_version),
        max_age=settings.session_hours * 3600, httponly=True, samesite="lax",
        secure=settings.cookie_secure, path="/")
    return user_out(user)


@router.post("/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    """Signs the user out everywhere: every cookie issued before now stops working."""
    signer = request.app.state.signer
    data = signer.read(request.cookies.get(COOKIE_NAME))
    if data is not None:
        user = db.get(User, data["uid"])
        if user is not None and signer.matches(data, user.password_hash, user.session_version):
            user.session_version += 1
            db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/auth/me")
def me(user: User = Depends(current_user)):
    return user_out(user)


class PasswordIn(BaseModel):
    current: str = Field(max_length=1000)
    new: str = Field(max_length=1000)


@router.post("/auth/password")
def change_password(body: PasswordIn, request: Request, response: Response,
                    user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not verify_password(body.current, user.password_hash):
        raise HTTPException(400, "The current password is incorrect")
    problem = password_problem(body.new)
    if problem:
        raise HTTPException(422, problem)
    user = db.get(User, user.id)
    user.password_hash = hash_password(body.new)
    db.commit()
    settings = request.app.state.settings
    response.set_cookie(                        # keep this browser signed in; others are signed out
        COOKIE_NAME, request.app.state.signer.make(user.id, user.password_hash, user.session_version),
        max_age=settings.session_hours * 3600, httponly=True, samesite="lax",
        secure=settings.cookie_secure, path="/")
    return {"ok": True}


# ------------------------------------------------------------------ administration
class UserCreate(BaseModel):
    email: str = Field(max_length=320)
    name: str = Field("", max_length=200)
    password: str = Field(max_length=1000)
    is_admin: bool = False


class UserPatch(BaseModel):
    name: Optional[str] = Field(None, max_length=200)
    is_admin: Optional[bool] = None
    is_active: Optional[bool] = None
    password: Optional[str] = Field(None, max_length=1000, description="Set to reset the user's password")


@router.get("/admin/users")
def list_users(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [user_out(u) for u in db.execute(select(User).order_by(User.email)).scalars()]


@router.post("/admin/users", status_code=201)
def create_user(body: UserCreate, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    email = norm_email(body.email)
    problem = password_problem(body.password)
    if problem:
        raise HTTPException(422, problem)
    if db.execute(select(User).where(User.email == email)).scalar_one_or_none():
        raise HTTPException(409, "A user with that email already exists")
    user = User(email=email, name=body.name.strip(), password_hash=hash_password(body.password),
                is_admin=body.is_admin)
    db.add(user)
    db.commit()
    return user_out(user)


@router.patch("/admin/users/{uid}")
def patch_user(uid: int, body: UserPatch, admin: User = Depends(require_admin),
               db: Session = Depends(get_db)):
    user = db.get(User, uid)
    if user is None:
        raise HTTPException(404, "User not found")
    removing_admin = (body.is_admin is False or body.is_active is False) and user.is_admin and user.is_active
    if removing_admin:
        others = db.execute(select(func.count()).select_from(User).where(
            User.is_admin.is_(True), User.is_active.is_(True), User.id != user.id)).scalar_one()
        if others == 0:
            raise HTTPException(400, "This is the only active administrator")
    if body.name is not None:
        user.name = body.name.strip()
    if body.is_admin is not None:
        user.is_admin = body.is_admin
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.password is not None:
        problem = password_problem(body.password)
        if problem:
            raise HTTPException(422, problem)
        user.password_hash = hash_password(body.password)
    db.commit()
    return user_out(user)
