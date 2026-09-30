"""范本模具重渲染：把"门"规格（dpi/width/height）真正落到 ref_image 像素上。

背景（09-30 诊断定案）：旧链路里 ref_image 是 150dpi 预览图的 1:1 裁剪，
声明 width/height 是裁剪框 ×(估算dpi/150)，两数恒差 ≈2 倍且互不相干；
上传件却按 dpi 列渲染 —— 三套口径不一致导致 detect_page_mode 全页误判 block。

本模块实现用户最初设计：一次定"门"，范本与上传件同一模具出图 ——
后端拿**源文件**按 dpi 列真渲染整页 → 同角旋转纠偏（与前端 canvas 同式）→
把 150dpi 预览坐标系的裁剪框换算到渲染口径并裁出 → LANCZOS 缩放到声明尺寸
⇒ **ref 像素恒等于 declared**，page_mode 判定与字段坐标换算 k=declared/ref=1
自然正确；矢量 PDF 为无损真渲染，位图源为上采样（信息不增但口径统一）。

校验（防"三数字互相矛盾"复发）：
- 校验一：宽高比一致性 —— declared 宽高比 == 裁剪框宽高比（applyCrop 换算恒等，2%）
- 校验二：全页范本（裁剪框覆盖度 ≥98%）时，源页物理英寸 × dpi 应 ≈ 声明尺寸，
  且 dpi 列决定上传件渲染口径 —— 矛盾时直接拒绝并给出两个可改方向的正确值。

备份：入库同一份字节**原封不动**写到
    {shared}/categories/{分类code}/{模板code}/ref_image.png|jpg
扩展名按魔数判定；备份失败不回滚保存，仅在响应 backup_ok/backup_error 提示。
"""

from __future__ import annotations

import base64
import io
import math
import re
from pathlib import Path

from PIL import Image

# 与 template_match.FULL_PAGE_TOLERANCE 同口径
CONSISTENCY_TOL = 0.02
# 整页门规格自洽容差（校验三）：dpi 是取样密度非物理真值，宽高是门框主体，
# 允许用户按标准门框设定（如 1600×2300），与物理英寸×dpi 存在小幅差异仍放行
# （200dpi 整页 1655×2340 vs 门框 1600×2300，差 ~3.4%）。与 detect_page_mode
# 的 FULL_PAGE_TOLERANCE(0.10) 同口径，ECC 配准 scale 自适应缩放。
FULL_PAGE_GEOM_TOL = 0.10
# 视为"整页范本"的裁剪覆盖度阈值（宽高各 ≥ 此比例 → 全页几何校验生效）
FULL_COVER_RATIO = 0.98

