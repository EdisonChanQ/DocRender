"""页产物渲染：渲染上传文件，落盘页图，并做模板配准（ECC）。

## 产物结构（按任务聚拢，一个任务 = 一个目录）

    jobs/{分类code}/{yyyyMM}/{yyyyMMdd}/{job_code}/
        ├── source/   上传原件（注册时落盘）
        ├── pages/    {job_code}.p{源页序}c{块序}.png  —— 配准通过（auto/low）
        └── reject/   同上命名                          —— 相关度 <0.45，隔离待审

目录路径由调用方用 file_job_service.job_dir 算好传入（task_dir 参数），
本模块不再自行拼分类/日期，避免与注册端拼出不同目录。

## 模板配准：只映射坐标，不动图

字段坐标存在**范本图空间**（ref_image 实际像素，如 1253×1764），页图在别的坐标系
（300dpi 渲染如 2482×3510），且两者之间存在微小旋转/缩放/平移差异。因此每页都要：
用 ECC 求出「范本坐标 → 页图坐标」的仿射矩阵 W，再把字段四角过一遍 W，
在**原分辨率页图**上裁切。图片本身不改（warp 整图会丢像素细节，已排除）。

## 两种模板类型（按声明尺寸自动判别，见 template_match.detect_page_mode）

- `full_page` 整页模板：模板声明尺寸 ≈ 页图尺寸（偏差 <2%）。
  → **不切块**，整页一个块，直接 align_page 求 W。
  这就是「一页一张完整表单」（如 HSBC General Payment Form）。
  ⚠ 此场景绝不能走 matchTemplate 定位（滑窗无位移空间，得分退化不可靠）。

- `block` 局部块模板：模板只描述单张支票块，一页 A4 排 N 张。
  → 先用 find_template_boxes 粗定位 N 个块，**再对每块做 align_block 精配准**。
  这就是「一页多支票」（如恒生绿色三连支票）。

两种情况下 blocks 里的 `align` 结构一致：matrix / scale / offset / page_mode / score，
调用方（worker）统一用 template_match.map_box 投影字段坐标即可，无需分支。

## 无模板 / 配准失败

整页作单块（仅全文提取，坐标不可用），match_score = None（band=unknown，落 pages/）。
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from app.core.errors import ParserError
from app.services import file_job_service
from app.services import template_match as tm
from app.services.template_match import to_gray

DEFAULT_DPI = 300
MAX_PAGES = 500


class PageRenderError(ParserError):
    pass


def render_and_save(
    *,
    data: bytes,
    filename: str,
    task_dir: str,
    job_code: str,
    shared_dir: str,
    dpi: int = DEFAULT_DPI,
    template=None,
    templates: list | None = None,
    killer: bool = True,
) -> dict:
    """渲染并落盘，同时完成**多模板选版 + 配准**。

    task_dir：任务目录相对路径（由 file_job_service.job_dir 算好传入），
      页产物落 `{task_dir}/pages`，配准失败（reject）落 `{task_dir}/reject`。

    template  / templates：
      - 传 `templates`（列表）时启用**多模板自动选版**：每页对每个候选模板做 ECC，
        取相关度最高的那个作为该页模板。这是分类下存在多个启用模板时的正确路径。
      - 传 `template`（单个）时视为唯一候选（等价于长度为 1 的 templates）。
      - 都没有 → 无模板整页文本提取。

    返回：
      blocks: [{
          page_index, source_page, rel_path, x, y, width, height, dpi, skew,
          match_score,      # 该页 ECC 相关度 0~1；无模板/失败为 None
          match_band,       # auto / low / reject / unknown
          page_mode,        # full_page / block / None
          template_id, template_code,   # 该页选中的模板
          candidates,       # 各候选模板的 (id, code, score)，便于排查选版歧义
          align: {matrix, scale, offset},   # 供 worker 投影字段坐标
      }]
      page_count: 源文件页数
      matched: 成功配准的块数
      rejected: 相关度 <SCORE_REJECT 被隔离到 reject/ 的块数
      dropped: 粗定位阶段被丢弃/救援失败的候选明细（含分数与原因，供日志排查）
      template_id / template_code: 若全部页选中同一模板则为该模板，否则 None
    """
    suffix = Path(filename or "").suffix.lower()
    pages_dir = f"{task_dir}/pages"
    reject_dir = f"{task_dir}/reject"
    abs_dir = Path(file_job_service.resolve_abs_path(pages_dir, shared_dir))
    abs_reject = Path(file_job_service.resolve_abs_path(reject_dir, shared_dir))
    try:
        abs_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PageRenderError(f"页产物目录创建失败：{exc}") from exc

    if suffix == ".pdf":
        source_pages = _render_pdf_pages(data, dpi)
    else:
        source_pages = _render_image_page(data)

    cands = _build_candidates(template, templates)
    if not cands:
        cands = [{"id": None, "code": None, "ref_img": None, "declared": None}]

    blocks: list[dict] = []
    seq = 0
    matched_total = 0
    rejected_total = 0
    dropped: list[dict] = []

    for src_idx, (img, scan_dpi) in enumerate(source_pages):
        page_blocks, page_dropped = _locate_blocks(img, cands)
        dropped.extend({"page": src_idx, **d} for d in page_dropped)
        for box, align in page_blocks:
            bx, by, bw, bh = box
            if bx == 0 and by == 0 and bw == img.width and bh == img.height:
                crop = img
            else:
                crop = img.crop((bx, by, bx + bw, by + bh))

            score = align.get("score")
            band = align.get("band", "unknown")
            # reject（相关度 <0.45）的页不套用字段坐标，产物隔离到 reject/ 待人工审计。
            # 仍返回带坐标的块，由调用方按 band 忽略字段（避免上游拿到脏坐标）。
            is_reject = band == "reject"
            name = f"{job_code}.p{src_idx}c{seq}.png"
            if is_reject:
                try:
                    abs_reject.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    raise PageRenderError(f"待审目录创建失败：{exc}") from exc
                _save_png(crop, abs_reject, name)
                rel_dir = reject_dir
                rejected_total += 1
            else:
                _save_png(crop, abs_dir, name)
                rel_dir = pages_dir
                if score is not None and band in ("auto", "low"):
                    matched_total += 1

            blocks.append(
                {
                    "page_index": seq,
                    "source_page": src_idx,
                    "rel_path": f"{rel_dir}/{name}",
                    "x": bx,
                    "y": by,
                    "width": bw,
                    "height": bh,
                    "dpi": scan_dpi,
                    "skew": _skew_from_matrix(align.get("matrix")),
                    "match_score": round(score, 4) if score is not None else None,
                    "match_band": band,
                    "page_mode": align.get("page_mode"),
                    "template_id": align.get("template_id"),
                    "template_code": align.get("template_code"),
                    "candidates": align.get("candidates") or [],
                    "rescued": bool(align.get("rescued")),
                    "locate_note": align.get("locate_note"),
                    "align": {
                        "matrix": align.get("matrix"),
                        "scale": align.get("scale", 1.0),
                        "offset": align.get("offset", (0.0, 0.0)),
                    },
                }
            )
            seq += 1

    used = {b["template_id"] for b in blocks}
    only = used.pop() if len(used) == 1 else None
    return {
        "blocks": blocks,
        "page_count": len(source_pages),
        "matched": matched_total,
        "rejected": rejected_total,
        "dropped": dropped,  # 粗定位丢弃/救援失败明细（worker 落日志，杜绝"凭空消失"）
        "template_id": only,
        "template_code": next(
            (b["template_code"] for b in blocks if b["template_id"] == only), None
        )
        if only is not None
        else None,
    }


def _build_candidates(template, templates: list | None) -> list[dict]:
    """候选模板 → [{'id','code','ref_img','declared'}]（ref_img 为 PIL Image）。"""
    src = []
    if templates:
        src = list(templates)
    elif template is not None:
        src = [template]
    out = []
    for t in src:
        if t is None:
            continue
        ref = _load_ref_image(t)
        declared = (
            (t.width, t.height)
            if getattr(t, "width", None) and getattr(t, "height", None)
            else None
        )
        out.append({"id": t.id, "code": t.code, "ref_img": ref, "declared": declared})
    return out


def _locate_blocks(img, cands: list[dict]) -> tuple[list[tuple[tuple[int, int, int, int], dict]], list[dict]]:
    """定位本页的块，并给出每块的配准信息（含多模板选版）。

    - 无范本图 → 整页单块、无配准
    - 多候选 → 每候选整页 ECC，相关度最高者胜出（记录全部候选分供排查）
    - 整页模板 → 不切块；局部块模板 → 粗定位 N 块 + 各块 local ECC 精配准
    - 粗定位"近失"峰（matchTemplate 分未过阈值但 ≥ floor）→ ECC 复核救援：
      matchTemplate 对底色/防伪纹理敏感（白底范本 vs 绿底划线付票 0.298 < 阈值），
      ECC 稳健（同票 0.707）。救援成功照常出块（标 rescued）；失败/丢弃全部记入
      dropped 返回给调用方落日志 —— 杜绝"票在页上但产物凭空消失且无提示"。
    返回 (blocks, dropped)。
    """
    whole = (0, 0, img.width, img.height)
    with_ref = [c for c in cands if c["ref_img"] is not None]
    if not with_ref:
        return (
            [
                (
                    whole,
                    {
                        "score": None, "band": "unknown", "matrix": None,
                        "scale": 1.0, "offset": (0.0, 0.0), "page_mode": None,
                        "template_id": None, "template_code": None, "candidates": [],
                    },
                )
            ],
            [],
        )

    page_gray = to_gray(img)

    # —— 多模板选版：每个候选跑一次整页 ECC，取最高分 ——
    scored: list[tuple[float, dict, dict]] = []
    for c in with_ref:
        base = tm.align_page(page_gray, to_gray(c["ref_img"]), declared_size=c["declared"])
        scored.append((base["score"], c, base))
    scored.sort(key=lambda t: t[0], reverse=True)
    best_score, best_c, best = scored[0]
    best["offset"] = (0.0, 0.0)
    best["template_id"] = best_c["id"]
    best["template_code"] = best_c["code"]
    best["candidates"] = [
        {"template_id": c["id"], "template_code": c["code"], "score": round(s, 4)}
        for s, c, _b in scored
    ]

    if best["page_mode"] == tm.PAGE_MODE_FULL:
        # 整页模板：不切块，整页即单块
        return [(whole, best)], []

    # 局部块模板：粗定位 + 每块局部精配准 + 近失峰 ECC 救援
    ref_gray = to_gray(best_c["ref_img"])
    boxes, near, th = tm.find_template_boxes(page_gray, ref_gray)
    dropped: list[dict] = []

    def _mk_align(box):
        align = tm.align_block(page_gray, ref_gray, box)
        align["page_mode"] = tm.PAGE_MODE_BLOCK
        align["template_id"] = best_c["id"]
        align["template_code"] = best_c["code"]
        align["candidates"] = best["candidates"]
        # 产物 = img.crop(box)：块图坐标系原点已归零到块左上角，
        # 字段坐标**不得**再叠加「块在整页上的偏移」（align_block 返回的 offset），
        # 否则双重计算 → 字段下移一个块高、越界被丢弃、退化成整页块。
        align["offset"] = (0.0, 0.0)
        return align

    out: list[tuple[tuple[int, int, int, int], dict]] = []
    for box in boxes:
        out.append((box, _mk_align(box)))

    for coarse, box in near:
        align = _mk_align(box)
        if align["band"] in ("auto", "low"):
            align["rescued"] = True
            align["coarse_score"] = round(coarse, 4)
            out.append((box, align))
        else:
            dropped.append(
                {
                    "box": box, "coarse": round(coarse, 4), "threshold": round(th, 4),
                    "ecc": round(align["score"], 4), "band": align["band"],
                    "reason": "近失峰 ECC 复核仍 reject",
                }
            )

    if not out:
        note = (
            f"粗定位 0 框（阈值 {th:.3f}）"
            + (f"，{len(near)} 个近失峰救援失败" if dropped else "，无近失峰")
            + " → 回落整页"
        )
        best["locate_note"] = note
        dropped.append({"reason": note, "threshold": round(th, 4), "boxes": 0})
        return [(whole, best)], dropped

    out.sort(key=lambda t: t[0][1])
    return out, dropped


def _skew_from_matrix(matrix) -> float:
    """从 ECC 仿射矩阵反推旋转角（度）。比投影轮廓法更准且零成本。"""
    if matrix is None:
        return 0.0
    try:
        m = matrix.tolist() if hasattr(matrix, "tolist") else matrix
        a, b = float(m[0][0]), float(m[1][0])
        import math

        deg = math.degrees(math.atan2(b, a))
        return 0.0 if abs(deg) < 0.02 else round(deg, 3)
    except Exception:  # noqa: BLE001
        return 0.0


def _save_png(image, abs_dir: Path, name: str) -> None:
    try:
        image.convert("RGB").save(abs_dir / name, format="PNG", optimize=True)
    except OSError as exc:
        raise PageRenderError(f"页图落盘失败：{exc}") from exc


def _load_ref_image(template):
    """模板范本图（配准底片）；无范本返回 None。"""
    if template is None or not getattr(template, "ref_image_b64", None):
        return None
    import base64

    from PIL import Image

    try:
        raw = base64.b64decode(template.ref_image_b64.split(",", 1)[-1])
        img = Image.open(BytesIO(raw))
        img.load()
        return img
    except Exception as exc:  # noqa: BLE001
        raise PageRenderError(f"模板范本图无法解析：{exc}") from exc


def _render_image_page(data: bytes) -> list[tuple[object, int]]:
    from PIL import Image

    try:
        img = Image.open(BytesIO(data))
        img.load()
    except Exception as exc:  # noqa: BLE001
        raise PageRenderError(f"无法读取图片：{exc}") from exc

    scan_dpi = DEFAULT_DPI
    meta = img.info.get("dpi")
    if meta and meta[0] > 20:
        scan_dpi = int(round(meta[0]))
    return [(img, scan_dpi)]


def _render_pdf_pages(data: bytes, dpi: int) -> list[tuple[object, int]]:
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:  # pragma: no cover
        raise PageRenderError("后端未安装 PyMuPDF，无法处理 PDF") from exc

    from PIL import Image

    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise PageRenderError(f"无法打开 PDF：{exc}") from exc
    try:
        if doc.page_count > MAX_PAGES:
            raise PageRenderError(f"页数 {doc.page_count} 超过上限 {MAX_PAGES}")
        zoom = dpi / 72.0
        out = []
        for page in doc:
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            out.append((Image.frombytes("RGB", (pix.width, pix.height), pix.samples), dpi))
        return out
    finally:
        doc.close()


def detect_skew_deg(image) -> float:
    """投影轮廓法检测修正角（度），失败或可忽略返回 0.0。

    保留作为无模板时的兜底；有模板时优先用 ECC 矩阵反推（_skew_from_matrix）。
    """
    try:
        import cv2
        import numpy as np

        arr = np.array(image.convert("L"))
        h, w = arr.shape
        if h < 40 or w < 40:
            return 0.0
        binary = (
            cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1].astype(np.float32)
            / 255.0
        )

        def score(deg: float) -> float:
            m = cv2.getRotationMatrix2D((w / 2, h / 2), deg, 1.0)
            rot = cv2.warpAffine(binary, m, (w, h), flags=cv2.INTER_LINEAR, borderValue=0)
            return float(np.var(rot.sum(axis=1)))

        best, best_score = 0.0, score(0.0)
        for deg in range(-100, 101, 10):
            d = deg / 10
            s = score(d)
            if s > best_score:
                best_score, best = s, d
        for deg10 in range(int((best - 1) * 10), int((best + 1) * 10) + 1):
            d = deg10 / 10
            s = score(d)
            if s > best_score:
                best_score, best = s, d
        return 0.0 if abs(best) < 0.15 else round(best, 2)
    except Exception:  # noqa: BLE001 - 检测失败不阻塞主流程
        return 0.0
