"""환경변수 로딩. 비밀값은 .env에서만 읽는다."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    db_url: str = Field(
        default="postgresql+psycopg://stocker:stocker@localhost:5432/stocker",
        validation_alias="STOCKER_DB_URL",
    )
    fred_api_key: str | None = Field(default=None, validation_alias="FRED_API_KEY")
    data_dir: Path = Field(default=Path("./data"), validation_alias="STOCKER_DATA_DIR")

    @property
    def fred_key(self) -> str | None:
        """빈 문자열은 키 없음으로 취급."""
        return self.fred_api_key or None


settings = Settings()
