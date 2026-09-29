from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "file-text-parser"
    env: str = "development"
    # ⚠ debug=True 会让 uvicorn 开 reload：reloader 监视文件变化 → 杀掉 serve 子进程。
    # worker 是子进程内的 daemon 线程，随之被杀 → 任务永久卡在 split（已踩坑两次）。
    # 故默认 False；确实要热重载时用 .env 显式开，并接受任务会被打断。
    debug: bool = False

    host: str = "0.0.0.0"
    port: int = 8000

    api_prefix: str = "/api"
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    storage_dir: Path = BASE_DIR / "storage"
    upload_dir: Path = BASE_DIR / "storage" / "uploads"
    db_config_file: Path = BASE_DIR / "storage" / "database.json"
    max_upload_size_mb: int = 50
    keep_uploaded_files: bool = False

    ocr_lang: str = "chi_sim+eng"
    tesseract_cmd: str | None = None
    ocr_dpi: int = 300
    pdf_ocr_fallback: bool = True

    # OCR-to-Text 工具（PP-OCRv6 onnx）
    ocr_model_dir: Path = BASE_DIR / "models"
    ocr_default_tier: str = "small"

    # 流水线 worker（与后端同进程后台消费队列）
    worker_enabled: bool = True
    worker_idle_seconds: float = 3.0
    worker_shutdown_timeout: float = 10.0

    @property
    def upload_dir_resolved(self) -> Path:
        return Path(self.upload_dir)

    def ensure_dirs(self) -> None:
        self.upload_dir_resolved.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
