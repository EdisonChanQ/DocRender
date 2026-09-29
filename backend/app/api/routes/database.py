from fastapi import APIRouter

from app.db.config_store import config_store
from app.db.manager import database_manager
from app.db.schemas import (
    DEFAULT_PORTS,
    DEFAULT_SQLSERVER_DRIVER,
    DatabaseConfig,
    DatabaseConfigPublic,
    DatabaseConfigUpdate,
    DatabaseStatus,
    DatabaseTestResult,
    DatabaseType,
)

router = APIRouter()

TYPE_LABELS: dict[str, str] = {
    "sqlite": "SQLite",
    "mysql": "MySQL",
    "postgresql": "PostgreSQL",
    "sqlserver": "SQL Server",
}


def _to_public(config: DatabaseConfig | None) -> DatabaseConfigPublic:
    if config is None:
        default = DatabaseConfig()
        return DatabaseConfigPublic(
            configured=False,
            type=default.type,
            host=default.host,
            port=default.port,
            database=default.database,
            username=default.username,
            password_set=False,
            charset=default.charset,
            driver=default.driver,
            config_file=str(config_store.path),
        )

    return DatabaseConfigPublic(
        configured=True,
        type=config.type,
        host=config.host,
        port=config.port,
        database=config.database,
        username=config.username,
        password_set=bool(config.password),
        charset=config.charset,
        driver=config.driver,
        config_file=str(config_store.path),
    )


def _build_config(payload: DatabaseConfigUpdate, saved: DatabaseConfig | None) -> DatabaseConfig:
    password = payload.password or (saved.password if saved else "")
    return DatabaseConfig(
        type=payload.type,
        host=payload.host,
        port=payload.port,
        database=payload.database,
        username=payload.username,
        password=password,
        charset=payload.charset,
        driver=payload.driver,
    )


@router.get("/types")
def list_types() -> list[dict]:
    return [
        {
            "value": member.value,
            "label": TYPE_LABELS.get(member.value, member.value),
            "default_port": DEFAULT_PORTS.get(member.value),
            "default_driver": DEFAULT_SQLSERVER_DRIVER if member is DatabaseType.SQLSERVER else None,
        }
        for member in DatabaseType
    ]


@router.get("/config", response_model=DatabaseConfigPublic)
def get_config() -> DatabaseConfigPublic:
    return _to_public(config_store.load())


@router.put("/config", response_model=DatabaseConfigPublic)
def update_config(payload: DatabaseConfigUpdate) -> DatabaseConfigPublic:
    saved = config_store.load()
    config = _build_config(payload, saved)
    config_store.save(config)

    database_manager.dispose()
    try:
        database_manager.connect(config)
    except Exception:
        database_manager.dispose()

    return _to_public(config)


@router.post("/test", response_model=DatabaseTestResult)
def test_connection(payload: DatabaseConfigUpdate | None = None) -> DatabaseTestResult:
    saved = config_store.load()
    if payload is None:
        if saved is None:
            return DatabaseTestResult(success=False, message="尚未配置数据库连接")
        config = saved
    else:
        config = _build_config(payload, saved)

    return database_manager.test(config)


@router.get("/status", response_model=DatabaseStatus)
def get_status() -> DatabaseStatus:
    return database_manager.status(config_store.load())
