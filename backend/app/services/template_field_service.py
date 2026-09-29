from __future__ import annotations

import base64
import re
from datetime import datetime, timezone

from sqlalchemy import column, delete, func, select, table as sa_table, text
from sqlalchemy.engine import Engine

from app.db.config_store import config_store
from app.db.manager import database_manager
from app.db.tables import TABLES
from app.schemas.template_field import (
    TemplateFieldCreate,
    TemplateFieldPublic,
    TemplateFieldUpdate,
)

# 表名统一定义处
FIELD_TABLE = TABLES["template_field"].name
TEMPLATE_TABLE = TABLES["template"].name

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_]+$")

_f = sa_table(
    FIELD_TABLE,
    column("id"), column("template_id"), column("field_key"), column("label"),
    column("field_type"),
    column("x"), column("y"), column("width"), column("height"),
    column("pad"), column("sort_order"), column("ref_image"),
    column("created_by"), column("created_datetime"),
    column("updated_by"), column("updated_datetime"),
)

# 列表查询列（排除 ref_image 大字段，与模板表同策略）
_LIST_COLS = [
    _f.c.id, _f.c.template_id, _f.c.field_key, _f.c.label, _f.c.field_type,
    _f.c.x, _f.c.y, _f.c.width, _f.c.height,
    _f.c.pad, _f.c.sort_order, _f.c.created_by, _f.c.created_datetime,
    _f.c.updated_by, _f.c.updated_datetime,
]

REF_IMAGE_MAX_BYTES = 4 * 1024 * 1024  # 字段样本图上限 4MB（base64 解码后）


class TemplateFieldError(Exception):
    """模板字段业务异常基类。"""


class DatabaseNotConfiguredError(TemplateFieldError):
    pass


class TemplateFieldNotFoundError(TemplateFieldError):
    pass


class TemplateNotFoundError(TemplateFieldError):
    pass


class FieldKeyConflictError(TemplateFieldError):
    pass


def get_engine() -> Engine:
    engine = database_manager.engine
    if engine is not None:
        return engine
    saved = config_store.load()
    if saved is None:
        raise DatabaseNotConfiguredError("尚未配置数据库连接，请先在数据库配置页面完成设置")
    return database_manager.connect(saved)


def _row_to_public(row) -> TemplateFieldPublic:
    return TemplateFieldPublic(
        id=row["id"],
        template_id=row["template_id"],
        field_key=row["field_key"],
        label=row["label"],
        field_type=row["field_type"] or "text",
        x=row["x"],
        y=row["y"],
        width=row["width"],
        height=row["height"],
        pad=row["pad"],
        sort_order=row["sort_order"],
        ref_image_b64=(
            base64.b64encode(bytes(row["ref_image"])).decode()
            if row.get("ref_image") is not None
            else None
        ),
        created_by=row["created_by"],
        created_datetime=row["created_datetime"],
        updated_by=row["updated_by"],
        updated_datetime=row["updated_datetime"],
    )


def _fetch_by_id(conn, field_id: int, *, with_ref: bool = False) -> TemplateFieldPublic | None:
    cols = select(_f) if with_ref else select(*_LIST_COLS)
    row = conn.execute(cols.where(_f.c.id == field_id)).mappings().first()
    return _row_to_public(row) if row is not None else None


def _decode_ref_image(ref_b64: str | None) -> bytes | None:
    """base64（可带 data:image/png;base64, 前缀）→ bytes，校验大小。"""
    if ref_b64 is None:
        return None
    payload = ref_b64.split(",", 1)[-1].strip()
    try:
        raw = base64.b64decode(payload, validate=False)
    except Exception as exc:  # noqa: BLE001
        raise TemplateFieldError(f"字段样本图 base64 无效：{exc}") from exc
    if not raw:
        raise TemplateFieldError("字段样本图为空")
    if len(raw) > REF_IMAGE_MAX_BYTES:
        raise TemplateFieldError(f"字段样本图超过 {REF_IMAGE_MAX_BYTES // (1024 * 1024)}MB 限制")
    return raw


def _check_table() -> None:
    for name in (FIELD_TABLE, TEMPLATE_TABLE):
        if not _IDENTIFIER_RE.match(name):
            raise TemplateFieldError(f"表名配置非法：{name}")


def _template_exists(conn, template_id: int) -> bool:
    row = conn.execute(
        text(f"SELECT TOP 1 id FROM {TEMPLATE_TABLE} WHERE id = :id"), {"id": template_id}
    ).first()
    return row is not None


