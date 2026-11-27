from pathlib import Path
from typing import Literal, TypeVar

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class ConfigError(Exception):
    pass


class SafeZone(BaseModel):
    model_config = ConfigDict(extra="forbid")
    top: float = Field(0.15, ge=0, lt=1)
    bottom: float = Field(0.25, ge=0, lt=1)
    right: float = Field(0.15, ge=0, lt=1)


class Logo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: Path
    position: Literal["top-left", "top-right", "bottom-left", "bottom-right"] = "top-right"
    safe_zone: SafeZone = Field(default_factory=SafeZone)
    opacity: float = Field(1.0, ge=0, le=1)


class TargetAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform: Literal["youtube", "instagram", "tiktok"]
    account_label: str = Field(min_length=1)
    device_id: str | None = None


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    per_day: int = Field(3, ge=1)
    min_gap_minutes: int = Field(60, ge=0)
    max_claude_requests_per_source: int = Field(10, ge=1)


class Clip(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_s: float = Field(30, gt=0)
    max_s: float = Field(55, gt=0)
    min_score: int = Field(70, ge=0, le=100)
    max_clips_per_source: int = Field(3, ge=1)

    @model_validator(mode="after")
    def _range(self):
        if self.min_s >= self.max_s:
            raise ValueError("min_s должен быть меньше max_s")
        return self


class Campaign(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    name: str = Field(min_length=1)
    language: str = "auto"
    mode: Literal["clip_source", "logo_overlay"]
    sources: list[Path] = Field(min_length=1)
    logo: Logo | None = None
    required_hashtags: list[str] = []
    required_mentions: list[str] = []
    target_accounts: list[TargetAccount] = []
    limits: Limits = Field(default_factory=Limits)
    clip: Clip = Field(default_factory=Clip)
    subtitle_preset: Literal["classic", "bold", "karaoke"] = "classic"
    output_dir: Path = Path("output")

    @model_validator(mode="after")
    def _logo_required(self):
        if self.mode == "logo_overlay" and self.logo is None:
            raise ValueError("для mode=logo_overlay нужен блок logo")
        return self


ANDROID_STEPS = ("create", "gallery", "pick", "next", "description", "publish")


class AndroidApp(BaseModel):
    """Android UI route for a platform without an official upload API. Selectors are uiautomator2 kwargs."""
    model_config = ConfigDict(extra="forbid")
    package: str = Field(min_length=1)
    user: int = Field(0, ge=0)
    selectors: dict[str, dict[str, str | int | bool]]

    @model_validator(mode="after")
    def _steps(self):
        missing = [s for s in ANDROID_STEPS if s not in self.selectors]
        if missing:
            raise ValueError(f"в selectors не хватает шагов: {', '.join(missing)}")
        return self


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    whisper_model: Literal["tiny", "base", "small", "medium", "large-v3"] = "small"
    prefilter_top_n: int = Field(40, ge=1)
    ffmpeg_path: str = "ffmpeg"
    adb_path: str = "adb"
    claude_daily_limit: int = Field(200, ge=1)
    android_apps: dict[Literal["youtube", "instagram", "tiktok"], AndroidApp] = {}


M = TypeVar("M", bound=BaseModel)


def _load(model: type[M], path: Path, title: str) -> M:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise ConfigError(f"{path}: не удалось прочитать файл: {e}") from e
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: ошибка YAML: {e}") from e
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: корень файла должен быть словарём полей")
    try:
        return model.model_validate(raw)
    except ValidationError as e:
        lines = ["  " + line for line in validation_lines(e)]
        raise ConfigError(f"{path}: ошибки в {title}:\n" + "\n".join(lines)) from e


def load_campaign(path: Path) -> Campaign:
    return _load(Campaign, path, "конфиге кампании")


def load_settings(path: Path = Path("config.yaml")) -> Settings:
    if not path.exists():
        return Settings()
    return _load(Settings, path, "настройках")


def save_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def validation_lines(e: ValidationError) -> list[str]:
    return [f"поле '{'.'.join(map(str, err['loc'])) or '<корень>'}': {err['msg']}" for err in e.errors()]
