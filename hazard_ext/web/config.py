"""Application settings, read from environment variables or a .env file."""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database. SQLite for local use; a Supabase Postgres connection string when hosted.
    database_url: str = "sqlite:///./data/app.db"
    db_schema: str = "hazard"
    auto_create_schema: bool = True

    # Where parsed datasets are kept: a local folder, the database, or Supabase Storage.
    storage_backend: Literal["local", "db", "supabase"] = "local"
    local_data_dir: str = "./data/blobs"
    supabase_url: Optional[str] = None
    supabase_service_key: Optional[str] = None
    supabase_bucket: str = "hazard-data"

    # Sessions
    session_secret: str = "dev-only-change-me"
    session_hours: int = 12
    cookie_secure: bool = False

    # First administrator, created at start-up when no user exists yet
    admin_email: Optional[str] = None
    admin_password: Optional[str] = None
    admin_name: str = "Administrator"

    max_upload_mb: int = 200
    api_docs: bool = False          # serve /api/docs (interactive API reference)

    # The assistant (natural-language changes to assumptions). Off when no key is set.
    anthropic_api_key: Optional[str] = None
    agent_model: str = "claude-opus-5-5"

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def sqlalchemy_url(self) -> str:
        url = self.database_url
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        return url

    def fatal_problems(self) -> list[str]:
        """Configuration the app refuses to start with."""
        out = []
        if not self.is_sqlite and (self.session_secret == "dev-only-change-me" or len(self.session_secret) < 32):
            out.append("SESSION_SECRET must be set to a random value of at least 32 characters")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", self.db_schema):
            out.append("DB_SCHEMA must be a plain identifier")
        return out

    def problems(self) -> list[str]:
        """Configuration that is unsafe or incomplete for a hosted deployment."""
        out = []
        if not self.is_sqlite and not self.cookie_secure:
            out.append("COOKIE_SECURE should be true when served over HTTPS")
        if self.storage_backend == "supabase" and not (self.supabase_url and self.supabase_service_key):
            out.append("STORAGE_BACKEND=supabase needs SUPABASE_URL and SUPABASE_SERVICE_KEY")
        return out
