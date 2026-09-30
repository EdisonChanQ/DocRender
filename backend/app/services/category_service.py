from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import column, select, table as sa_table, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from app.db.config_store import config_store
from app.db.manager import database_manager
from app.db.tables import TABLES
from app.schemas.category import CategoryCreate, CategoryPage, CategoryPublic, CategoryUpdate

# 分类表名 —— 统一定义在 app/db/tables/category.py，改表名只需改那里
CATEGORY_TABLE = TABLES["category"].name

ID_PREFIX = "CAT"
SEQ_WIDTH = 4
CODE_MAX_RETRY = 5

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_]+$")

COLUMNS = (
    "id, code, name, sort_order, is_enabled, remark, "
    "created_by, created_datetime, updated_by, updated_datetime"
)


class CategoryError(Exception):
    """分类业务操作基类异常。"""


class DatabaseNotConfiguredError(CategoryError):
    pass


class CategoryNotFoundError(CategoryError):
    pass


class CategoryCodeGenerationError(CategoryError):
    pass


def _table() -> str:
    if not _IDENTIFIER_RE.match(CATEGORY_TABLE):
        raise CategoryError(f"分类表名配置非法：{CATEGORY_TABLE}")
    return CATEGORY_TABLE


def get_engine() -> Engine:
    engine = database_manager.engine
    if engine is not None:
        return engine
    saved = config_store.load()
    if saved is None:
        raise DatabaseNotConfiguredError("尚未配置数据库连接，请先在数据库配置页面完成设置")
    return database_manager.connect(saved)


def _row_to_public(row) -> CategoryPublic:
    return CategoryPublic(
        id=row["id"],
        code=row["code"],
        name=row["name"],
        sort_order=row["sort_order"],
        is_enabled=bool(row["is_enabled"]),
        remark=row["remark"],
        created_by=row["created_by"],
        created_datetime=row["created_datetime"],
        updated_by=row["updated_by"],
        updated_datetime=row["updated_datetime"],
    )


def _current_period() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m")


def _next_code(conn, table: str, period: str) -> str:
    prefix = f"{ID_PREFIX}{period}"
    start = len(prefix) + 1
    # 目标库是 SQL Server 2008 R2（10.50，兼容级别 100），TRY_CAST 是 2012+ 才有的，
    # 用了会直接 SQLExecDirectW 报 195。这里拆成两步等价实现：
    #   NOT LIKE N'%[^0-9]%' 先判纯数字，再 CAST —— 效果等同 TRY_CAST 的「转换失败给 NULL」。
    # 不用 ISNUMERIC 是因为它太宽松：'1e5'、'$1'、'1.2'、'+' 都会返回 1，但 CAST 会抛错。
    row = conn.execute(
        text(
            f"SELECT ISNULL(MAX(CASE WHEN SUBSTRING(code, :start, :width) NOT LIKE N'%[^0-9]%' "
            f"THEN CAST(SUBSTRING(code, :start, :width) AS INT) END), 0) + 1 "
            f"FROM {table} WHERE code LIKE :pattern"
        ),
        {"start": start, "width": SEQ_WIDTH, "pattern": f"{prefix}%"},
    ).scalar()
    seq = int(row or 1)
    return f"{prefix}{seq:0{SEQ_WIDTH}d}"


def _fetch_by_id(conn, table: str, category_id: int) -> CategoryPublic | None:
    row = conn.execute(
        text(f"SELECT {COLUMNS} FROM {table} WHERE id = :id"),
        {"id": category_id},
    ).mappings().first()
    if row is None:
        return None
    return _row_to_public(row)


