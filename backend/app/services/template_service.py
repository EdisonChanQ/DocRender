from __future__ import annotations

import base64
import re
from datetime import datetime, timezone

from sqlalchemy import column, delete, func, insert, select, table as sa_table, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from app.db.config_store import config_store
from app.db.manager import database_manager
from app.db.tables import TABLES
from app.schemas.template import (
    RefRenderRequest,
    TemplateCreate,
    TemplatePage,
    TemplatePublic,
    TemplateUpdate,
)
from app.services import template_ref_render as ref_render

# 表名统一定义处：app/db/tables/*.py
TEMPLATE_TABLE = TABLES["template"].name
CATEGORY_TABLE = TABLES["category"].name

ID_PREFIX = "TPL"
SEQ_WIDTH = 4
CODE_MAX_RETRY = 5

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_]+$")

# 轻量表引用（无需 ORM 映射），列与建表 DDL 对齐
_t = sa_table(
    TEMPLATE_TABLE,
    column("id"), column("code"), column("category_id"), column("name"),
    column("dpi"), column("width"), column("height"), column("ref_image"),
    column("is_enabled"), column("created_by"), column("created_datetime"),
    column("updated_by"), column("updated_datetime"),
)
_c = sa_table(CATEGORY_TABLE, column("id"), column("code"), column("name"))

REF_IMAGE_MAX_BYTES = 8 * 1024 * 1024  # 范本图上限 8MB（base64 解码后）


class TemplateError(Exception):
    """模板业务操作基类异常。"""


class DatabaseNotConfiguredError(TemplateError):
    pass


class TemplateNotFoundError(TemplateError):
    pass


class TemplateCodeGenerationError(TemplateError):
    pass


class CategoryInvalidError(TemplateError):
    """所属分类不存在（启用/创建时的校验）。"""


def _check_names() -> None:
    for name in (TEMPLATE_TABLE, CATEGORY_TABLE):
        if not _IDENTIFIER_RE.match(name):
            raise TemplateError(f"表名配置非法：{name}")


def get_engine() -> Engine:
    engine = database_manager.engine
    if engine is not None:
        return engine
    saved = config_store.load()
    if saved is None:
        raise DatabaseNotConfiguredError("尚未配置数据库连接，请先在数据库配置页面完成设置")
    return database_manager.connect(saved)


def _row_to_public(row, *, with_ref: bool = False) -> TemplatePublic:
    ref_b64 = None
    if with_ref and row.get("ref_image") is not None:
        ref_b64 = base64.b64encode(bytes(row["ref_image"])).decode()
    return TemplatePublic(
        id=row["id"],
        code=row["code"],
        category_id=row["category_id"],
        category_name=row["category_name"],
        name=row["name"],
        dpi=row["dpi"],
        width=row["width"],
        height=row["height"],
        ref_image_b64=ref_b64,
        is_enabled=bool(row["is_enabled"]),
        created_by=row["created_by"],
        created_datetime=row["created_datetime"],
        updated_by=row["updated_by"],
        updated_datetime=row["updated_datetime"],
    )


def _select_with_category(with_ref: bool = False):
    """模板 LEFT JOIN 分类：分类被删除后 category_name 为 None（即失效）。
    with_ref=True 时附带 ref_image 大字段（仅单查详情使用）。
    """
    cols = [
        _t.c.id, _t.c.code, _t.c.category_id, _t.c.name,
        _t.c.dpi, _t.c.width, _t.c.height, _t.c.is_enabled,
        _t.c.created_by, _t.c.created_datetime, _t.c.updated_by, _t.c.updated_datetime,
        _c.c.name.label("category_name"),
    ]
    if with_ref:
        cols.append(_t.c.ref_image)
    return select(*cols).select_from(_t.outerjoin(_c, _t.c.category_id == _c.c.id))


def _category_exists(conn, category_id: int) -> bool:
    count = conn.execute(
        select(func.count()).select_from(_c).where(_c.c.id == category_id)
    ).scalar()
    return bool(count)


def _current_period() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m")