PDF_EXTS = {".pdf"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp"}
_ILLEGAL_DIR_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class RefRenderError(Exception):
    """范本重渲染/校验失败（路由层映射为 400）。"""


def _rotated_size(w: float, h: float, deg: float) -> tuple[int, int]:
    """与前端 canvas 完全同式的扩展尺寸：nw = w·|cosθ|+h·|sinθ|（<0.05° 不旋转）。"""
    if abs(deg) < 0.05:
        return int(round(w)), int(round(h))
    rad = math.radians(deg)
    cos, sin = abs(math.cos(rad)), abs(math.sin(rad))
    return int(round(w * cos + h * sin)), int(round(w * sin + h * cos))


def _rotate(img: Image.Image, deg: float) -> Image.Image:
    """顺时针旋转 deg 度（PIL rotate 正值=逆时针，取反），白底扩展 —— 与前端一致。"""
    if abs(deg) < 0.05:
        return img
    rot = img.rotate(-deg, expand=True, fillcolor=(255, 255, 255), resample=Image.BICUBIC)
    bg = Image.new("RGB", rot.size, (255, 255, 255))
    bg.paste(rot, (0, 0))
    return bg


def _load_source_page(data: bytes, suffix: str, page_index: int, dpi: int) -> tuple[Image.Image, float, float, float]:
    """按 dpi 渲染源页 → (RGB 图, 页物理宽英寸, 页物理高英寸, 预览基准dpi)。

    预览基准dpi = prepare_pages 生成预览所用的口径（方案 A 起二者同源）：
    - PDF：_estimate_pdf_dpi 估算的扫描 dpi（估不出默认 200，与 prepare 同常量）——prepare 按它渲染真实尺寸预览；
    - 图片：源图元数据 dpi（缺失默认 200，与 prepare 同常量）——prepare 不再降采样，预览即原图。
    校验二用它对账 preview 坐标系。
    """
    if suffix in PDF_EXTS:
        try:
            import fitz
        except ImportError as exc:
            raise RefRenderError("后端未安装 PyMuPDF，无法重渲染 PDF 范本") from exc
        try:
            doc = fitz.open(stream=data, filetype="pdf")
        except Exception as exc:  # noqa: BLE001
            raise RefRenderError(f"无法打开 PDF 源文件：{exc}") from exc
        try:
            if page_index >= doc.page_count:
                raise RefRenderError(f"源文件只有 {doc.page_count} 页，取不到第 {page_index + 1} 页")
            page = doc[page_index]
            pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0), alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            # 与 template_prepare 同口径：预览基准 dpi = 估算扫描 dpi（估不出默认 200）
            from app.services import template_prepare

            preview_base = template_prepare._estimate_pdf_dpi(page) or template_prepare.DEFAULT_SCAN_DPI
            return img, page.rect.width / 72.0, page.rect.height / 72.0, float(preview_base)
        finally:
            doc.close()
    if suffix in IMAGE_EXTS:
        try:
            img = Image.open(io.BytesIO(data))
            img.load()
        except Exception as exc:  # noqa: BLE001
            raise RefRenderError(f"无法读取图片源文件：{exc}") from exc
        img = img.convert("RGB")
        # 与 template_prepare 同口径：源图元数据 dpi（缺失默认 200）
        from app.services import template_prepare

        src_dpi = template_prepare.DEFAULT_SCAN_DPI
        try:
            meta = img.info.get("dpi")
            if meta and meta[0] > 20:
                src_dpi = int(round(meta[0]))
        except Exception:  # noqa: BLE001
            pass
        # 与 template_prepare 同口径：预览基准 dpi = 源图 dpi（prepare 不再降采样）
        return img, img.width / src_dpi, img.height / src_dpi, float(src_dpi)
    raise RefRenderError(f"不支持的源文件类型：{suffix or '未知'}（仅 PDF 与常见图片）")


def decode_ref_bytes(ref_b64: str | None, max_bytes: int) -> bytes:
    """base64（可带 data: 前缀）→ bytes + 大小校验。"""
    payload = (ref_b64 or "").split(",", 1)[-1].strip()
    if not payload:
        raise RefRenderError("缺少范本图")
    try:
        raw = base64.b64decode(payload, validate=False)
    except Exception as exc:  # noqa: BLE001
        raise RefRenderError(f"范本图 base64 无效：{exc}") from exc
    if not raw:
        raise RefRenderError("范本图为空")
    if len(raw) > max_bytes:
        raise RefRenderError(f"范本图超过 {max_bytes // (1024 * 1024)}MB 限制")
    return raw


