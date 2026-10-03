"""Request dependencies: database session, signed-in user, project access."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ROLES, Project, ProjectMember, User
from .security import COOKIE_NAME, CSRF_HEADER, CSRF_VALUE


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


def csrf_guard(request: Request) -> None:
    """Mutating requests must carry a header that a cross-site form cannot set."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise HTTPException(403, "Missing request header")


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    signer = request.app.state.signer
    data = signer.read(request.cookies.get(COOKIE_NAME))
    if data is None:
        raise HTTPException(401, "Not signed in")
    user = db.get(User, data["uid"])
    if user is None or not user.is_active or not signer.matches(data, user.password_hash, user.session_version):
        raise HTTPException(401, "Not signed in")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "Administrator rights are required")
    return user


@dataclass
class Access:
    project: Project
    role: str
    user: User

    def can(self, role: str) -> bool:
        return ROLES.index(self.role) >= ROLES.index(role)


def project_access(min_role: str = "viewer"):
    """Dependency factory. A user who is not a member gets 404, so project ids do not leak."""

    def dep(pid: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Access:
        project = db.get(Project, pid)
        if project is None:
            raise HTTPException(404, "Project not found")
        if user.is_admin:
            role = "owner"
        else:
            member = db.execute(select(ProjectMember).where(
                ProjectMember.project_id == pid, ProjectMember.user_id == user.id)).scalar_one_or_none()
            if member is None:
                raise HTTPException(404, "Project not found")
            role = member.role
        access = Access(project=project, role=role, user=user)
        if not access.can(min_role):
            raise HTTPException(403, f"This needs the {min_role} role on the project")
        return access

    return dep