def _next_code(conn, period: str) -> str:
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
            f"FROM {TEMPLATE_TABLE} WHERE code LIKE :pattern"
        ),
        {"start": start, "width": SEQ_WIDTH, "pattern": f"{prefix}%"},
    ).scalar()
    seq = int(row or 1)
    return f"{prefix}{seq:0{SEQ_WIDTH}d}"


def _fetch_by_id(conn, template_id: int) -> TemplatePublic | None:
    row = conn.execute(
        _select_with_category().where(_t.c.id == template_id)
    ).mappings().first()
    return _row_to_public(row) if row is not None else None


def _decode_ref_image(ref_b64: str | None) -> bytes | None:
    """base64（可带 data:image/png;base64, 前缀）→ bytes，校验大小。"""
    if ref_b64 is None:
        return None
    payload = ref_b64.split(",", 1)[-1].strip()
    try:
        raw = base64.b64decode(payload, validate=False)
    except Exception as exc:  # noqa: BLE001
        raise TemplateError(f"范本图 base64 无效：{exc}") from exc
    if not raw:
        raise TemplateError("范本图为空")
    if len(raw) > REF_IMAGE_MAX_BYTES:
        raise TemplateError(f"范本图超过 {REF_IMAGE_MAX_BYTES // (1024 * 1024)}MB 限制")
    return raw


def list_templates(page: int = 1, page_size: int = 10, category_id: int | None = None) -> TemplatePage:
    page = max(1, page)
    page_size = min(max(1, page_size), 200)
    offset = (page - 1) * page_size

    engine = get_engine()
    _check_names()
    with engine.connect() as conn:
        count_stmt = select(func.count()).select_from(_t)
        list_stmt = _select_with_category()
        if category_id is not None:
            count_stmt = count_stmt.where(_t.c.category_id == category_id)
            list_stmt = list_stmt.where(_t.c.category_id == category_id)
        total = conn.execute(count_stmt).scalar() or 0
        rows = conn.execute(
            list_stmt.order_by(_t.c.id.desc()).limit(page_size).offset(offset)
        ).mappings().all()
    return TemplatePage(
        items=[_row_to_public(row) for row in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


def get_template(template_id: int) -> TemplatePublic:
    """单查详情：含范本图 base64（编辑页回显用）。"""
    engine = get_engine()
    _check_names()
    with engine.connect() as conn:
        row = conn.execute(
            _select_with_category(with_ref=True).where(_t.c.id == template_id)
        ).mappings().first()
        item = _row_to_public(row, with_ref=True) if row is not None else None
    if item is None:
        raise TemplateNotFoundError("模板不存在")
    return item


def create_template(payload: TemplateCreate) -> TemplatePublic:
    engine = get_engine()
    _check_names()
    ref_bytes = _decode_ref_image(payload.ref_image)
    period = _current_period()
    last_error: Exception | None = None

    for _ in range(CODE_MAX_RETRY):
        try:
            with engine.begin() as conn:
                if not _category_exists(conn, payload.category_id):
                    raise CategoryInvalidError("所属分类不存在，请选择有效分类")
                code = _next_code(conn, period)
                conn.execute(
                    insert(_t).values(
                        code=code,
                        category_id=payload.category_id,
                        name=payload.name,
                        dpi=payload.dpi,
                        width=payload.width,
                        height=payload.height,
                        ref_image=ref_bytes,
                        is_enabled=payload.is_enabled,
                    )
                )
                new_id = conn.execute(
                    select(_t.c.id).where(_t.c.code == code)
                ).scalar()
                item = _fetch_by_id(conn, new_id)
            if item is None:  # pragma: no cover
                raise TemplateCodeGenerationError("模板创建后读取失败，请重试")
            return item
        except IntegrityError as exc:
            last_error = exc
            continue

    raise TemplateCodeGenerationError("模板编号生成冲突，请稍后重试") from last_error


def update_template(template_id: int, payload: TemplateUpdate) -> TemplatePublic:
    engine = get_engine()
    _check_names()
    data = payload.model_dump(exclude_unset=True)
    allowed = {k: v for k, v in data.items() if k in {"category_id", "name", "is_enabled", "dpi", "width", "height"}}
    # 范本图：显式传 null 视为清除；传 base64 则替换
    if "ref_image" in data:
        allowed["ref_image"] = _decode_ref_image(data["ref_image"])

    with engine.begin() as conn:
        existing = _fetch_by_id(conn, template_id)
        if existing is None:
            raise TemplateNotFoundError("模板不存在")

        # 生效值：本次未传的字段沿用现有值
        next_category_id = allowed.get("category_id", existing.category_id)
        next_enabled = allowed.get("is_enabled", existing.is_enabled)

        # 校验一：迁移分类必须指向有效分类
        if "category_id" in allowed and not _category_exists(conn, next_category_id):
            raise CategoryInvalidError("目标分类不存在，请选择有效分类")

        # 校验二：启用状态下必须挂有效分类（失效模板需先修正分类才能启用）
        if next_enabled and not _category_exists(conn, next_category_id):
            raise CategoryInvalidError("所属分类已失效，请先修改为有效分类后再启用")

        if allowed:
            assignments = ", ".join(f"{col} = :{col}" for col in allowed)
            params = dict(allowed)
            if "is_enabled" in params:
                params["is_enabled"] = bool(params["is_enabled"])
            params["id"] = template_id
            conn.execute(
                text(
                    f"UPDATE {TEMPLATE_TABLE} SET {assignments}, "
                    f"updated_datetime = SYSUTCDATETIME(), updated_by = SUSER_SNAME() "
                    f"WHERE id = :id"
                ),
                params,
            )
        item = _fetch_by_id(conn, template_id)

    if item is None:  # pragma: no cover
        raise TemplateNotFoundError("模板不存在")
    return item


def delete_template(template_id: int) -> None:
    engine = get_engine()
    _check_names()
    with engine.begin() as conn:
        result = conn.execute(delete(_t).where(_t.c.id == template_id))
        if result.rowcount == 0:
            raise TemplateNotFoundError("模板不存在")


FIELD_TABLE = TABLES["template_field"].name

_f = sa_table(
    FIELD_TABLE,
    column("id"), column("template_id"), column("x"), column("y"),
    column("width"), column("height"), column("pad"),
)


def _rescale_fields(conn, template_id: int, old_w: int, old_h: int, new_w: int, new_h: int) -> int:
    """范本换尺寸后把字段坐标从旧 ref 像素空间等比缩放到新空间（四舍五入，最小 1px）。

    宽高比一致性已由模具校验一（2%）保证，sx≈sy。pad 是"识别留量像素"，
    同样按尺度换算，保持物理留量不变。字段样本图（field.ref_image）不重裁：
    它是 OCR 底片参考，下次字段编辑时前端会按新范本重新裁取。
    """
    if old_w == new_w and old_h == new_h:
        return 0
    sx, sy = new_w / old_w, new_h / old_h
    rows = conn.execute(select(_f.c.id, _f.c.x, _f.c.y, _f.c.width, _f.c.height, _f.c.pad).where(
        _f.c.template_id == template_id
    )).all()
    for rid, x, y, w, h, pad in rows:
        conn.execute(
            text(
                f"UPDATE {FIELD_TABLE} SET x = :x, y = :y, width = :w, height = :h, pad = :pad, "
                f"updated_datetime = SYSUTCDATETIME(), updated_by = SUSER_SNAME() WHERE id = :id"
            ),
            {
                "x": int(round(x * sx)), "y": int(round(y * sy)),
                "w": max(1, int(round(w * sx))), "h": max(1, int(round(h * sy))),
                "pad": int(round((pad or 0) * ((sx + sy) / 2))), "id": rid,
            },
        )
    return len(rows)


def save_ref_rendered(template_id: int, req: RefRenderRequest, source: bytes | None,
                      source_filename: str) -> TemplatePublic:
    """模具重渲染并保存范本：ref 像素恒等于声明尺寸 + 字段坐标联动 + 共享目录备份。

    备份失败**不回滚**入库（图已经存对），仅在返回体标记 backup_ok=False。
    """
    engine = get_engine()
    _check_names()

    ref_raw_old: bytes | None = None
    with engine.begin() as conn:
        row = conn.execute(
            select(_t.c.id, _t.c.code, _t.c.category_id, _t.c.ref_image).where(_t.c.id == template_id)
        ).first()
        if row is None:
            raise TemplateNotFoundError("模板不存在")
        tpl_id, tpl_code = row[0], row[1]
        category_id = row[2]
        ref_raw_old = bytes(row[3]) if row[3] is not None else None

    # —— 模具重渲染（含全部几何校验，失败抛 RefRenderError → 路由 400）——
    # 无源文件（二次框选旧范本）时的缩放底片 = 库中既有 ref（ref_raw_old）。
    # 不从前端传范本 base64：Starlette 普通 form 字段有 1MB/part 上限，范本必超。
    ref_raw_new = ref_render.render_ref(
        source=source,
        source_filename=source_filename,
        ref_raw=ref_raw_old,
        page_index=req.page_index,
        rotation_deg=req.rotation_deg,
        crop=(req.crop_x, req.crop_y, req.crop_w, req.crop_h),
        preview_size=(req.preview_w, req.preview_h),
        dpi=req.dpi,
        width=req.width,
        height=req.height,
    )
    if len(ref_raw_new) > REF_IMAGE_MAX_BYTES:
        raise TemplateError(f"重渲染范本超过 {REF_IMAGE_MAX_BYTES // (1024 * 1024)}MB 限制")

    # —— 旧 ref 尺寸（字段坐标缩放基准；无旧 ref 则无需缩放）——
    old_size: tuple[int, int] | None = None
    if ref_raw_old is not None:
        try:
            from PIL import Image
            import io

            with Image.open(io.BytesIO(ref_raw_old)) as im:
                old_size = im.size
        except Exception:  # noqa: BLE001 - 旧图坏了不阻塞换新
            old_size = None

    backup_rel: str | None = None
    backup_err: str | None = None
    with engine.begin() as conn:
        conn.execute(
            text(
                f"UPDATE {TEMPLATE_TABLE} SET dpi = :dpi, width = :w, height = :h, ref_image = :ref, "
                f"updated_datetime = SYSUTCDATETIME(), updated_by = SUSER_SNAME() WHERE id = :id"
            ),
            {"dpi": req.dpi, "w": req.width, "h": req.height, "ref": ref_raw_new, "id": tpl_id},
        )
        n = 0
        if old_size is not None:
            n = _rescale_fields(conn, tpl_id, old_size[0], old_size[1], req.width, req.height)
        item = _fetch_by_id(conn, tpl_id)
        cat_code = conn.execute(select(_c.c.code).where(_c.c.id == category_id)).scalar()

    # —— 共享目录备份（事务外：网络盘失败不回滚库）——
    try:
        from app.services import file_job_service

        shared_dir = file_job_service.get_shared_dir()
        backup_rel, backup_err = ref_render.backup_ref_to_shared(
            shared_dir=shared_dir,
            category_code=str(cat_code or "unknown"),
            template_code=str(tpl_code),
            raw=ref_raw_new,
        )
    except Exception as exc:  # noqa: BLE001
        backup_err = f"备份失败：{exc}"

    assert item is not None
    item.backup_path = backup_rel
    item.backup_ok = backup_err is None
    item.backup_error = backup_err
    return item


def disable_templates_of_category(conn, category_id: int) -> None:
    """供分类删除时调用：该分类下所有模板置为失效（is_enabled=0），模板本身保留。"""
    conn.execute(
        text(
            f"UPDATE {TEMPLATE_TABLE} SET is_enabled = 0, "
            f"updated_datetime = SYSUTCDATETIME(), updated_by = SUSER_SNAME() "
            f"WHERE category_id = :category_id"
        ),
        {"category_id": category_id},
    )
