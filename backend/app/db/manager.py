import time
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, URL

from app.config import BASE_DIR
from app.db.config_store import config_store
from app.db.schemas import (
    DEFAULT_PORTS,
    DEFAULT_SQLSERVER_DRIVER,
    DatabaseConfig,
    DatabaseStatus,
    DatabaseTestResult,
)

DRIVER_NAME: dict[str, str] = {
    "sqlite": "sqlite",
    "mysql": "mysql+pymysql",
    "postgresql": "postgresql+psycopg2",
    "sqlserver": "mssql+pyodbc",
}

VERSION_QUERY: dict[str, str] = {
    "sqlite": "SELECT sqlite_version()",
    "mysql": "SELECT VERSION()",
    "postgresql": "SHOW server_version",
    "sqlserver": "SELECT @@VERSION",
}


class DatabaseManager:
    """Builds a SQLAlchemy engine from the stored configuration and tests it."""

    def __init__(self) -> None:
        self._engine: Engine | None = None

    def resolved_port(self, config: DatabaseConfig) -> int | None:
        if config.type.value == "sqlite":
            return None
        return config.port or DEFAULT_PORTS.get(config.type.value)

    def resolved_database(self, config: DatabaseConfig) -> str:
        if config.type.value != "sqlite":
            return config.database
        path = Path(config.database)
        if not path.is_absolute():
            path = BASE_DIR / path
        return str(path)

    def build_url(self, config: DatabaseConfig) -> URL:
        drivername = DRIVER_NAME[config.type.value]
        if config.type.value == "sqlite":
            return URL.create(drivername, database=self.resolved_database(config))

        query: dict[str, str] = {}
        if config.type.value == "mysql":
            query["charset"] = config.charset or "utf8mb4"
        elif config.type.value == "sqlserver":
            query["driver"] = config.driver or DEFAULT_SQLSERVER_DRIVER
            query["TrustServerCertificate"] = "yes"

        return URL.create(
            drivername,
            username=config.username or None,
            password=config.password or None,
            host=config.host or None,
            port=self.resolved_port(config),
            database=config.database or None,
            query=query,
        )

    def create_engine(self, config: DatabaseConfig, timeout: int = 5) -> Engine:
        connect_args: dict[str, object] = {}
        pool_kwargs: dict[str, object] = {}
        if config.type.value == "sqlite":
            connect_args["check_same_thread"] = False
        elif config.type.value == "sqlserver":
            connect_args["timeout"] = timeout
            # 队列 worker 多线程并发（claim 拆事务后单请求顺序消耗多个连接），
            # SQLAlchemy 默认 5+10 偏小；瞬时涌开会打爆老服务器（2008R2 出现过
            # 08001 预登录握手被重置），预建池 + 提高上限平滑连接建立。
            pool_kwargs = {"pool_size": 10, "max_overflow": 20, "pool_timeout": 30}
        else:
            connect_args["connect_timeout"] = timeout

        return create_engine(
            self.build_url(config),
            pool_pre_ping=True,
            connect_args=connect_args,
            **pool_kwargs,
        )

    def test(self, config: DatabaseConfig, timeout: int = 5) -> DatabaseTestResult:
        started = time.perf_counter()
        try:
            engine = self.create_engine(config, timeout=timeout)
        except Exception as exc:
            return DatabaseTestResult(success=False, message=self._describe(exc, config))

        try:
            with engine.connect() as connection:
                version = self._server_version(connection, config)
            return DatabaseTestResult(
                success=True,
                message="连接成功",
                dialect=engine.dialect.name,
                server_version=version,
                elapsed_ms=self._elapsed_ms(started),
            )
        except Exception as exc:
            return DatabaseTestResult(
                success=False,
                message=self._describe(exc, config),
                dialect=config.type.value,
                elapsed_ms=self._elapsed_ms(started),
            )
        finally:
            engine.dispose()

    def status(self, config: DatabaseConfig | None) -> DatabaseStatus:
        config_file = str(config_store.path)
        if config is None:
            return DatabaseStatus(
                configured=False,
                reachable=False,
                message="尚未配置数据库连接",
                config_file=config_file,
            )

        result = self.test(config, timeout=3)
        return DatabaseStatus(
            configured=True,
            reachable=result.success,
            message=result.message,
            dialect=result.dialect,
            server_version=result.server_version,
            config_file=config_file,
        )

    def connect(self, config: DatabaseConfig) -> Engine:
        self.dispose()
        self._engine = self.create_engine(config)
        return self._engine

    @property
    def engine(self) -> Engine | None:
        return self._engine

    def dispose(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None

    def _server_version(self, connection, config: DatabaseConfig) -> str | None:
        query = VERSION_QUERY.get(config.type.value)
        if not query:
            return None
        try:
            value = connection.execute(text(query)).scalar()
        except Exception:
            return None
        if value is None:
            return None
        return str(value).strip().splitlines()[0][:120]

    def _elapsed_ms(self, started: float) -> int:
        return int((time.perf_counter() - started) * 1000)

    def _describe(self, exc: Exception, config: DatabaseConfig) -> str:
        message = f"{exc.__class__.__name__}: {exc}"
        if config.password:
            message = message.replace(config.password, "***")
        return message


database_manager = DatabaseManager()
