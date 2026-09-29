"""流水线 worker：抢占文件任务并跑完整条处理链。

用法（在 backend/ 目录下）：
    python worker.py run        常驻循环，持续消费队列（前台驻留，Ctrl+C 退出）
    python worker.py once       只跑一个任务就退出（调试用，退出码 0成功/1失败/2空队列）
    python worker.py status     查看当前队列状态

闭环：
    claim job → 确定模板 → 渲染并**按模板切块**落盘（一页多支票 → 多块）
    → 每块登记为一条页记录（含该块字段切片）→ job 转等待OCR
    → 逐页 claim → 按 field_type 裁块 → OCR / QR → complete 双写回写
    → 全部页终态后 job 聚合为成功/失败

产物路径（一个任务 = 一个目录，路径从 job.rel_path 反推，见
file_job_service.task_dir_of）：
    jobs/{分类code}/{yyyyMM}/{yyyyMMdd}/{job_code}/source/{原文件名}   上传原件
    jobs/{分类code}/{yyyyMM}/{yyyyMMdd}/{job_code}/pages/*.png         配准通过的页/块图
    jobs/{分类code}/{yyyyMM}/{yyyyMMdd}/{job_code}/pages/{块名}/       该块的切片子目录
        {field_key}.png                                                每字段一张切片图
    jobs/{分类code}/{yyyyMM}/{yyyyMMdd}/{job_code}/reject/*.png        相关度<0.45，待审
切片图是**页图的子目录**（与页图同名、去扩展名），随页走便于按页审计；
提取时裁切落盘并把相对路径回写 FileSlice.rel_path，与 OCR 用同一份裁切。

配准要点（真机验证 + 受控实验得出，改动前务必先读）：
1. 模板字段坐标存在**范本图空间**（ref_image 像素系），不是模板声明的 width/height。
   每页用 ECC 求「范本坐标 → 页图坐标」仿射矩阵 W，字段四角过 W 后
   × scale（页图宽/范本宽）即得页图坐标。**只映射坐标，绝不 warp 整图**
   （warp 会丢像素细节）。已封装在 template_match.align_page / map_box。
2. 整页模板（声明尺寸 ≈ 页图尺寸）走 align_page，**不切块**；
   局部块模板（一页多支票）先用 find_template_boxes 粗定位再 align_block 精配准。
   类型由 template_match.detect_page_mode 按声明尺寸自动判别。
3. ECC 相关度（0~1）直接作为页的 match_score 落库，供前端分档提示：
   ≥0.75 自动通过 / 0.45~0.75 低置信 / <0.45 拒绝。
4. **多模板自动选版**：分类下有多个启用模板时，把全部候选交给 page_render，
   逐页对每个候选做整页 ECC，相关度最高者即该页模板。
   ⚠ 不能用 list_templates 的列表项当模板（不含 ref_image，会静默退化为无模板整页）。
5. 每页的 template_id 可能不同，**字段清单必须按块的实际 template_id 取**。

启动方式（二选一，勿同时用）：
A. **后端自动挂载（默认，推荐）**：FastAPI 启动时由 app/core/worker_mount 起一个
   daemon 线程消费队列（见 start_background）。跨进程文件锁保证「后端 + 手工 worker」
   同时起来时只有一方真正消费。受 settings.worker_enabled 开关控制。
   改动 app/worker 相关代码后需**重启后端**才生效。
B. **手工前台运行**：python worker.py run —— 调试、排障、或后端未启动时使用。
   文件锁只约束「自动挂载」这一路（防止两个后端进程各起一个后台线程）；
   手工 CLI 不碰锁，因此可与后端内置 worker 并存——两者靠 claim 的
   READPAST+ROWLOCK 保证不重复领取同一任务。

多实例：claim 的 READPAST+ROWLOCK 保证多消费者不重复领取同一任务。
优雅停止：run_forever / start_background 均接受 stop_event；置位后不再抢新任务，
当前任务跑完即返回（后端 lifespan 关闭时调用 unmount 触发）。
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from PIL import Image

from app.config import settings
from app.core.ocr_engine import ocr_bytes
from app.core.qr_decode import decode_bytes
from app.schemas.file_job import ClaimRequest, FailRequest, HeartbeatRequest
from app.services import file_job_service, file_page_service, page_render
from app.services import template_field_service, template_service
from app.services.file_job_service import get_shared_dir, resolve_abs_path, sanitize_filename

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("worker")

LEASE_MINUTES = 10
IDLE_SLEEP_SECONDS = 3.0

# 多实例防重：同进程内只允许挂载一个后台 worker 线程
_bg_lock = threading.Lock()
_bg_started = False
_bg_thread: threading.Thread | None = None


class JobFailed(Exception):
    """本任务不可继续，需标记失败。"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _read_file(rel_path: str, shared_dir: str) -> bytes:
    import pathlib

    abs_path = pathlib.Path(resolve_abs_path(rel_path, shared_dir))
    if not abs_path.exists():
        raise JobFailed(f"源文件不存在：{rel_path}")
    return abs_path.read_bytes()


