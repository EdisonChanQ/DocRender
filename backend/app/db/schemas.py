from enum import Enum

from pydantic import BaseModel, Field

DEFAULT_PORTS: dict[str, int] = {
    "mysql": 3306,
    "postgresql": 5432,
    "sqlserver": 1433,
}

DEFAULT_SQLSERVER_DRIVER = "ODBC Driver 17 for SQL Server"


class DatabaseType(str, Enum):
    SQLITE = "sqlite"
    MYSQL = "mysql"
    POSTGRESQL = "postgresql"
    SQLSERVER = "sqlserver"


class DatabaseConfig(BaseModel):
    type: DatabaseType = DatabaseType.SQLITE
    host: str = "127.0.0.1"
    port: int | None = None
    database: str = "storage/app.db"
    username: str = ""
    password: str = ""
    charset: str = "utf8mb4"
    driver: str | None = None


class DatabaseConfigUpdate(BaseModel):
    type: DatabaseType = DatabaseType.SQLITE
    host: str = "127.0.0.1"
    port: int | None = None
    database: str = "storage/app.db"
    username: str = ""
    password: str | None = Field(
        default=None,
        description="留空或省略表示沿用已保存的密码",
    )
    charset: str = "utf8mb4"
    driver: str | None = None


class DatabaseConfigPublic(BaseModel):
    configured: bool
    type: DatabaseType
    host: str
    port: int | None
    database: str
    username: str
    password_set: bool
    charset: str
    driver: str | None
    config_file: str


class DatabaseTestResult(BaseModel):
    success: bool
    message: str
    dialect: str | None = None
    server_version: str | None = None
    elapsed_ms: int | None = None


class DatabaseStatus(BaseModel):
    configured: bool
    reachable: bool
    message: str
    dialect: str | None = None
    server_version: str | None = None
    config_file: str
