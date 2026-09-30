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

    # LLM-to-Text 工具（多模态大模型识别图片文字，OpenAI 兼容接口）
    # 空 api_key 表示未配置，调用时返回明确错误而非静默降级。
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = "gpt-4o"
    llm_timeout_seconds: float = 60.0
    # worker 提取兜底：OCR 平均置信度低于阈值时，裁原页块送 LLM 重识别。
    # 需 llm_api_key 已配置才生效；关闭则纯 OCR/QR。
    llm_fallback_enabled: bool = True
    llm_confidence_threshold: float = 0.8

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
