from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import column, delete, func, select, table as sa_table, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from app.db.config_store import config_store
from app.db.manager import database_manager
from app.db.tables import TABLES
from app.schemas.path_config import (
    PathConfigCreate,
    PathConfigPage,
    PathConfigPublic,
    PathConfigUpdate,
)

# 表名统一定义在 app/db/tables/path_config.py
PATH_CONFIG_TABLE = TABLES["path_config"].name

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_]+$")
# code 作为配置键使用，限制为字母/数字/下划线/中划线，避免空格与特殊字符引起歧义
_CODE_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")

_t = sa_table(
    PATH_CONFIG_TABLE,
    column("id"), column("code"), column("name"), column("path"),
    column("is_enabled"), column("remark"), column("created_by"),
    column("created_datetime"), column("updated_by"), column("updated_datetime"),
)


class PathConfigError(Exception):
    """路径配置业务操作基类异常。"""


class DatabaseNotConfiguredError(PathConfigError):
    pass


class PathConfigNotFoundError(PathConfigError):
    pass


class PathConfigCodeError(PathConfigError):
    pass


def _check_table() -> None:
    if not _IDENTIFIER_RE.match(PATH_CONFIG_TABLE):
        raise PathConfigError(f"表名配置非法：{PATH_CONFIG_TABLE}")


def get_engine() -> Engine:
    engine = database_manager.engine
    if engine is not None:
        return engine
    saved = config_store.load()
    if saved is None:
        raise DatabaseNotConfiguredError("尚未配置数据库连接，请先在数据库配置页面完成设置")
    return database_manager.connect(saved)


def _row_to_public(row) -> PathConfigPublic:
    return PathConfigPublic(
        id=row["id"],
        code=row["code"],
        name=row["name"],
        path=row["path"],
        is_enabled=bool(row["is_enabled"]),
        remark=row["remark"],
        created_by=row["created_by"],
        created_datetime=row["created_datetime"],
        updated_by=row["updated_by"],
        updated_datetime=row["updated_datetime"],
    )


def _fetch_by_id(conn, path_id: int) -> PathConfigPublic | None:
    row = conn.execute(select(_t).where(_t.c.id == path_id)).mappings().first()
    return _row_to_public(row) if row is not None else None


def _fetch_by_code(conn, code: str) -> PathConfigPublic | None:
    row = conn.execute(select(_t).where(_t.c.code == code)).mappings().first()
    return _row_to_public(row) if row is not None else None


def list_path_configs(page: int = 1, page_size: int = 10, keyword: str | None = None) -> PathConfigPage:
    page = max(1, page)
    page_size = min(max(1, page_size), 200)
    offset = (page - 1) * page_size

    engine = get_engine()
    _check_table()
    with engine.connect() as conn:
        count_stmt = select(func.count()).select_from(_t)
        list_stmt = select(_t)
        if keyword:
            like = f"%{keyword}%"
            cond = _t.c.code.like(like) | _t.c.name.like(like) | _t.c.path.like(like)
            count_stmt = count_stmt.where(cond)
            list_stmt = list_stmt.where(cond)
        total = conn.execute(count_stmt).scalar() or 0
        rows = conn.execute(
            list_stmt.order_by(_t.c.id).limit(page_size).offset(offset)
        ).mappings().all()
    return PathConfigPage(
        items=[_row_to_public(row) for row in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


def get_path_config(path_id: int) -> PathConfigPublic:
    engine = get_engine()
    _check_table()
    with engine.connect() as conn:
        item = _fetch_by_id(conn, path_id)
    if item is None:
        raise PathConfigNotFoundError("路径配置不存在")
    return item


def create_path_config(payload: PathConfigCreate) -> PathConfigPublic:
    code = payload.code.strip()
    if not _CODE_RE.match(code):
        raise PathConfigCodeError("路径编码仅允许字母、数字、下划线和中划线（1-64 位）")

    engine = get_engine()
    _check_table()
    try:
        with engine.begin() as conn:
            if _fetch_by_code(conn, code) is not None:
                raise PathConfigCodeError(f"路径编码「{code}」已存在")
            conn.execute(
                text(
                    f"INSERT INTO {PATH_CONFIG_TABLE} (code, name, path, is_enabled, remark) "
                    f"VALUES (:code, :name, :path, :is_enabled, :remark)"
                ),
                {
                    "code": code,
                    "name": payload.name.strip(),
                    "path": payload.path.strip(),
                    "is_enabled": payload.is_enabled,
                    "remark": payload.remark,
                },
            )
            item = _fetch_by_code(conn, code)
    except IntegrityError as exc:  # 并发下唯一约束兜底
        raise PathConfigCodeError(f"路径编码「{code}」已存在") from exc
    if item is None:  # pragma: no cover
        raise PathConfigError("创建后读取失败，请重试")
    return item


def update_path_config(path_id: int, payload: PathConfigUpdate) -> PathConfigPublic:
    """编辑更新；code 为配置键不可修改，不在可更新字段内。"""
    engine = get_engine()
    _check_table()
    data = payload.model_dump(exclude_unset=True)
    allowed = {k: v for k, v in data.items() if k in {"name", "path", "is_enabled", "remark"}}
    for key in ("name", "path"):
        if key in allowed and isinstance(allowed[key], str):
            allowed[key] = allowed[key].strip() or None
    if any(k in allowed and allowed[k] is None for k in ("name", "path")):
        raise PathConfigError("名称与路径不能为空")

    with engine.begin() as conn:
        existing = _fetch_by_id(conn, path_id)
        if existing is None:
            raise PathConfigNotFoundError("路径配置不存在")
        if allowed:
            assignments = ", ".join(f"{col} = :{col}" for col in allowed)
            params = dict(allowed)
            if "is_enabled" in params:
                params["is_enabled"] = bool(params["is_enabled"])
            params["id"] = path_id
            params["updated_datetime"] = datetime.now(timezone.utc)
            conn.execute(
                text(
                    f"UPDATE {PATH_CONFIG_TABLE} SET {assignments}, "
                    f"updated_datetime = :updated_datetime, updated_by = SUSER_SNAME() "
                    f"WHERE id = :id"
                ),
                params,
            )
        item = _fetch_by_id(conn, path_id)
    if item is None:  # pragma: no cover
        raise PathConfigNotFoundError("路径配置不存在")
    return item


def delete_path_config(path_id: int) -> None:
    engine = get_engine()
    _check_table()
    with engine.begin() as conn:
        result = conn.execute(delete(_t).where(_t.c.id == path_id))
        if result.rowcount == 0:
            raise PathConfigNotFoundError("路径配置不存在")
