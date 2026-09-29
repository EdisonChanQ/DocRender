"""建表迁移器。

表结构来源：app/db/tables/ 注册中心（每张表一个 .py 文件，定义物理表名 + DDL）。
DDL 中的 ${TABLE_NAME} 由本模块注入为 TableDef.name，按 GO 分批执行。
DDL 自身以 IF NOT EXISTS 保证幂等，可重复执行；单个表失败不影响其它表。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import text

from app.db.config_store import config_store
from app.db.manager import database_manager
from app.db.tables import TABLES
from app.db.tables.base import TableDef

TABLE_TOKEN = "${TABLE_NAME}"
_GO_PATTERN = re.compile(r"(?mi)^\s*GO\s*$")


@dataclass
class MigrationResult:
    key: str
    table: str
    ok: bool
    message: str = ""


def table_name_of(key: str) -> str:
    """按逻辑名取物理表名；未定义的表直接报错，避免拼写错误静默通过。"""
    try:
        return TABLES[key].name
    except KeyError:
        raise KeyError(f"未注册的表：{key}（可用：{', '.join(TABLES)}）") from None


def split_batches(script_sql: str) -> list[str]:
    """按 GO 批次分隔符拆分为可单独执行的语句批次。"""
    return [batch.strip() for batch in _GO_PATTERN.split(script_sql) if batch.strip()]


def render_table(table: TableDef) -> str:
    """把 DDL 中的 ${TABLE_NAME} 替换为注册的物理表名。"""
    return table.ddl.replace(TABLE_TOKEN, table.name)


def apply_tables(keys: list[str] | None = None) -> list[MigrationResult]:
    """执行所有（或指定逻辑名的）建表 DDL，每个表独立事务提交。"""
    if keys is None:
        targets = list(TABLES.values())
    else:
        unknown = [k for k in keys if k not in TABLES]
        if unknown:
            raise KeyError(f"未注册的表：{', '.join(unknown)}（可用：{', '.join(TABLES)}）")
        targets = [TABLES[k] for k in keys]

    saved = config_store.load()
    if saved is None:
        raise RuntimeError("尚未配置数据库连接，无法执行建表脚本")

    engine = database_manager.create_engine(saved)
    results: list[MigrationResult] = []
    try:
        for table in targets:
            sql = render_table(table)
            try:
                with engine.begin() as conn:
                    for batch in split_batches(sql):
                        conn.execute(text(batch))
                results.append(MigrationResult(key=table.key, table=table.name, ok=True))
            except Exception as exc:  # noqa: BLE001 - 逐表收集错误并继续
                results.append(
                    MigrationResult(key=table.key, table=table.name, ok=False, message=str(exc))
                )
    finally:
        database_manager.dispose()

    return results
