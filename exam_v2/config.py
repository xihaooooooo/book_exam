"""重写版应用配置。

本模块集中管理路径和环境变量覆盖项。导入时不创建目录，
由调用方决定何时初始化运行所需目录。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LLM_PROVIDER = "deepseek"
DEFAULT_DEEP_MODEL = "deepseek-v4-flash"
DEFAULT_QUICK_MODEL = "deepseek-v4-flash"


@dataclass(frozen=True, slots=True)
class AppConfig:
    """存储、Agent 和 Web API 共享的运行配置。"""

    project_root: Path = PROJECT_ROOT
    cache_dir: Path = PROJECT_ROOT / "cache"
    output_dir: Path = PROJECT_ROOT / "output"
    uploads_dir: Path = PROJECT_ROOT / "uploads"
    default_book_id: str = "default"
    llm_provider: str = DEFAULT_LLM_PROVIDER
    deep_model: str = DEFAULT_DEEP_MODEL
    quick_model: str = DEFAULT_QUICK_MODEL
    temperature: float | None = None

    @property
    def sections_db(self) -> Path:
        return self.cache_dir / "sections.db"

    @property
    def attempts_db(self) -> Path:
        return self.cache_dir / "attempts.db"

    def ensure_directories(self) -> None:
        for path in (self.cache_dir, self.output_dir, self.uploads_dir):
            path.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "AppConfig":
        env = env or os.environ
        return cls(
            llm_provider=env.get("BOOKTOEXAM_LLM_PROVIDER", DEFAULT_LLM_PROVIDER),
            deep_model=env.get("BOOKTOEXAM_DEEP_THINK_LLM", DEFAULT_DEEP_MODEL),
            quick_model=env.get("BOOKTOEXAM_QUICK_THINK_LLM", DEFAULT_QUICK_MODEL),
            temperature=_optional_float(env.get("BOOKTOEXAM_TEMPERATURE")),
        )


def _optional_float(raw: str | None) -> float | None:
    if raw is None or raw.strip() == "":
        return None
    return float(raw)