def render_ref(
    *,
    source: bytes | None,
    source_filename: str,
    ref_raw: bytes | None,
    page_index: int,
    rotation_deg: float,
    crop: tuple[float, float, float, float],
    preview_size: tuple[int, int],
    dpi: int,
    width: int,
    height: int,
) -> bytes:
    """模具重渲染主入口：返回 PNG 字节，尺寸恒等于 (width, height)。

    crop / preview_size 均为前端 150dpi 预览坐标系（**旋转后**）的值，与 applyCrop 同源。
    优先用 source 真重渲染；source 缺失但给了 ref_raw（二次框选既有范本、无源文件）
    → 退化为把既有范本 LANCZOS 缩放到声明尺寸（口径立即统一，信息量不变）。
    两者都没有 → 报错（前端必须重新上传源文件）。
    """
    if dpi < 1 or width < 1 or height < 1:
        raise RefRenderError("dpi / 宽 / 高 必须为正整数")
    cx, cy, cw, ch = crop
    pw0, ph0 = preview_size
    if cw < 8 or ch < 8 or pw0 < 8 or ph0 < 8:
        raise RefRenderError("裁剪框或预览尺寸无效（<8px）")
    eps = max(2.0, pw0 * 0.01)
    if cx < -eps or cy < -eps or cx + cw > pw0 + eps or cy + ch > ph0 + eps:
        raise RefRenderError(
            f"裁剪框越界：crop=({cx:.0f},{cy:.0f},{cw:.0f},{ch:.0f}) 预览=({pw0},{ph0})，请重新框选"
        )

    # —— 校验一：宽高比一致（applyCrop 的 width/height 由裁剪框等比换算而来）——
    if abs(width / height - cw / ch) / max(width / height, cw / ch) > CONSISTENCY_TOL:
        raise RefRenderError(
            f"声明尺寸与裁剪框宽高比不一致：{width}×{height}（{width / height:.3f}）"
            f" vs 裁剪框 {cw:.0f}×{ch:.0f}（{cw / ch:.3f}）。"
            f"请修正宽/高，或按当前尺寸重算：建议 {width}×{round(width * ch / cw)}"
        )

    suffix = Path(source_filename or "").suffix.lower()

    if source is None:
        # —— 无源文件：把既有范本缩放到声明尺寸（二次框选路径的退化形式）——
        if ref_raw is None:
            raise RefRenderError("缺少源文件与既有范本，无法按门规格重渲染；请重新上传源文件后再保存")
        try:
            img = Image.open(io.BytesIO(ref_raw))
            img.load()
        except Exception as exc:  # noqa: BLE001
            raise RefRenderError(f"既有范本无法解析：{exc}") from exc
        img = img.convert("RGB")
        # 二次框选时裁剪框在「范本自然尺寸」坐标空间：先裁再缩放
        sx, sy = img.width / pw0, img.height / ph0
        box = (
            max(0, int(round(cx * sx))),
            max(0, int(round(cy * sy))),
            min(img.width, int(round((cx + cw) * sx))),
            min(img.height, int(round((cy + ch) * sy))),
        )
        if box[2] - box[0] < 8 or box[3] - box[1] < 8:
            raise RefRenderError("裁剪区域过小（<8px），无法生成有效范本")
        out = img.crop(box).resize((width, height), Image.LANCZOS)
        return _to_png(out)

    # —— ① 按 dpi 列真渲染整页 ——
    page_img, phys_w, phys_h, preview_base_dpi = _load_source_page(source, suffix, page_index, dpi)

    # —— 校验二：预览坐标系必须是 prepare_pages 口径（物理英寸 × preview_base_dpi）——
    # 且要过旋转膨胀（前端 canvas 同式）。位图源小 dpi 不放大 → preview_base_dpi<150，
    # 用 dpi 列反推会误杀，所以判据与 prepare_pages 同源而不是硬编码 150。
    exp_w, exp_h = _rotated_size(phys_w * preview_base_dpi, phys_h * preview_base_dpi, rotation_deg)
    if exp_w < 8 or exp_h < 8:
        raise RefRenderError("源页过小，无法建立预览坐标系对账基准")
    if abs(pw0 - exp_w) / exp_w > 0.05 or abs(ph0 - exp_h) / exp_h > 0.05:
        raise RefRenderError(
            f"预览坐标系与源页口径对不上：preview=({pw0},{ph0}) vs 预期 "
            f"({round(exp_w)},{round(exp_h)})。请重新上传源文件后框选保存（不要直接用旧范本改尺寸）。"
        )

    # —— ② 同角旋转纠偏（与前端 canvas 同式，白底扩展）——
    page_rotated = _rotate(page_img, rotation_deg)

    # —— ③ 预览坐标裁剪框 → 渲染口径裁剪框 → 裁出 ——
    sx = page_rotated.width / pw0
    sy = page_rotated.height / ph0
    box = (
        max(0, int(round(cx * sx))),
        max(0, int(round(cy * sy))),
        min(page_rotated.width, int(round((cx + cw) * sx))),
        min(page_rotated.height, int(round((cy + ch) * sy))),
    )
    if box[2] - box[0] < 8 or box[3] - box[1] < 8:
        raise RefRenderError("裁剪区域过小（<8px），无法生成有效范本")
    crop_img = page_rotated.crop(box)

    # —— ④ 校验三：整页范本的门几何自洽 —— declared ≈ 页物理尺寸 × dpi（容差 10%）——
    # 上传件按 dpi 列渲染，整页页图像素 ≈ 物理英寸 × dpi；detect_page_mode 拿声明尺寸
    # 与页图直接比对。dpi 是取样密度非物理真值，宽高是门框主体：用户按标准门框设定
    # （如 1600×2300）与物理×dpi（1655×2340）存在小幅差异（≤10%）时放行，ECC 配准
    # scale 自适应缩放；超 10%（如 2 倍差）才拒绝，防止三数字矛盾复发（09-30
    # JOB20260930041810CFC7 事故根因）。局部块模板不受此约束：其 declared 由用户
    # "稍微修改"以贴合未来页上块尺寸，倍率不是门规格函数，强校验会违背设计原意。
    full_cover = cw / pw0 >= FULL_COVER_RATIO and ch / ph0 >= FULL_COVER_RATIO
    if full_cover and phys_w > 0 and phys_h > 0:
        exp_w_page, exp_h_page = phys_w * dpi, phys_h * dpi
        off_w = abs(width - exp_w_page) / exp_w_page
        off_h = abs(height - exp_h_page) / exp_h_page
        if off_w > FULL_PAGE_GEOM_TOL or off_h > FULL_PAGE_GEOM_TOL:
            sug_w, sug_h = round(exp_w_page), round(exp_h_page)
            implied_dpi = width / phys_w
            raise RefRenderError(
                f"整页门规格偏差过大：源页物理 {phys_w:.2f}×{phys_h:.2f} 英寸，按 dpi={dpi} "
                f"整页为 {sug_w}×{sug_h}，而声明为 {width}×{height}（隐含 {implied_dpi:.0f}dpi），"
                f"偏差 {(off_w * 100):.1f}% 超过容差 {FULL_PAGE_GEOM_TOL * 100:.0f}%。"
                f"dpi 是取样密度非物理真值、宽高是门框主体，小幅归一化（≤10%）放行；"
                f"当前差异过大说明声明与渲染口径互相矛盾，会误判 block。"
                f"请改宽/高为 {sug_w}×{sug_h}，或改 dpi 为 {round(implied_dpi)}，"
                f"或改宽/高到与 dpi 差异 ≤{FULL_PAGE_GEOM_TOL * 100:.0f}% 的门框值。"
            )

    # —— ⑤ 模具输出：缩放到声明尺寸（恒等时零操作）——
    if crop_img.size != (width, height):
        crop_img = crop_img.resize((width, height), Image.LANCZOS)
    return _to_png(crop_img)


def _to_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def ref_ext(raw: bytes) -> str:
    """按魔数判定备份扩展名：PNG→png，JPEG→jpg，其他→bin（仍原样备份）。"""
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if raw[:2] == b"\xff\xd8":
        return "jpg"
    return "bin"


def _safe_dirname(s: str) -> str:
    cleaned = _ILLEGAL_DIR_CHARS.sub("_", str(s or "")).strip(" .")
    return cleaned or "unknown"


def backup_ref_to_shared(
    *, shared_dir: str, category_code: str, template_code: str, raw: bytes
) -> tuple[str, str | None]:
    """把入库字节**原样**写到共享目录。返回 (相对路径, 错误信息或 None)。"""
    ext = ref_ext(raw)
    fname = f"ref_image.{ext}"
    rel = f"categories/{_safe_dirname(category_code)}/{_safe_dirname(template_code)}/{fname}"
    from app.services import file_job_service

    try:
        abs_path = Path(file_job_service.resolve_abs_path(rel, shared_dir))
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(raw)
        return rel, None
    except OSError as exc:
        return rel, f"共享目录备份失败：{exc}"