def _crop(img, x: int, y: int, w: int, h: int, pad: int):
    """按坐标（含 pad 外扩）裁块，返回 (PNG 字节, 裁切框)。越界返回 (None, None)。"""
    sx = max(0, x - pad)
    sy = max(0, y - pad)
    ex = min(img.width, x + w + pad)
    ey = min(img.height, y + h + pad)
    if ex <= sx or ey <= sy:
        return None, None
    buf = BytesIO()
    img.crop((sx, sy, ex, ey)).save(buf, format="PNG")
    return buf.getvalue(), (sx, sy, ex, ey)


def slice_dir_rel(page_rel_path: str) -> str:
    """页图相对路径 → 该页的切片目录（与页图同名、去扩展名）。

    jobs/{分类code}/.../pages/JOBxxx.p0c0.png → jobs/.../pages/JOBxxx.p0c0
    切片图是页图的**子目录**，随页走，便于按页审计。

    只剥已知图片后缀：页图名本身含点（JOBxxx.p0c0），
    不能把名字里的点当扩展名（会把 JOBxxx.p0c0 误剥成 JOBxxx）。
    """
    norm = (page_rel_path or "").replace("\\", "/").strip("/")
    low = norm.lower()
    for ext in (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"):
        if low.endswith(ext):
            return norm[: -len(ext)]
    return norm


def _save_slice(
    img, *, page_rel_path: str, field_key: str | None, slice_index: int,
    x: int, y: int, w: int, h: int, pad: int, shared_dir: str,
) -> str | None:
    """把一块切片图落到「页图同名子目录」下，返回相对路径（失败返回 None）。

    文件名用 field_key.png；无 field_key（整页回退块）**不落盘**——那等价于复制
    整页图，N 页无模板就 N 张重复大图，没有意义（页图本身即产物）。
    落盘与 OCR 用的是同一份裁切逻辑（_crop），保证「图上看到的 = 识别用的」。
    """
    if not field_key:
        return None
    crop, box = _crop(img, x, y, w, h, pad)
    if crop is None:
        return None
    stem = sanitize_filename(field_key)
    if not stem.lower().endswith(".png"):
        stem = f"{stem}.png"
    rel = f"{slice_dir_rel(page_rel_path)}/{stem}"
    abs_path = Path(resolve_abs_path(rel, shared_dir))
    try:
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(crop)
    except OSError as exc:  # noqa: BLE001 - 切片落盘失败不拖垮整页提取
        log.warning("切片落盘失败 %s：%s", rel, exc)
        return None
    return rel



def _project_field(field, align: dict, bw: int, bh: int) -> tuple[int, int, int, int]:
    """把模板字段坐标投影到块图坐标。

    - 有 ECC 矩阵：字段四角 × W × scale（+ 块偏移），取外接矩形 —— 含旋转/平移校正。
    - 无矩阵（无模板/配准失败）：退回等比缩放（f.x × scale）。
    越界不在此处理，由调用方按块尺寸裁剪。
    """
    matrix = align.get("matrix")
    scale = align.get("scale", 1.0) or 1.0
    if matrix is None:
        return (
            round(field.x * scale),
            round(field.y * scale),
            round(field.width * scale),
            round(field.height * scale),
        )
    from app.services import template_match

    return template_match.map_box(
        matrix,
        scale,
        float(field.x),
        float(field.y),
        float(field.width),
        float(field.height),
        offset=align.get("offset") or (0.0, 0.0),
    )


def _resolve_templates(job) -> list:
    """确定本任务的候选模板列表（按 ECC 相关度逐页选版）。

    优先级：实例强制模板 > 该分类下全部启用模板 > 无模板（整页 text）。
    返回**模板详情对象**列表（含 ref_image），供 page_render 逐页打分选版。

    ⚠ 必须用 get_template 取详情：list_templates 为省流量**不返回 ref_image**，
    拿列表项当模板喂给 page_render 会导致「无范本图」→ ECC 配准与切块全部跳过，
    静默退化为整页文本提取（已踩坑，勿改回）。
    """
    if job.template_id is not None:
        return [template_service.get_template(job.template_id)]

    page = template_service.list_templates(page=1, page_size=200, category_id=job.category_id)
    enabled = [t for t in page.items if t.is_enabled]
    if not enabled:
        return []
    details = []
    for t in enabled:
        try:
            details.append(template_service.get_template(t.id))
        except Exception as exc:  # noqa: BLE001 - 单个模板异常不影响其它候选
            log.warning("模板 #%s 读取失败，跳过：%s", t.id, exc)
    return details


def process_job(job) -> None:
    """处理单个已抢占的任务（state=1、instance_id 为本实例）。"""
    code = job.code
    inst = job.instance_id
    assert inst is not None
    shared_dir = get_shared_dir("default")
    # 任务目录从上传件 rel_path 反推（去掉尾部 source/{文件名}）：
    # 与注册端天然一致，不用再查分类、也不用重算日期。页产物落其 pages/ 与 reject/。
    task_dir = file_job_service.task_dir_of(job.rel_path)

    def beat(step: str) -> None:
        file_job_service.heartbeat(
            code, HeartbeatRequest(instance_id=inst, step=step, lease_minutes=LEASE_MINUTES)
        )

    # ① 读源文件
    data = _read_file(job.rel_path, shared_dir)
    log.info("[%s] 源文件 %s（%.1f KB）", code, job.rel_path, len(data) / 1024)

    # ② 确定候选模板 → 决定渲染 dpi（多模板时由 page_render 逐页 ECC 选版）
    templates = _resolve_templates(job)
    if not templates:
        render_dpi = page_render.DEFAULT_DPI
    else:
        render_dpi = next(
            (t.dpi for t in templates if t.dpi), page_render.DEFAULT_DPI
        )
    log.info(
        "[%s] 候选模板 %s，按 %sdpi 渲染",
        code,
        ", ".join(f"#{t.id} {t.code}" for t in templates) if templates else "无（整页处理）",
        render_dpi,
    )

    # ③ 渲染 + 配准选版 + 切块落盘（一页多支票 → 多块）
    beat("split")
    rendered = page_render.render_and_save(
        data=data,
        filename=job.file_name,
        task_dir=task_dir,
        job_code=code,
        shared_dir=shared_dir,
        dpi=render_dpi,
        templates=templates,
    )
    blocks = rendered["blocks"]
    scores = [b["match_score"] for b in blocks if b.get("match_score") is not None]
    modes = {b.get("page_mode") for b in blocks if b.get("page_mode")}
    log.info(
        "[%s] 渲染完成：源 %d 页 → 切出 %d 块（配准通过 %d，隔离 %d，%s，相关度 %s）→ %s/pages/",
        code,
        rendered["page_count"],
        len(blocks),
        rendered["matched"],
        rendered.get("rejected", 0),
        "/".join(sorted(modes)) or "无模板",
        f"{min(scores):.3f}~{max(scores):.3f}" if scores else "-",
        task_dir,
    )

    # ④ 组装页 + 切片登记载荷（每块 = 一条页记录；字段四角经 ECC 矩阵投影到块图坐标）
    #    注意：多模板选版后**每页可能命中不同模板**，字段清单须按块的实际 template_id 取
    fields_cache: dict[int, list] = {
        t.id: template_field_service.list_fields(t.id) for t in templates
    }
    pages_payload = []
    for b in blocks:
        bw, bh = b["width"], b["height"]
        align = b.get("align") or {}
        # 相关度闸门：reject 页不套用字段坐标（套了只会提取垃圾），退化为整页文本。
        # 保留 template_id / match_score 落库，供前端提示「最接近哪个模板、差多少」。
        usable = b.get("match_band") in ("auto", "low")
        fields = fields_cache.get(b.get("template_id"), []) if usable else []
        if fields:
            slices = []
            for i, f in enumerate(fields):
                # 有 ECC 矩阵 → 映射字段四角（含旋转/平移校正）；否则退回等比缩放
                x, y, w, h = _project_field(f, align, bw, bh)
                # 裁掉越界部分（映射框可能压在块边）
                if x >= bw or y >= bh:
                    continue
                slices.append(
                    {
                        "slice_index": len(slices),
                        "field_key": f.field_key,
                        "rel_path": None,  # 切片图在提取时裁切落盘，再把相对路径回写本列
                        "x": x,
                        "y": y,
                        "width": min(w, bw - x),
                        "height": min(h, bh - y),
                        "_pad": round(f.pad * align.get("scale", 1.0)),
                        "_type": f.field_type,
                    }
                )
            if not slices:
                log.warning("[%s] 块 %s 无有效字段区域，作整页块处理", code, b["page_index"])
        if not fields or not slices:
            slices = [
                {
                    "slice_index": 0,
                    "field_key": None,  # 整页回退块：无字段，不落切片图（页图本身即产物）
                    "rel_path": None,
                    "x": 0,
                    "y": 0,
                    "width": bw,
                    "height": bh,
                    "_pad": 0,
                    "_type": "text",
                }
            ]
        pages_payload.append(
            {
                "page_index": b["page_index"],
                "orig_rel_path": b["rel_path"],
                "normalized_rel_path": b["rel_path"],
                "skew": b["skew"],
                "width": bw,
                "height": bh,
                "dpi": b["dpi"],
                "template_id": b.get("template_id"),
                "match_score": b.get("match_score"),
                "page_mode": b.get("page_mode"),
                "slices": slices,
            }
        )

    # ⑤ 登记页 + 切片（job 转 5 等待OCR；无切片则直接 2 成功）
    beat("slice")
    reg = file_page_service.register_pipeline_output(code, inst, pages_payload)
    log.info(
        "[%s] 登记产物：%d 页 / %d 块，待提取 %d 页",
        code,
        reg["page_count"],
        reg["slice_count"],
        reg["pending_pages"],
    )

    # ⑥ 逐页提取：claim page → 裁块 → OCR/QR → complete 双写
    #    字段表按模板缓存（多模板选版下每页的 template_id 可能不同）
    extract_inst = f"{inst}-ext"
    field_maps: dict[int, dict] = {
        tid: {f.field_key: f for f in fl} for tid, fl in fields_cache.items()
    }
    from PIL import Image

    img_cache: dict[str, Image.Image] = {}
    done = 0
    while True:
        page = file_page_service.claim_page(
            ClaimRequest(instance_id=extract_inst, lease_minutes=LEASE_MINUTES)
        )
        if page is None:
            break
        try:
            rel = page.get("orig_rel_path") or ""
            page_img = img_cache.get(rel)
            if page_img is None:
                page_img = Image.open(BytesIO(_read_file(rel, shared_dir)))
                page_img.load()
                img_cache[rel] = page_img
                if len(img_cache) > 8:  # 大图不吃内存：只留最近 8 张
                    img_cache.pop(next(iter(img_cache)))

            # 本页字段表：按该页实际选中的模板取（多模板选版下每页可能不同）
            field_by_key = field_maps.get(page.get("template_id"), {})
            data_map: dict[str, dict] = {}
            # 切片图相对路径：slice_index → rel_path，随 complete 一并回写 FileSlice 行
            # （按 slice_index 匹配：整页回退块 field_key 为 NULL，用 field_key 匹配会漏）
            slice_paths: dict[int, str] = {}
            for sl in page.get("slices") or []:
                field = field_by_key.get(sl["field_key"]) if sl["field_key"] else None
                is_qr = field is not None and field.field_type == "qr"
                key = sl["field_key"] or f"__page{page['page_index']}__"
                pad = field.pad if field is not None else 0
                crop, _box = _crop(page_img, sl["x"], sl["y"], sl["width"], sl["height"], pad)
                if crop is None:
                    data_map[key] = {
                        "value": None,
                        "source": "QR" if is_qr else "OCR",
                        "confidence": None,
                    }
                    continue
                # 切片落盘：页图同名子目录下 field_key.png（与 OCR 同一份裁切，图上所见即识别所用）
                saved = _save_slice(
                    page_img,
                    page_rel_path=rel,
                    field_key=sl["field_key"],
                    slice_index=int(sl["slice_index"]),
                    x=sl["x"], y=sl["y"], w=sl["width"], h=sl["height"],
                    pad=pad, shared_dir=shared_dir,
                )
                if saved:
                    slice_paths[int(sl["slice_index"])] = saved
                try:
                    if is_qr:
                        hits = decode_bytes(crop)
                        text = hits[0]["text"] if hits else None
                        data_map[key] = {
                            "value": text,
                            "source": "QR",
                            "confidence": 1.0 if text else None,
                        }
                    else:
                        _channel, lines = ocr_bytes(crop, settings.ocr_default_tier, "auto")
                        text = "\n".join(ln["text"] for ln in lines).strip() or None
                        scores = [ln["score"] for ln in lines if ln.get("score") is not None]
                        avg = sum(scores) / len(scores) if scores else None
                        data_map[key] = {
                            "value": text,
                            "source": "OCR",
                            "confidence": round(avg, 3) if avg is not None else None,
                        }
                except Exception as exc:  # noqa: BLE001 - 单块失败不拖垮整页
                    log.warning("[%s] 页#%s 块 %s 提取失败：%s", code, page["page_index"], key, exc)
                    data_map[key] = {
                        "value": None,
                        "source": "QR" if is_qr else "OCR",
                        "confidence": None,
                    }

            # 路由层的 data 校验要求只含 value/source/confidence
            payload = {
                k: {"value": v["value"], "source": v["source"], "confidence": v["confidence"]}
                for k, v in data_map.items()
            }
            file_page_service.complete_page(
                page["id"],
                instance_id=extract_inst,
                result={"data": payload},
                slice_paths=slice_paths,
            )
            done += 1
            if page["page_index"] % 20 == 0 or page["page_index"] == len(pages_payload) - 1:
                log.info("[%s] 提取进度 %d/%d 页", code, done, len(pages_payload))
        except Exception as exc:  # noqa: BLE001 - 页级失败回队/置失败
            log.error("[%s] 页#%s 处理异常：%s", code, page["page_index"], exc)
            file_page_service.fail_page(page["id"], instance_id=extract_inst, error_msg=str(exc)[:500])

    log.info("[%s] 流水线结束：%d/%d 页提取完成", code, done, len(pages_payload))


def run_once() -> int:
    """抢一个任务跑完。0=成功 1=失败 2=队列为空。"""
    inst = f"worker-{int(time.time())}"
    job = file_job_service.claim_job(ClaimRequest(instance_id=inst, lease_minutes=LEASE_MINUTES))
    if job is None:
        return 2
    log.info("已抢占 %s（%s）", job.code, job.file_name)
    try:
        process_job(job)
    except Exception as exc:  # noqa: BLE001
        log.exception("任务 %s 处理失败", job.code)
        try:
            file_job_service.fail(
                job.code,
                FailRequest(instance_id=inst, error_msg=f"{type(exc).__name__}: {exc}"[:500]),
            )
        except Exception as inner:  # noqa: BLE001
            log.error("标记失败也失败：%s", inner)
        return 1
    return 0


def run_forever(stop_event: "threading.Event | None" = None) -> None:
    """常驻消费循环，直到 Ctrl+C 或 stop_event 被置位。

    stop_event 由调用方（后端 lifespan）传入，用于进程退出时优雅停止：
    置位后不再抢新任务，等当前任务跑完即返回。
    """
    log.info("worker 启动（空闲轮询 %.0fs，Ctrl+C 退出）", IDLE_SLEEP_SECONDS)
    processed = 0
    try:
        while not (stop_event is not None and stop_event.is_set()):
            rc = run_once()
            if rc == 2:
                _interruptible_sleep(IDLE_SLEEP_SECONDS, stop_event)
                continue
            processed += 1
    except KeyboardInterrupt:
        log.info("收到中断，退出")
    log.info("worker 停止（本次共处理 %d 个任务）", processed)


def _interruptible_sleep(seconds: float, stop_event: "threading.Event | None") -> None:
    """可被 stop_event 提前打断的 sleep（避免退出时干等一个轮询周期）。"""
    if stop_event is None:
        time.sleep(seconds)
        return
    stop_event.wait(seconds)


def start_background(stop_event: "threading.Event | None" = None) -> threading.Thread:
    """在后端进程内启动常驻 worker 线程（daemon，随主进程退出）。

    ⚠ 必须容错：数据库未配置、路径表缺失、OCR 模型未就绪等情况下，
    后台线程只能告警重试，**绝不能把 FastAPI 主进程带崩**。
    同类异常做**日志去重 + 指数退避**，避免数据库未配置时刷屏。

    ⚠ 防重：同一进程内幂等（重复调用返回既有线程），避免 uvicorn --reload
    或多 worker 场景下同进程起两个消费线程、互相抢任务。
    """
    global _bg_started, _bg_thread
    with _bg_lock:
        if _bg_started and _bg_thread is not None:
            log.debug("后台 worker 已在运行，跳过重复启动")
            return _bg_thread
        if stop_event is None:
            stop_event = threading.Event()

        base_sleep = IDLE_SLEEP_SECONDS
        max_sleep = max(base_sleep, 60.0)  # 异常退避上限 60s
        state = {"err": None, "count": 0, "logged": 0}

        def _loop() -> None:
            sleep_for = base_sleep
            while not stop_event.is_set():
                try:
                    run_once()
                    # 正常一轮（空队列或处理成功）→ 退避复位
                    sleep_for = base_sleep
                    state["err"] = None
                    state["count"] = 0
                except Exception as exc:  # noqa: BLE001 - 后台线程绝不外抛
                    kind = type(exc).__name__
                    if kind == state["err"]:
                        state["count"] += 1
                    else:
                        state["err"] = kind
                        state["count"] = 1
                        state["logged"] = 0
                    # 首次 + 每 20 次告警一次，避免刷屏
                    if state["logged"] == 0 or state["count"] % 20 == 0:
                        log.warning(
                            "后台 worker 轮询异常（%s，已连续 %d 次），%.0fs 后重试：%s",
                            kind, state["count"], sleep_for, exc,
                        )
                        state["logged"] += 1
                    sleep_for = min(max_sleep, sleep_for * 2)
                _interruptible_sleep(sleep_for, stop_event)
            log.info("后台 worker 线程退出")

        _bg_thread = threading.Thread(target=_loop, name="pipeline-worker", daemon=True)
        _bg_thread.start()
        _bg_started = True
        return _bg_thread


def show_status() -> None:
    page = file_job_service.list_jobs(page=1, page_size=20)
    names = {0: "待处理", 1: "处理中", 2: "成功", 3: "失败", 4: "已取消", 5: "等待OCR"}
    print(f"共 {page.total} 个任务")
    for j in page.items:
        print(
            f"  {j.code}  {names.get(j.state, j.state):<6} step={j.step or '-':<9} "
            f"pages={j.page_count if j.page_count is not None else '-':<4} "
            f"retry={j.retry_count}  {j.file_name}"
            + (f"  ⚠ {j.error_msg[:60]}" if j.error_msg else "")
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="文件解析流水线 worker")
    parser.add_argument("command", choices=["run", "once", "status"])
    args = parser.parse_args()

    if args.command == "status":
        show_status()
        return 0
    if args.command == "once":
        return run_once()
    run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
