import json
from pathlib import Path

from app.config import settings
from app.db.schemas import DatabaseConfig


class DatabaseConfigStore:
    """Persists the database connection settings in a local JSON file.

    The connection settings cannot live inside the database they describe,
    so they are kept next to the application and excluded from version control.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path or settings.db_config_file)

    @property
    def path(self) -> Path:
        return self._path

    def exists(self) -> bool:
        return self._path.exists()

    def load(self) -> DatabaseConfig | None:
        if not self._path.exists():
            return None
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        try:
            return DatabaseConfig.model_validate(raw)
        except Exception:
            return None

    def save(self, config: DatabaseConfig) -> DatabaseConfig:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(config.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return config

    def delete(self) -> None:
        self._path.unlink(missing_ok=True)


config_store = DatabaseConfigStore()
