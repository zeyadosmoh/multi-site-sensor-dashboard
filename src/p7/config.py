"""Settings loaded from environment / .env."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_user: str = "p7"
    postgres_password: str = ""
    postgres_db: str = "p7"
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5437

    app_host: str = "127.0.0.1"
    app_port: int = 8790
    session_secret: str = ""
    cookie_secure: bool = False

    admin_username: str = "admin"
    admin_password: str = ""

    data_dir: Path = Path("data")
    gen_seed: int = 7

    reports_dir: Path = Path("reports")
    max_upload_mb: int = 50
    # Hourly readings: each reading stands for this many minutes when counting time out of range.
    reading_interval_minutes: int = 60
    # Generate PDFs in the background after each upload (tests switch this off).
    reports_after_upload: bool = True

    def dsn(self, dbname: str | None = None) -> str:
        return (
            f"host={self.postgres_host} port={self.postgres_port} "
            f"dbname={dbname or self.postgres_db} user={self.postgres_user} "
            f"password={self.postgres_password}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