def list_categories(page: int = 1, page_size: int = 10) -> CategoryPage:
    """分页查询分类列表。

    使用 SQLAlchemy Core 的 select().limit().offset()，由方言自动编译分页语法
    （SQL Server OFFSET/FETCH、MySQL/PG/SQLite LIMIT...OFFSET）。
    注意：text() 生成的 TextClause 是叶子对象，不支持 .limit()，故这里用
    table()/column() 构造轻量表引用，无需 ORM 映射。
    """
    page = max(1, page)
    page_size = min(max(1, page_size), 200)
    offset = (page - 1) * page_size

    engine = get_engine()
    tbl_name = _table()
    tbl = sa_table(
        tbl_name,
        column("id"), column("code"), column("name"), column("sort_order"),
        column("is_enabled"), column("remark"), column("created_by"),
        column("created_datetime"), column("updated_by"), column("updated_datetime"),
    )
    with engine.connect() as conn:
        total = conn.execute(
            text(f"SELECT COUNT(*) FROM {tbl_name}")
        ).scalar() or 0
        stmt = (
            select(tbl)
            .order_by(tbl.c.sort_order, tbl.c.id)
            .limit(page_size)
            .offset(offset)
        )
        rows = conn.execute(stmt).mappings().all()
    return CategoryPage(
        items=[_row_to_public(row) for row in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


def get_category(category_id: int) -> CategoryPublic:
    engine = get_engine()
    table = _table()
    with engine.connect() as conn:
        item = _fetch_by_id(conn, table, category_id)
    if item is None:
        raise CategoryNotFoundError("分类不存在")
    return item


def create_category(payload: CategoryCreate) -> CategoryPublic:
    engine = get_engine()
    table = _table()
    period = _current_period()
    last_error: Exception | None = None

    for _ in range(CODE_MAX_RETRY):
        try:
            with engine.begin() as conn:
                code = _next_code(conn, table, period)
                conn.execute(
                    text(
                        f"INSERT INTO {table} (code, name, sort_order, is_enabled, remark) "
                        f"VALUES (:code, :name, :sort_order, :is_enabled, :remark)"
                    ),
                    {
                        "code": code,
                        "name": payload.name,
                        "sort_order": payload.sort_order,
                        "is_enabled": payload.is_enabled,
                        "remark": payload.remark,
                    },
                )
                item = _fetch_by_id(conn, table, conn.execute(
                    text(f"SELECT id FROM {table} WHERE code = :code"),
                    {"code": code},
                ).scalar())
            if item is None:  # pragma: no cover
                raise CategoryCodeGenerationError("分类创建后读取失败，请重试")
            return item
        except IntegrityError as exc:
            last_error = exc
            continue

    raise CategoryCodeGenerationError("分类编号生成冲突，请稍后重试") from last_error


def update_category(category_id: int, payload: CategoryUpdate) -> CategoryPublic:
    engine = get_engine()
    table = _table()
    data = payload.model_dump(exclude_unset=True)
    allowed = {k: v for k, v in data.items() if k in {"name", "sort_order", "is_enabled", "remark"}}

    with engine.begin() as conn:
        existing = _fetch_by_id(conn, table, category_id)
        if existing is None:
            raise CategoryNotFoundError("分类不存在")

        if allowed:
            assignments = ", ".join(f"{column} = :{column}" for column in allowed)
            params = dict(allowed)
            if "is_enabled" in params:
                params["is_enabled"] = bool(params["is_enabled"])
            params["id"] = category_id
            conn.execute(
                text(
                    f"UPDATE {table} SET {assignments}, "
                    f"updated_datetime = SYSUTCDATETIME(), updated_by = SUSER_SNAME() "
                    f"WHERE id = :id"
                ),
                params,
            )
            item = _fetch_by_id(conn, table, category_id)
        else:
            item = existing

    if item is None:  # pragma: no cover
        raise CategoryNotFoundError("分类不存在")
    return item


def delete_category(category_id: int) -> None:
    """删除分类；同一事务内将该分类下所有模板置为失效（模板保留，需改挂有效分类才能再启用）。"""
    from app.services.template_service import disable_templates_of_category

    engine = get_engine()
    table = _table()
    with engine.begin() as conn:
        result = conn.execute(text(f"DELETE FROM {table} WHERE id = :id"), {"id": category_id})
        if result.rowcount == 0:
            raise CategoryNotFoundError("分类不存在")
        disable_templates_of_category(conn, category_id)