def list_fields(template_id: int) -> list[TemplateFieldPublic]:
    """模板下全部字段（量小不分页），按 sort_order、id 排序。"""
    engine = get_engine()
    _check_table()
    with engine.connect() as conn:
        if not _template_exists(conn, template_id):
            raise TemplateNotFoundError("模板不存在")
        rows = conn.execute(
            select(*_LIST_COLS)
            .where(_f.c.template_id == template_id)
            .order_by(_f.c.sort_order, _f.c.id)
        ).mappings().all()
    return [_row_to_public(row) for row in rows]


def get_field(field_id: int) -> TemplateFieldPublic:
    """单查详情：含字段样本图 base64。"""
    engine = get_engine()
    _check_table()
    with engine.connect() as conn:
        item = _fetch_by_id(conn, field_id, with_ref=True)
    if item is None:
        raise TemplateFieldNotFoundError("字段不存在")
    return item


def create_field(template_id: int, payload: TemplateFieldCreate) -> TemplateFieldPublic:
    engine = get_engine()
    _check_table()
    ref_bytes = _decode_ref_image(payload.ref_image)
    with engine.begin() as conn:
        if not _template_exists(conn, template_id):
            raise TemplateNotFoundError("模板不存在")
        dup = conn.execute(
            select(func.count())
            .select_from(_f)
            .where(_f.c.template_id == template_id, _f.c.field_key == payload.field_key)
        ).scalar()
        if dup:
            raise FieldKeyConflictError(f"字段命名「{payload.field_key}」在该模板下已存在")
        conn.execute(
            text(
                f"INSERT INTO {FIELD_TABLE} (template_id, field_key, label, field_type, x, y, width, height, pad, sort_order, ref_image) "
                f"VALUES (:template_id, :field_key, :label, :field_type, :x, :y, :width, :height, :pad, :sort_order, :ref_image)"
            ),
            {
                "template_id": template_id,
                "field_key": payload.field_key,
                "label": payload.label.strip(),
                "field_type": payload.field_type,
                "x": payload.x,
                "y": payload.y,
                "width": payload.width,
                "height": payload.height,
                "pad": payload.pad,
                "sort_order": payload.sort_order,
                "ref_image": ref_bytes,
            },
        )
        row = conn.execute(
            select(_f.c.id)
            .where(_f.c.template_id == template_id, _f.c.field_key == payload.field_key)
        ).scalar()
        item = _fetch_by_id(conn, int(row)) if row else None
    if item is None:  # pragma: no cover
        raise TemplateFieldError("创建后读取失败，请重试")
    return item


def update_field(field_id: int, payload: TemplateFieldUpdate) -> TemplateFieldPublic:
    engine = get_engine()
    _check_table()
    data = payload.model_dump(exclude_unset=True)
    allowed = {
        k: v
        for k, v in data.items()
        if k in {"field_key", "label", "field_type", "x", "y", "width", "height", "pad", "sort_order"}
    }
    # 样本图：显式传 null 视为清除；传 base64 则替换
    if "ref_image" in data:
        allowed["ref_image"] = _decode_ref_image(data["ref_image"])
    if "label" in allowed and isinstance(allowed["label"], str):
        allowed["label"] = allowed["label"].strip()

    with engine.begin() as conn:
        existing = _fetch_by_id(conn, field_id)
        if existing is None:
            raise TemplateFieldNotFoundError("字段不存在")
        if "field_key" in allowed and allowed["field_key"] != existing.field_key:
            dup = conn.execute(
                select(func.count())
                .select_from(_f)
                .where(
                    _f.c.template_id == existing.template_id,
                    _f.c.field_key == allowed["field_key"],
                )
            ).scalar()
            if dup:
                raise FieldKeyConflictError(f"字段命名「{allowed['field_key']}」在该模板下已存在")
        if allowed:
            assignments = ", ".join(f"{col} = :{col}" for col in allowed)
            params = dict(allowed)
            params["id"] = field_id
            params["updated_datetime"] = datetime.now(timezone.utc)
            conn.execute(
                text(
                    f"UPDATE {FIELD_TABLE} SET {assignments}, "
                    f"updated_datetime = :updated_datetime, updated_by = SUSER_SNAME() "
                    f"WHERE id = :id"
                ),
                params,
            )
        item = _fetch_by_id(conn, field_id)
    if item is None:  # pragma: no cover
        raise TemplateFieldNotFoundError("字段不存在")
    return item


def delete_field(field_id: int) -> None:
    engine = get_engine()
    _check_table()
    with engine.begin() as conn:
        result = conn.execute(delete(_f).where(_f.c.id == field_id))
        if result.rowcount == 0:
            raise TemplateFieldNotFoundError("字段不存在")
