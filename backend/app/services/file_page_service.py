"""文件页队列服务：页 = OCR/QR 文字提取任务的抢占单元。

模型：
- 流水线实例抢占 job 行执行 split/normalize/rectify/match/slice，产物通过
  register_pipeline_output 原子登记：页行（进提取队列）+ 切片行（纯产物：
  相对路径 + 页内坐标，供提取 worker 拉图裁块）。模板归属记在页行 template_id，
  切片沿 page_id 反查，不冗余存。
- 提取实例（OCR / QR）claim 页 → get_page_for_extract 拉页信息+切片清单
  → 逐块解析 → 汇总 JSON → complete 回写页行 result_json。
  result_json 固定格式：{"data": {field_key: {value, source, confidence}}}，
  每字段一条，source = OCR / QR（对应模板字段 field_type），confidence 0~1。
- 页状态：0 待提取 / 1 处理中 / 2 成功 / 3 失败。
- 页终态聚合 job：同 job 全部页终态（2/3）→ 有成功则 job=2，全失败则 job=3。
- 租约（lease_expires_datetime）超时自动回收，claim 前顺带执行。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from sqlalchemy import func, select, table as sa_table, column as sa_column, text
from sqlalchemy.engine import Engine

from app.db.config_store import config_store
from app.db.manager import database_manager
from app.db.tables import TABLES
from app.schemas.file_job import ClaimRequest, HeartbeatRequest

PAGE_TABLE = TABLES["file_page"].name
SLICE_TABLE = TABLES["file_slice"].name
JOB_TABLE = TABLES["file_job"].name

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_]+$")

_p = sa_table(
    PAGE_TABLE,
    sa_column("id"), sa_column("job_id"), sa_column("job_code"), sa_column("page_index"),
    sa_column("state"),
    sa_column("orig_rel_path"), sa_column("normalized_rel_path"), sa_column("skew"),
    sa_column("width"), sa_column("height"), sa_column("dpi"), sa_column("template_id"),
    sa_column("match_score"), sa_column("page_mode"),
    sa_column("slice_count"), sa_column("error_msg"),
    sa_column("instance_id"), sa_column("claimed_datetime"), sa_column("lease_expires_datetime"),
    sa_column("heartbeat_datetime"), sa_column("retry_count"), sa_column("max_retry"),
    sa_column("priority"), sa_column("result_json"),
    sa_column("created_by"), sa_column("created_datetime"),
    sa_column("updated_by"), sa_column("updated_datetime"),
)
_s = sa_table(
    SLICE_TABLE,
    sa_column("id"), sa_column("job_id"), sa_column("page_id"),
    sa_column("slice_index"), sa_column("field_key"), sa_column("rel_path"),
    sa_column("x"), sa_column("y"), sa_column("width"), sa_column("height"),
    sa_column("result_content"), sa_column("source"), sa_column("confidence"),
    sa_column("created_by"), sa_column("created_datetime"),
    sa_column("updated_by"), sa_column("updated_datetime"),
)

RESULT_MAX_CHARS = 200000  # 页 result_json 上限（字符）

PAGE_STATE_PENDING = 0      # 待提取
PAGE_STATE_PROCESSING = 1   # 处理中
PAGE_STATE_DONE = 2         # 成功
PAGE_STATE_FAILED = 3       # 失败


class FilePageError(Exception):
    pass


class DatabaseNotConfiguredError(FilePageError):
    pass


class FilePageNotFoundError(FilePageError):
    pass


class FilePageStateError(FilePageError):
    pass


def get_engine() -> Engine:
    engine = database_manager.engine
    if engine is not None:
        return engine
    saved = config_store.load()
    if saved is None:
        raise DatabaseNotConfiguredError("尚未配置数据库连接")
    return database_manager.connect(saved)


def _check_tables() -> None:
    for name in (PAGE_TABLE, SLICE_TABLE, JOB_TABLE):
        if not _IDENTIFIER_RE.match(name):
            raise FilePageError(f"表名配置非法：{name}")


def _page_to_public(row, *, with_result: bool = False) -> dict:
    data = {
        "id": int(row["id"]),
        "job_id": int(row["job_id"]),
        "job_code": row["job_code"],
        "page_index": int(row["page_index"]),
        "state": int(row["state"]),
        "orig_rel_path": row["orig_rel_path"],
        "normalized_rel_path": row["normalized_rel_path"],
        "skew": float(row["skew"]) if row["skew"] is not None else None,
        "width": int(row["width"]) if row["width"] is not None else None,
        "height": int(row["height"]) if row["height"] is not None else None,
        "dpi": int(row["dpi"]) if row["dpi"] is not None else None,
        "template_id": int(row["template_id"]) if row["template_id"] is not None else None,
        "match_score": float(row["match_score"]) if row.get("match_score") is not None else None,
        "page_mode": row.get("page_mode"),
        "slice_count": int(row["slice_count"]) if row["slice_count"] is not None else None,
        "error_msg": row["error_msg"],
        "instance_id": row["instance_id"],
        "claimed_datetime": row["claimed_datetime"],
        "lease_expires_datetime": row["lease_expires_datetime"],
        "heartbeat_datetime": row["heartbeat_datetime"],
        "retry_count": int(row["retry_count"]),
        "max_retry": int(row["max_retry"]),
        "priority": int(row["priority"]),
        "created_datetime": row["created_datetime"],
        "updated_datetime": row["updated_datetime"],
    }
    if with_result:
        data["result"] = json.loads(row["result_json"]) if row.get("result_json") else None
    return data


def _fetch_page(conn, page_id: int, *, with_result: bool = False) -> dict | None:
    cols = [_p] if with_result else [
        _p.c.id, _p.c.job_id, _p.c.job_code, _p.c.page_index, _p.c.state,
        _p.c.orig_rel_path, _p.c.normalized_rel_path, _p.c.skew,
        _p.c.width, _p.c.height, _p.c.dpi, _p.c.template_id,
        _p.c.match_score, _p.c.page_mode, _p.c.slice_count,
        _p.c.error_msg, _p.c.instance_id, _p.c.claimed_datetime,
        _p.c.lease_expires_datetime, _p.c.heartbeat_datetime,
        _p.c.retry_count, _p.c.max_retry, _p.c.priority,
        _p.c.created_datetime, _p.c.updated_datetime,
    ]
    row = conn.execute(select(*cols).where(_p.c.id == page_id)).mappings().first()
    return _page_to_public(row, with_result=with_result) if row is not None else None


def reclaim_expired_pages(conn) -> None:
    """租约超时的处理中页回收：retry+1 回待提取；超限置失败并聚合 job。"""
    expired = conn.execute(
        text(
            f"SELECT id, job_id, retry_count, max_retry FROM {PAGE_TABLE} "
            f"WHERE state = {PAGE_STATE_PROCESSING} "
            f"AND lease_expires_datetime < SYSUTCDATETIME()"
        )
    ).mappings().all()
    for row in expired:
        retry_next = int(row["retry_count"]) + 1
        new_state = PAGE_STATE_FAILED if retry_next >= int(row["max_retry"]) else PAGE_STATE_PENDING
        conn.execute(
            text(
                f"UPDATE {PAGE_TABLE} SET state = :state, retry_count = :retry, "
                f"  instance_id = NULL, claimed_datetime = NULL, lease_expires_datetime = NULL, "
                f"  error_msg = CASE WHEN :state = {PAGE_STATE_FAILED} "
                f"    THEN N'提取租约超时且重试耗尽' ELSE error_msg END, "
                f"  updated_datetime = SYSUTCDATETIME() "
                f"WHERE id = :id"
            ),
            {"state": new_state, "retry": retry_next, "id": int(row["id"])},
        )
        if new_state == PAGE_STATE_FAILED:
            aggregate_job(conn, int(row["job_id"]))


def aggregate_job(conn, job_id: int) -> None:
    """同 job 全部页终态（2/3）时聚合 job 状态（5等待OCR → 2成功/3失败）。幂等。"""
    conn.execute(
        text(
            f"UPDATE {JOB_TABLE} SET "
            f"  state = CASE WHEN EXISTS (SELECT 1 FROM {PAGE_TABLE} "
            f"                WHERE job_id = :jid AND state = {PAGE_STATE_DONE}) "
            f"            THEN 2 ELSE 3 END, "
            f"  step = NULL, updated_datetime = SYSUTCDATETIME() "
            f"WHERE id = :jid AND state = 5 "
            f"  AND NOT EXISTS (SELECT 1 FROM {PAGE_TABLE} "
            f"                  WHERE job_id = :jid AND state IN (0, 1))"
        ),
        {"jid": job_id},
    )


def claim_page(req: ClaimRequest) -> dict | None:
    """提取实例抢占一个待提取页，**一次返回页信息 + 该页全部切片**（免二次拉取）。

    无任务返回 null。claim 前顺带回收到期租约。
    """
    engine = get_engine()
    _check_tables()
    with engine.begin() as conn:
        reclaim_expired_pages(conn)
        row = conn.execute(
            text(
                f"UPDATE {PAGE_TABLE} WITH (READPAST, ROWLOCK) "
                f"SET state = {PAGE_STATE_PROCESSING}, instance_id = :inst, error_msg = NULL, "
                f"    claimed_datetime = SYSUTCDATETIME(), heartbeat_datetime = SYSUTCDATETIME(), "
                f"    lease_expires_datetime = DATEADD(MINUTE, :lease, SYSUTCDATETIME()), "
                f"    updated_datetime = SYSUTCDATETIME() "
                f"OUTPUT inserted.id "
                f"WHERE id = (SELECT TOP 1 id FROM {PAGE_TABLE} WITH (READPAST) "
                f"            WHERE state = {PAGE_STATE_PENDING} ORDER BY priority DESC, id)"
            ),
            {"inst": req.instance_id, "lease": req.lease_minutes},
        ).first()
        if row is None:
            return None
        item = _fetch_page(conn, int(row[0]))
        if item is not None:
            item["slices"] = get_page_slices(conn, item["id"])
    if item is None:  # pragma: no cover
        raise FilePageError("抢占成功但读取失败")
    return item


def get_page_slices(conn, page_id: int) -> list[dict]:
    """页下切片清单（路径+坐标+field_key）。模板归属沿页行 template_id。"""
    return [
        {
            "id": int(r["id"]),
            "slice_index": int(r["slice_index"]),
            "field_key": r["field_key"],
            "rel_path": r["rel_path"],
            "x": int(r["x"]),
            "y": int(r["y"]),
            "width": int(r["width"]),
            "height": int(r["height"]),
        }
        for r in conn.execute(
            select(_s).where(_s.c.page_id == page_id).order_by(_s.c.slice_index)
        ).mappings().all()
    ]


def get_page_for_extract(page_id: int, instance_id: str) -> dict:
    """提取实例拉任务详情：页信息 + 该页切片列表（校验归属）。"""
    engine = get_engine()
    _check_tables()
    with engine.connect() as conn:
        item = _fetch_page(conn, page_id)
        if item is None:
            raise FilePageNotFoundError("页不存在")
        if item["instance_id"] != instance_id or item["state"] != PAGE_STATE_PROCESSING:
            raise FilePageStateError("页不在本实例的处理中状态")
        item["slices"] = get_page_slices(conn, page_id)
    return item


def _require_owned_page(conn, page_id: int, instance_id: str) -> dict:
    row = conn.execute(
        text(f"SELECT state, instance_id FROM {PAGE_TABLE} WHERE id = :id"),
        {"id": page_id},
    ).first()
    if row is None:
        raise FilePageNotFoundError("页不存在")
    if int(row[0]) != PAGE_STATE_PROCESSING:
        raise FilePageStateError("页不在处理中状态")
    if row[1] != instance_id:
        raise FilePageStateError(f"页已被其它实例占用（{row[1]}）")
    item = _fetch_page(conn, page_id)
    if item is None:  # pragma: no cover
        raise FilePageNotFoundError("页不存在")
    return item


def heartbeat_page(page_id: int, req: HeartbeatRequest) -> dict:
    engine = get_engine()
    _check_tables()
    with engine.begin() as conn:
        _require_owned_page(conn, page_id, req.instance_id)
        conn.execute(
            text(
                f"UPDATE {PAGE_TABLE} SET heartbeat_datetime = SYSUTCDATETIME(), "
                f"  lease_expires_datetime = DATEADD(MINUTE, :lease, SYSUTCDATETIME()), "
                f"  updated_datetime = SYSUTCDATETIME() "
                f"WHERE id = :id"
            ),
            {"lease": req.lease_minutes, "id": page_id},
        )
        item = _fetch_page(conn, page_id)
    assert item is not None
    return item


def complete_page(
    page_id: int, *, instance_id: str, result: dict, slice_paths: dict | None = None
) -> dict:
    """回写整页提取结果，**同事务双写两处**：

    1. 切片行（Sup_EWP_DCR_FileSlice）：按 field_key 匹配，写
       result_content / source / confidence —— 每块解析结果落自己行；
    2. 页行（Sup_EWP_DCR_FilePage）：result_json 汇总整页
       {"data": {field_key: {value, source, confidence}}}，页置成功并聚合 job。

    result 为 {"data": {...}}，由路由层从 PageResultRequest 组装；
    data 里没有对应 field_key 的切片行保持原值不动。

    slice_paths: {slice_index: rel_path}，提取时把切片图落盘后的相对路径回写
      FileSlice.rel_path（按 slice_index 匹配：整页回退块 field_key 为 NULL，
      按 field_key 匹配会漏）。为 None 则不动该列。
    """
    payload = json.dumps(result, ensure_ascii=False)
    if len(payload) > RESULT_MAX_CHARS:
        raise FilePageStateError(f"结果 JSON 超过 {RESULT_MAX_CHARS} 字符上限")
    data_map: dict = result.get("data") or {}
    engine = get_engine()
    _check_tables()
    with engine.begin() as conn:
        item = _require_owned_page(conn, page_id, instance_id)
        # 0) 切片图路径回写（按 slice_index 精确匹配，含 field_key 为 NULL 的回退块）
        for idx, rel in (slice_paths or {}).items():
            conn.execute(
                text(
                    f"UPDATE {SLICE_TABLE} SET rel_path = :rel, "
                    f"  updated_datetime = SYSUTCDATETIME(), updated_by = :inst "
                    f"WHERE page_id = :pid AND slice_index = :idx"
                ),
                {"rel": rel, "inst": instance_id, "pid": page_id, "idx": int(idx)},
            )
        # 1) 切片行逐块回写（按 field_key 匹配该页切片）
        for field_key, entry in data_map.items():
            conn.execute(
                text(
                    f"UPDATE {SLICE_TABLE} SET result_content = :val, source = :src, "
                    f"  confidence = :conf, updated_datetime = SYSUTCDATETIME(), "
                    f"  updated_by = :inst "
                    f"WHERE page_id = :pid AND field_key = :fk"
                ),
                {
                    "val": entry.get("value"),
                    "src": entry.get("source"),
                    "conf": entry.get("confidence"),
                    "inst": instance_id,
                    "pid": page_id,
                    "fk": field_key,
                },
            )
        # 2) 页行汇总 JSON
        conn.execute(
            text(
                f"UPDATE {PAGE_TABLE} SET state = {PAGE_STATE_DONE}, result_json = :rj, "
                f"  error_msg = NULL, lease_expires_datetime = NULL, "
                f"  updated_datetime = SYSUTCDATETIME(), updated_by = :inst "
                f"WHERE id = :id"
            ),
            {"rj": payload, "inst": instance_id, "id": page_id},
        )
        aggregate_job(conn, item["job_id"])
        item = _fetch_page(conn, page_id, with_result=True)
    assert item is not None
    return item


def fail_page(page_id: int, *, instance_id: str, error_msg: str) -> dict:
    """主动报失败：retry+1 未超限回队，超限置失败并聚合。"""
    engine = get_engine()
    _check_tables()
    with engine.begin() as conn:
        item = _require_owned_page(conn, page_id, instance_id)
        retry_next = item["retry_count"] + 1
        new_state = PAGE_STATE_FAILED if retry_next >= item["max_retry"] else PAGE_STATE_PENDING
        conn.execute(
            text(
                f"UPDATE {PAGE_TABLE} SET state = :state, retry_count = :retry, "
                f"  error_msg = :msg, instance_id = NULL, claimed_datetime = NULL, "
                f"  lease_expires_datetime = NULL, updated_datetime = SYSUTCDATETIME() "
                f"WHERE id = :id"
            ),
            {"state": new_state, "retry": retry_next, "msg": error_msg[:1024], "id": page_id},
        )
        if new_state == PAGE_STATE_FAILED:
            aggregate_job(conn, item["job_id"])
        item = _fetch_page(conn, page_id)
    assert item is not None
    return item


def list_pages_by_job(job_code: str) -> list[dict]:
    engine = get_engine()
    _check_tables()
    with engine.connect() as conn:
        rows = conn.execute(
            select(_p.c.id, _p.c.job_id, _p.c.job_code, _p.c.page_index, _p.c.state,
                   _p.c.orig_rel_path, _p.c.normalized_rel_path, _p.c.skew,
                   _p.c.width, _p.c.height, _p.c.dpi, _p.c.template_id,
                   _p.c.match_score, _p.c.page_mode, _p.c.slice_count,
                   _p.c.error_msg, _p.c.instance_id, _p.c.claimed_datetime,
                   _p.c.lease_expires_datetime, _p.c.heartbeat_datetime,
                   _p.c.retry_count, _p.c.max_retry, _p.c.priority,
                   _p.c.created_datetime, _p.c.updated_datetime)
            .where(_p.c.job_code == job_code)
            .order_by(_p.c.page_index)
        ).mappings().all()
    return [_page_to_public(r) for r in rows]


def register_pipeline_output(job_code: str, instance_id: str, pages: list[dict]) -> dict:
    """流水线登记产物：页行（进提取队列）+ 切片行（纯产物：路径+坐标）。

    pages: [{page_index, orig_rel_path, normalized_rel_path, skew, width, height,
             dpi, template_id, slices: [{slice_index, field_key, rel_path, x, y, width, height}]}]
    无切片的页直接置成功（无内容可提取）。job 转"等待OCR"（5）。
    """
    engine = get_engine()
    _check_tables()
    with engine.begin() as conn:
        job = conn.execute(
            text(f"SELECT id, state, instance_id FROM {JOB_TABLE} WHERE code = :code"),
            {"code": job_code},
        ).first()
        if job is None:
            raise FilePageNotFoundError("任务不存在")
        job_id = int(job[0])
        if int(job[1]) != 1 or job[2] != instance_id:
            raise FilePageStateError("任务不在本实例的处理中状态，无法登记产物")
        existing = conn.execute(
            text(f"SELECT COUNT(*) FROM {PAGE_TABLE} WHERE job_id = :jid"), {"jid": job_id}
        ).scalar()
        if existing:
            raise FilePageStateError("该任务已登记过分页产物，不能重复登记")

        page_count = 0
        slice_count = 0
        pending = 0
        for p in pages:
            slices = p.get("slices") or []
            page_state = PAGE_STATE_PENDING if slices else PAGE_STATE_DONE
            if slices:
                pending += 1
            page_row = conn.execute(
                text(
                    f"INSERT INTO {PAGE_TABLE} "
                    f"(job_id, job_code, page_index, state, orig_rel_path, normalized_rel_path, "
                    f" skew, width, height, dpi, template_id, match_score, page_mode, slice_count) "
                    f"OUTPUT inserted.id "
                    f"VALUES (:job_id, :job_code, :page_index, :state, :orig, :norm, "
                    f" :skew, :w, :h, :dpi, :tid, :ms, :pm, :sc)"
                ),
                {
                    "job_id": job_id,
                    "job_code": job_code,
                    "page_index": int(p["page_index"]),
                    "state": page_state,
                    "orig": p.get("orig_rel_path"),
                    "norm": p.get("normalized_rel_path"),
                    "skew": p.get("skew"),
                    "w": p.get("width"),
                    "h": p.get("height"),
                    "dpi": p.get("dpi"),
                    "tid": p.get("template_id"),
                    "ms": p.get("match_score"),
                    "pm": p.get("page_mode"),
                    "sc": len(slices),
                },
            ).first()
            page_id = int(page_row[0])
            page_count += 1
            for sl in slices:
                conn.execute(
                    text(
                        f"INSERT INTO {SLICE_TABLE} "
                        f"(job_id, page_id, slice_index, field_key, rel_path, x, y, width, height) "
                        f"VALUES (:job_id, :page_id, :idx, :fkey, :rel, :x, :y, :w, :h)"
                    ),
                    {
                        "job_id": job_id,
                        "page_id": page_id,
                        "idx": int(sl["slice_index"]),
                        "fkey": sl.get("field_key"),
                        "rel": sl.get("rel_path"),
                        "x": int(sl["x"]),
                        "y": int(sl["y"]),
                        "w": int(sl["width"]),
                        "h": int(sl["height"]),
                    },
                )
                slice_count += 1

        if pending == 0:
            conn.execute(
                text(
                    f"UPDATE {JOB_TABLE} SET state = 2, step = NULL, page_count = :pc, "
                    f"  lease_expires_datetime = NULL, updated_datetime = SYSUTCDATETIME(), "
                    f"  updated_by = :inst "
                    f"WHERE id = :jid"
                ),
                {"pc": page_count, "inst": instance_id, "jid": job_id},
            )
        else:
            conn.execute(
                text(
                    f"UPDATE {JOB_TABLE} SET state = 5, step = N'ocr', page_count = :pc, "
                    f"  lease_expires_datetime = NULL, updated_datetime = SYSUTCDATETIME(), "
                    f"  updated_by = :inst "
                    f"WHERE id = :jid"
                ),
                {"pc": page_count, "inst": instance_id, "jid": job_id},
            )
    return {"job_code": job_code, "page_count": page_count, "slice_count": slice_count, "pending_pages": pending}


def _field_to_coord(f) -> dict:
    """模板字段 → 坐标信息条目（含是否二维码区域）。"""
    return {
        "field_key": f.field_key,
        "label": f.label,
        "field_type": f.field_type,
        "is_qr": f.field_type == "qr",
        "x": f.x,
        "y": f.y,
        "width": f.width,
        "height": f.height,
        "pad": f.pad,
        "sort_order": f.sort_order,
        "space": "ref_image",  # 坐标系：范本图像素空间
    }


def summarize_templates(used_ids: list[int]) -> list[dict]:
    """按模板ID汇总模板信息与全部字段坐标（供结果 JSON 的 templates 段）。"""
    from app.services import template_field_service, template_service

    out: list[dict] = []
    for tid in sorted({i for i in used_ids if i is not None}):
        try:
            tpl = template_service.get_template(tid)
        except Exception:  # noqa: BLE001 - 模板可能已被删（孤儿引用）
            out.append({"id": tid, "code": None, "name": None, "fields": [], "missing": True})
            continue
        try:
            fields = [_field_to_coord(f) for f in template_field_service.list_fields(tid)]
        except Exception:  # noqa: BLE001
            fields = []
        out.append(
            {
                "id": tpl.id,
                "code": tpl.code,
                "name": tpl.name,
                "category_id": tpl.category_id,
                "category_name": tpl.category_name,
                "dpi": tpl.dpi,
                "declared_width": tpl.width,
                "declared_height": tpl.height,
                "is_enabled": tpl.is_enabled,
                "fields": fields,
            }
        )
    return out


def _score_band(score: float | None) -> str:
    from app.services.template_match import SCORE_AUTO, SCORE_REJECT, score_band

    if score is None:
        return "unknown"
    return score_band(score)


def get_job_result(job_code: str) -> dict | None:
    """反查拼装完整结果。

    返回结构（三大段）：
      info        —— 文件任务信息（文件、页数、附件、执行时间、状态）
      legend      —— 提示图例（分类、模板、匹配度、附件及页数、执行时间、状态）
      templates   —— 涉及到的所有模板坐标信息（字段名称、坐标、是否二维码区域）
      pages       —— 逐页产物（页图、结果、切片坐标与提取值）
    """
    engine = get_engine()
    _check_tables()
    with engine.connect() as conn:
        job = conn.execute(
            text(
                f"SELECT j.id, j.code, j.state, j.step, j.file_name, j.rel_path, j.file_size, "
                f"       j.extension, j.category_id, j.template_id, j.page_count, j.error_msg, "
                f"       j.created_by, j.created_datetime, j.updated_by, j.updated_datetime, "
                f"       c.code AS category_code, c.name AS category_name "
                f"FROM {JOB_TABLE} j "
                f"LEFT JOIN {TABLES['category'].name} c ON c.id = j.category_id "
                f"WHERE j.code = :code"
            ),
            {"code": job_code},
        ).mappings().first()
        if job is None:
            return None
        job_id = int(job["id"])
        pages = conn.execute(
            text(
                f"SELECT id, page_index, state, normalized_rel_path, orig_rel_path, skew, "
                f"width, height, dpi, template_id, match_score, page_mode, slice_count, "
                f"result_json, error_msg, created_datetime, updated_datetime "
                f"FROM {PAGE_TABLE} WHERE job_id = :jid ORDER BY page_index"
            ),
            {"jid": job_id},
        ).mappings().all()
        slices = conn.execute(
            text(
                f"SELECT page_id, slice_index, field_key, rel_path, x, y, width, height, "
                f"result_content, source, confidence "
                f"FROM {SLICE_TABLE} WHERE job_id = :jid ORDER BY page_id, slice_index"
            ),
            {"jid": job_id},
        ).mappings().all()

    from app.schemas.file_job import JOB_STATES

    slices_by_page: dict[int, list[dict]] = {}
    for s in slices:
        slices_by_page.setdefault(int(s["page_id"]), []).append(
            {
                "slice_index": int(s["slice_index"]),
                "field_key": s["field_key"],
                "rel_path": s["rel_path"],
                "x": int(s["x"]),
                "y": int(s["y"]),
                "width": int(s["width"]),
                "height": int(s["height"]),
                "result_content": s["result_content"],
                "source": s["source"],
                "confidence": float(s["confidence"]) if s["confidence"] is not None else None,
            }
        )

    state = int(job["state"])
    page_count = int(job["page_count"]) if job["page_count"] is not None else len(pages)

    # 逐页产物的公共字段（先构建；模板角色统计依赖其 match_band）
    page_items = []
    for p in pages:
        score = float(p["match_score"]) if p["match_score"] is not None else None
        page_items.append(
            {
                "page_index": int(p["page_index"]),
                "state": int(p["state"]),
                "normalized_rel_path": p["normalized_rel_path"],
                "orig_rel_path": p["orig_rel_path"],
                "skew": float(p["skew"]) if p["skew"] is not None else None,
                "width": int(p["width"]) if p["width"] is not None else None,
                "height": int(p["height"]) if p["height"] is not None else None,
                "dpi": int(p["dpi"]) if p["dpi"] is not None else None,
                "template_id": int(p["template_id"]) if p["template_id"] is not None else None,
                "match_score": round(score, 4) if score is not None else None,
                "match_band": _score_band(score),
                "page_mode": p["page_mode"],
                "result": json.loads(p["result_json"]) if p["result_json"] else None,
                "error_msg": p["error_msg"],
                "slices": slices_by_page.get(int(p["id"]), []),
            }
        )

    # 已匹配 = 相关度达 auto/low 档并**套用了字段坐标**的页；reject 仅记录"最接近谁"不算匹配
    matched_pages = [p for p in page_items if p["match_band"] in ("auto", "low")]
    rejected_pages = [p for p in page_items if p["match_band"] == "reject"]
    scores = [p["match_score"] for p in matched_pages if p["match_score"] is not None]
    all_scores = [p["match_score"] for p in page_items if p["match_score"] is not None]

    # 「涉及到的模板」= 强制模板 + 真正命中的模板 +（无强制时）该分类全部启用模板（候选）
    used_ids = {p["template_id"] for p in matched_pages if p["template_id"] is not None}
    forced_id = int(job["template_id"]) if job["template_id"] is not None else None
    candidate_ids: set[int] = set()
    if forced_id is None:
        from app.services import template_service

        try:
            cand_page = template_service.list_templates(
                page=1, page_size=200, category_id=int(job["category_id"])
            )
            candidate_ids = {t.id for t in cand_page.items if t.is_enabled}
        except Exception:  # noqa: BLE001 - 模板查询失败不影响结果主体
            candidate_ids = set()
    all_ids = used_ids | candidate_ids | ({forced_id} if forced_id is not None else set())
    templates = summarize_templates(list(all_ids))
    # 标注每个模板的角色，供前端图例区分"候选/命中"
    for t in templates:
        t["used_on_pages"] = t["id"] in used_ids
        t["is_forced"] = t["id"] == forced_id
        t["is_candidate"] = t["id"] in candidate_ids
    return {
        "code": str(job["code"]),
        "state": state,
        # —— 段1：文件任务信息 ——
        "info": {
            "code": str(job["code"]),
            "file_name": job["file_name"],
            "rel_path": job["rel_path"],
            "file_size": int(job["file_size"]),
            "extension": job["extension"],
            "page_count": page_count,
            "attachment_pages": len(pages),   # 附件实际解析出的页/块数
            "category_id": int(job["category_id"]),
            "category_code": job["category_code"],
            "category_name": job["category_name"],
            "forced_template_id": int(job["template_id"]) if job["template_id"] is not None else None,
            "state": state,
            "state_label": JOB_STATES.get(state, str(state)),
            "step": job["step"],
            "error_msg": job["error_msg"],
            "created_by": job["created_by"],
            "created_datetime": job["created_datetime"],
            "updated_by": job["updated_by"],
            "updated_datetime": job["updated_datetime"],
        },
        # —— 段2：提示图例 ——
        "legend": {
            "category": {
                "id": int(job["category_id"]),
                "code": job["category_code"],
                "name": job["category_name"],
            },
            "templates": [
                {"id": t["id"], "code": t["code"], "name": t["name"]}
                for t in templates
            ],
            "match": {
                # score = 已达匹配档的页里最高分；均为 None 时退回展示"最接近"的分（含 reject 页）
                "score": round(max(scores), 4) if scores else (round(max(all_scores), 4) if all_scores else None),
                "min_score": round(min(scores), 4) if scores else None,
                "band": _score_band(max(scores) if scores else None),
                "matched_pages": len(matched_pages),
                "rejected_pages": len(rejected_pages),
                "bands": {"auto": 0.75, "low": 0.45, "reject": 0.0, "unknown": None},
            },
            "attachment": {
                "file_name": job["file_name"],
                "page_count": page_count,
                "parsed_pages": len(pages),
            },
            "executed_at": job["updated_datetime"] or job["created_datetime"],
            "created_at": job["created_datetime"],
            "state": state,
            "state_label": JOB_STATES.get(state, str(state)),
        },
        # —— 段3：涉及到的所有模板坐标信息 ——
        "templates": templates,
        "pages": page_items,
    }
