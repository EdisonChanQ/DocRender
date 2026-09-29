"""文件任务注册表服务：注册（hash 去重）、抢占（READPAST+租约）、心跳、完成、失败、取消。

多实例并发安全：claim 用 UPDATE TOP(1) WITH (READPAST, ROWLOCK)，
各实例跳过被锁行互不阻塞；租约超时（默认 10 分钟无心跳）的任务在 claim 前自动回收到待处理。
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select, table as sa_table, column as sa_column, text
from sqlalchemy.engine import Engine

from app.db.config_store import config_store
from app.db.manager import database_manager
from app.db.tables import TABLES
from app.schemas.file_job import (
    JOB_STATES,
    JOB_STEPS,
    PAGE_STATE_DONE,
    PAGE_STATE_FAILED,
    ClaimRequest,
    CompleteRequest,
    FailRequest,
    FileJobPage,
    FileJobPublic,
    HeartbeatRequest,
)

JOB_TABLE = TABLES["file_job"].name
CATEGORY_TABLE = TABLES["category"].name
PAGE_TABLE = TABLES["file_page"].name

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_]+$")

_j = sa_table(
    JOB_TABLE,
    sa_column("id"), sa_column("code"), sa_column("file_hash"), sa_column("file_name"),
    sa_column("rel_path"), sa_column("file_size"), sa_column("extension"),
    sa_column("category_id"), sa_column("template_id"), sa_column("state"), sa_column("step"),
    sa_column("instance_id"), sa_column("claimed_datetime"), sa_column("lease_expires_datetime"),
    sa_column("heartbeat_datetime"), sa_column("retry_count"), sa_column("max_retry"),
    sa_column("priority"), sa_column("error_msg"), sa_column("page_count"),
    sa_column("output_rel_path"), sa_column("created_by"), sa_column("created_datetime"),
    sa_column("updated_by"), sa_column("updated_datetime"),
)
_c = sa_table(CATEGORY_TABLE, sa_column("id"), sa_column("name"))
# 页表只取聚合所需的两个列，避免把大列（result_json）带进列表查询
_pg = sa_table(PAGE_TABLE, sa_column("job_id"), sa_column("state"))


class FileJobError(Exception):
    pass


class DatabaseNotConfiguredError(FileJobError):
    pass


class FileJobNotFoundError(FileJobError):
    pass


class FileJobStateError(FileJobError):
    """状态机冲突（如重复注册返回已有、非法迁移）。"""

    def __init__(self, message: str, *, existing: FileJobPublic | None = None):
        super().__init__(message)
        self.existing = existing


def get_engine() -> Engine:
    engine = database_manager.engine
    if engine is not None:
        return engine
    saved = config_store.load()
    if saved is None:
        raise DatabaseNotConfiguredError("尚未配置数据库连接，请先在数据库配置页面完成设置")
    return database_manager.connect(saved)


def _check_tables() -> None:
    for name in (JOB_TABLE, CATEGORY_TABLE):
        if not _IDENTIFIER_RE.match(name):
            raise FileJobError(f"表名配置非法：{name}")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _row_to_public(row) -> FileJobPublic:
    state = int(row["state"])
    return FileJobPublic(
        id=row["id"],
        code=row["code"],
        file_hash=row["file_hash"],
        file_name=row["file_name"],
        rel_path=row["rel_path"],
        file_size=int(row["file_size"]),
        extension=row["extension"],
        category_id=int(row["category_id"]),
        category_name=row.get("category_name"),
        template_id=int(row["template_id"]) if row["template_id"] is not None else None,
        state=state,
        state_label=JOB_STATES.get(state, str(state)),
        step=row["step"],
        instance_id=row["instance_id"],
        claimed_datetime=row["claimed_datetime"],
        lease_expires_datetime=row["lease_expires_datetime"],
        heartbeat_datetime=row["heartbeat_datetime"],
        retry_count=int(row["retry_count"]),
        max_retry=int(row["max_retry"]),
        priority=int(row["priority"]),
        error_msg=row["error_msg"],
        page_count=int(row["page_count"]) if row["page_count"] is not None else None,
        page_total=int(row.get("page_total") or 0),
        page_done=int(row.get("page_done") or 0),
        output_rel_path=row["output_rel_path"],
        created_by=row["created_by"],
        created_datetime=row["created_datetime"],
        updated_by=row["updated_by"],
        updated_datetime=row["updated_datetime"],
    )


def _page_progress_subquery():
    """按 job_id 聚合页级进度：总页数 + 已终态页数（成功/失败都算「处理完」）。

    一次性 GROUP BY 全表，配合 LEFT JOIN 供列表反查，**不在 Python 里逐任务查询**
    （列表 200 条 → 200 次往返会拖垮列表接口）。
    流水线登记页产物之前该 job 无页行 → LEFT JOIN 得 NULL，coalesce 成 0。
    """
    return (
        select(
            _pg.c.job_id.label("job_id"),
            func.count().label("page_total"),
            func.sum(
                case((_pg.c.state.in_([PAGE_STATE_DONE, PAGE_STATE_FAILED]), 1), else_=0)
            ).label("page_done"),
        )
        .group_by(_pg.c.job_id)
        .subquery()
    )


def _select_with_category():
    """单条查询（写路径回执用）：不带页级进度聚合。

    进度聚合是全表 GROUP BY，claim/heartbeat/complete 等写路径每步都读回执，
    带上会白跑一次全表扫描；单条回执也不需要进度（前端列表才展示）。
    """
    return select(
        _j, _c.c.name.label("category_name"),
    ).select_from(_j.outerjoin(_c, _j.c.category_id == _c.c.id))


def _select_list_with_progress():
    """列表查询：LEFT JOIN 页级进度聚合（一次 GROUP BY 覆盖整页结果，非逐任务查询）。"""
    prog = _page_progress_subquery()
    return (
        select(
            _j,
            _c.c.name.label("category_name"),
            func.coalesce(prog.c.page_total, 0).label("page_total"),
            func.coalesce(prog.c.page_done, 0).label("page_done"),
        ).select_from(
            _j.outerjoin(_c, _j.c.category_id == _c.c.id).outerjoin(
                prog, prog.c.job_id == _j.c.id
            )
        )
    )


def _fetch_by_code(conn, code: str) -> FileJobPublic | None:
    row = conn.execute(_select_with_category().where(_j.c.code == code)).mappings().first()
    return _row_to_public(row) if row is not None else None


def generate_code() -> str:
    """JOB+yyyyMMddHHmmss+4位随机（对齐 drc-data 的 JOB 命名风格）。"""
    return f"JOB{_utcnow().strftime('%Y%m%d%H%M%S')}{secrets.token_hex(2).upper()}"


# —— 落盘路径规则（唯一真源，注册与渲染都必须走这里，避免两边拼出不同目录）——
#
#   jobs/{分类code}/{yyyyMM}/{yyyyMMdd}/{job_code}/
#       ├── source/{原文件名}          上传原件（保留原名，可读）
#       ├── pages/{job_code}.p{页}c{块}.png   配准通过的页/块产物
#       └── reject/{job_code}.p{页}c{块}.png  相关度 <0.45 的页，隔离待人工审计
#
# 为什么用「分类 code」而不是 category_id：盘上是 CAT2026090001 这种有语义的名字，
# 人工翻盘不用查库；为什么「分类/月/日/任务」四级：一个任务 = 一个目录，
# 清任务产物 = 删一个目录，且分类/月/日天然可归档。

def job_dir(category_code: str, when: datetime, job_code: str) -> str:
    """任务目录（相对共享根）。

    when 用任务的注册时间（UTC），保证注册时算出的路径与 worker 处理时一致
    （worker 用 job.created_datetime 反算，不能取当前时间）。
    """
    return f"jobs/{category_code}/{when.strftime('%Y%m')}/{when.strftime('%Y%m%d')}/{job_code}"


def source_rel_path(category_code: str, when: datetime, job_code: str, file_name: str) -> str:
    """上传原件相对路径：{任务目录}/source/{原文件名}。"""
    return f"{job_dir(category_code, when, job_code)}/source/{sanitize_filename(file_name)}"


def job_pages_dir(category_code: str, when: datetime, job_code: str) -> str:
    """配准通过的页/块产物目录。"""
    return f"{job_dir(category_code, when, job_code)}/pages"


def job_reject_dir(category_code: str, when: datetime, job_code: str) -> str:
    """配准失败（reject）的页图像目录，隔离待审。"""
    return f"{job_dir(category_code, when, job_code)}/reject"


def task_dir_of(rel_path: str) -> str:
    """从上传件相对路径反推任务目录（去掉尾部 source/{文件名}）。

    路径由 source_rel_path 生成，形如
    `jobs/{分类code}/{yyyyMM}/{yyyyMMdd}/{job_code}/source/{文件名}`，
    去掉最后两段即任务目录。worker 用它落页产物，**与注册端天然一致**
    （不用再查分类、也不用重算日期，避免两边拼出不同目录）。
    兼容旧结构（无 source/ 段）时返回其父目录。
    """
    parts = (rel_path or "").replace("\\", "/").strip("/").split("/")
    if len(parts) >= 2 and parts[-2] == "source":
        return "/".join(parts[:-2])
    if len(parts) >= 2:
        return "/".join(parts[:-1])
    return "/".join(parts)


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def get_shared_dir(code: str = "default") -> str:
    """从路径注册表取共享目录（启用状态），拼接任务相对路径用。"""
    from app.db.tables import TABLES as _T

    path_table = _T["path_config"].name
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text(
                f"SELECT path FROM {path_table} "
                f"WHERE code = :code AND is_enabled = 1"
            ),
            {"code": code},
        ).first()
    if row is None:
        raise FileJobStateError(f"路径注册表缺少启用中的 code='{code}' 记录，请先在路径管理配置")
    return str(row[0])


def resolve_abs_path(rel_path: str, shared_dir: str) -> str:
    """相对路径 + 共享目录 → 绝对路径（兼容 UNC 与盘符，统一反斜杠拼接）。"""
    base = shared_dir.rstrip("\\/")
    rel = rel_path.lstrip("\\/")
    return f"{base}\\{rel}"


# Windows 文件名保留字符 + 控制字符；UNC 路径下同样非法
_ILLEGAL_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_MAX_STEM_LEN = 80


def sanitize_filename(name: str) -> str:
    """清洗上传文件名，使其可安全落盘（保留中文与扩展名）。

    - 去掉目录成分（防路径穿越）
    - 非法字符替换为下划线，合并连续下划线，去首尾空白与点
    - Windows 保留设备名加前缀规避
    - 主名截断到 80 字符（避免超长路径）
    """
    raw = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if "." in raw:
        stem, dot, suffix = raw.rpartition(".")
        suffix = "." + suffix
    else:
        stem, dot, suffix = raw, "", ""
    stem = _ILLEGAL_FILENAME_CHARS.sub("_", stem)
    stem = re.sub(r"_{2,}", "_", stem).strip(" ._")
    if not stem:
        stem = "unnamed"
    if stem.upper() in _RESERVED_NAMES:
        stem = f"_{stem}"
    if len(stem) > _MAX_STEM_LEN:
        stem = stem[:_MAX_STEM_LEN].rstrip(" ._") or "unnamed"
    suffix = _ILLEGAL_FILENAME_CHARS.sub("", suffix)
    return f"{stem}{suffix}"


def register_job(
    *,
    data: bytes,
    file_name: str,
    rel_path: str,
    category_id: int,
    template_id: int | None = None,
    priority: int = 0,
    code: str | None = None,
) -> FileJobPublic:
    """注册文件任务：hash 去重（重复注册抛 existing），code 唯一。

    code 可由调用方预先算好传入 —— 因为落盘路径里含 job_code，注册路由必须
    先有 code 才能拼出与文件实际位置一致的 rel_path。不传则内部生成。
    """
    file_hash = sha256_of(data)
    engine = get_engine()
    _check_tables()
    # 调用方传入的 code 已经写进 rel_path，碰撞时不能偷偷换号（否则路径与 code 不一致）
    fixed_code = code
    for _ in range(5):  # code 随机碰撞重试
        attempt = fixed_code or generate_code()
        try:
            with engine.begin() as conn:
                dup = conn.execute(
                    text(f"SELECT TOP 1 code FROM {JOB_TABLE} WHERE file_hash = :h"),
                    {"h": file_hash},
                ).first()
                if dup:
                    existing = _fetch_by_code(conn, str(dup[0]))
                    raise FileJobStateError(
                        f"相同文件已注册（{existing.code if existing else dup[0]}），未重复入队",
                        existing=existing,
                    )
                conn.execute(
                    text(
                        f"INSERT INTO {JOB_TABLE} "
                        f"(code, file_hash, file_name, rel_path, file_size, extension, "
                        f" category_id, template_id, state, priority) "
                        f"VALUES (:code, :file_hash, :file_name, :rel_path, :file_size, :extension, "
                        f" :category_id, :template_id, 0, :priority)"
                    ),
                    {
                        "code": attempt,
                        "file_hash": file_hash,
                        "file_name": file_name,
                        "rel_path": rel_path,
                        "file_size": len(data),
                        "extension": ("." + file_name.rsplit(".", 1)[-1].lower()) if "." in file_name else None,
                        "category_id": category_id,
                        "template_id": template_id,
                        "priority": priority,
                    },
                )
                item = _fetch_by_code(conn, attempt)
            if item is None:  # pragma: no cover
                raise FileJobError("注册后读取失败，请重试")
            return item
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if "UQ_" in msg and "_code" in msg:
                if fixed_code:
                    raise FileJobError(f"文件ID {fixed_code} 已存在，请重新上传") from exc
                continue  # code 碰撞，换号重试
            raise
    raise FileJobError("文件ID 生成冲突，请重试")


def reclaim_expired(conn) -> int:
    """租约超时的处理中任务回收：retry+1 回待处理；超过 max_retry 置失败终态。"""
    result = conn.execute(
        text(
            f"UPDATE {JOB_TABLE} SET "
            f"  state = CASE WHEN retry_count + 1 >= max_retry THEN 3 ELSE 0 END, "
            f"  retry_count = retry_count + 1, "
            f"  instance_id = NULL, claimed_datetime = NULL, lease_expires_datetime = NULL, step = NULL, "
            f"  error_msg = CASE WHEN retry_count + 1 >= max_retry THEN N'租约超时且重试耗尽' ELSE error_msg END, "
            f"  updated_datetime = SYSUTCDATETIME() "
            f"WHERE state = 1 AND lease_expires_datetime < SYSUTCDATETIME()"
        )
    )
    return int(result.rowcount or 0)


def claim_job(req: ClaimRequest) -> FileJobPublic | None:
    """抢占一个待处理任务（READPAST 并发安全）；无任务返回 None。抢占前顺带回收到期租约。"""
    engine = get_engine()
    _check_tables()
    with engine.begin() as conn:
        reclaim_expired(conn)
        row = conn.execute(
            text(
                f"UPDATE {JOB_TABLE} WITH (READPAST, ROWLOCK) "
                f"SET state = 1, instance_id = :inst, step = NULL, error_msg = NULL, "
                f"    claimed_datetime = SYSUTCDATETIME(), heartbeat_datetime = SYSUTCDATETIME(), "
                f"    lease_expires_datetime = DATEADD(MINUTE, :lease, SYSUTCDATETIME()), "
                f"    updated_datetime = SYSUTCDATETIME() "
                f"OUTPUT inserted.code "
                f"WHERE id = (SELECT TOP 1 id FROM {JOB_TABLE} WITH (READPAST) "
                f"            WHERE state = 0 ORDER BY priority DESC, id)"
            ),
            {"inst": req.instance_id, "lease": req.lease_minutes},
        ).first()
        if row is None:
            return None
        item = _fetch_by_code(conn, str(row[0]))
    if item is None:  # pragma: no cover
        raise FileJobError("抢占成功但读取失败")
    return item


def _require_owned(conn, code: str, instance_id: str) -> FileJobPublic:
    row = conn.execute(
        text(f"SELECT state, instance_id FROM {JOB_TABLE} WHERE code = :code"),
        {"code": code},
    ).first()
    if row is None:
        raise FileJobNotFoundError("任务不存在")
    if int(row[0]) != 1:
        raise FileJobStateError("任务不在处理中状态")
    if row[1] != instance_id:
        raise FileJobStateError(f"任务已被其它实例占用（{row[1]}）")
    item = _fetch_by_code(conn, code)
    if item is None:  # pragma: no cover
        raise FileJobNotFoundError("任务不存在")
    return item


def heartbeat(code: str, req: HeartbeatRequest) -> FileJobPublic:
    """续约 + 可选上报阶段。"""
    if req.step is not None and req.step not in JOB_STEPS:
        raise FileJobStateError(f"未知阶段：{req.step}（可选 {', '.join(sorted(JOB_STEPS))}）")
    engine = get_engine()
    _check_tables()
    with engine.begin() as conn:
        item = _require_owned(conn, code, req.instance_id)
        conn.execute(
            text(
                f"UPDATE {JOB_TABLE} SET heartbeat_datetime = SYSUTCDATETIME(), "
                f"  lease_expires_datetime = DATEADD(MINUTE, :lease, SYSUTCDATETIME()), "
                f"  step = COALESCE(:step, step), updated_datetime = SYSUTCDATETIME() "
                f"WHERE code = :code"
            ),
            {"lease": req.lease_minutes, "step": req.step, "code": code},
        )
        item = _fetch_by_code(conn, code)
    assert item is not None
    return item


def complete(code: str, req: CompleteRequest) -> FileJobPublic:
    engine = get_engine()
    _check_tables()
    with engine.begin() as conn:
        _require_owned(conn, code, req.instance_id)
        conn.execute(
            text(
                f"UPDATE {JOB_TABLE} SET state = 2, step = NULL, error_msg = NULL, "
                f"  page_count = :pc, output_rel_path = :out, "
                f"  lease_expires_datetime = NULL, updated_datetime = SYSUTCDATETIME(), "
                f"  updated_by = :inst "
                f"WHERE code = :code"
            ),
            {
                "pc": req.page_count,
                "out": req.output_rel_path,
                "inst": req.instance_id,
                "code": code,
            },
        )
        item = _fetch_by_code(conn, code)
    if item is None:  # pragma: no cover
        raise FileJobNotFoundError("任务不存在")
    return item


def fail(code: str, req: FailRequest) -> FileJobPublic:
    """实例主动报失败：retry+1 后未超限回待处理（可被再抢），超限置失败终态。"""
    engine = get_engine()
    _check_tables()
    with engine.begin() as conn:
        item = _require_owned(conn, code, req.instance_id)
        retry_next = item.retry_count + 1
        new_state = 3 if retry_next >= item.max_retry else 0
        conn.execute(
            text(
                f"UPDATE {JOB_TABLE} SET state = :state, retry_count = :retry, "
                f"  error_msg = :msg, instance_id = NULL, claimed_datetime = NULL, "
                f"  lease_expires_datetime = NULL, step = NULL, updated_datetime = SYSUTCDATETIME() "
                f"WHERE code = :code"
            ),
            {
                "state": new_state,
                "retry": retry_next,
                "msg": req.error_msg[:1024],
                "code": code,
            },
        )
        item = _fetch_by_code(conn, code)
    if item is None:  # pragma: no cover
        raise FileJobNotFoundError("任务不存在")
    return item


def cancel(code: str) -> FileJobPublic:
    """取消（仅待处理/失败可取消；处理中不允许，避免实例白干）。"""
    engine = get_engine()
    _check_tables()
    with engine.begin() as conn:
        row = conn.execute(
            text(f"SELECT state FROM {JOB_TABLE} WHERE code = :code"), {"code": code}
        ).first()
        if row is None:
            raise FileJobNotFoundError("任务不存在")
        if int(row[0]) not in (0, 3):
            raise FileJobStateError("仅待处理或失败的任务可取消")
        conn.execute(
            text(
                f"UPDATE {JOB_TABLE} SET state = 4, updated_datetime = SYSUTCDATETIME() "
                f"WHERE code = :code"
            ),
            {"code": code},
        )
        item = _fetch_by_code(conn, code)
    if item is None:  # pragma: no cover
        raise FileJobNotFoundError("任务不存在")
    return item


def retry(code: str) -> FileJobPublic:
    """失败任务手动重投入队（retry 计数清零）。"""
    engine = get_engine()
    _check_tables()
    with engine.begin() as conn:
        row = conn.execute(
            text(f"SELECT state FROM {JOB_TABLE} WHERE code = :code"), {"code": code}
        ).first()
        if row is None:
            raise FileJobNotFoundError("任务不存在")
        if int(row[0]) != 3:
            raise FileJobStateError("仅失败任务可重投")
        conn.execute(
            text(
                f"UPDATE {JOB_TABLE} SET state = 0, retry_count = 0, error_msg = NULL, "
                f"  instance_id = NULL, claimed_datetime = NULL, lease_expires_datetime = NULL, "
                f"  updated_datetime = SYSUTCDATETIME() "
                f"WHERE code = :code"
            ),
            {"code": code},
        )
        item = _fetch_by_code(conn, code)
    if item is None:  # pragma: no cover
        raise FileJobNotFoundError("任务不存在")
    return item


def list_jobs(
    page: int = 1,
    page_size: int = 10,
    state: int | None = None,
    keyword: str | None = None,
) -> FileJobPage:
    page = max(1, page)
    page_size = min(max(1, page_size), 200)
    offset = (page - 1) * page_size

    engine = get_engine()
    _check_tables()
    with engine.connect() as conn:
        reclaim_expired(conn)
        count_stmt = select(func.count()).select_from(_j)
        list_stmt = _select_list_with_progress()
        if state is not None:
            count_stmt = count_stmt.where(_j.c.state == state)
            list_stmt = list_stmt.where(_j.c.state == state)
        if keyword:
            like = f"%{keyword}%"
            cond = _j.c.code.like(like) | _j.c.file_name.like(like)
            count_stmt = count_stmt.where(cond)
            list_stmt = list_stmt.where(cond)
        total = conn.execute(count_stmt).scalar() or 0
        rows = conn.execute(
            list_stmt.order_by(_j.c.id.desc()).limit(page_size).offset(offset)
        ).mappings().all()
    return FileJobPage(
        items=[_row_to_public(r) for r in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )
