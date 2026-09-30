"""模板制作：临时解析上传文件为页预览图（纯交互，不落盘不落库）。

- 预览图按**真实尺寸**渲染并转 JPEG：PDF 按估算扫描 dpi（估不出默认 300）、
  图片按元数据 dpi（缺失按 300），preview_w/h == full_w/h == 真实像素尺寸，
  前端所见即所得（显示层缩放不影响框选坐标系）。
  （方案 A：不再用 150dpi 缩略 + 估算 dpi 换算两条链，两数恒差 2 倍且互不相干。）
- 每页同时报告预览像素尺寸（= 真实尺寸，框选坐标系）与 dpi，
  前端把框选比例换算成最终 width/height 提交（scale = full/preview = 1）。
- PDF：PyMuPDF 渲染；扫描件 dpi 从内嵌图像素/页物理英寸估算。
- 图片：单页，dpi 读元数据（缺失按 300）。
"""

from __future__ import annotations

import base64
from io import BytesIO

from app.core.errors import ParserError

# 方案 A 起预览即真实尺寸渲染，PREVIEW_DPI 已不再用于预览降采样；
# 保留常量仅为兼容引用检查，无实际作用。
PREVIEW_DPI = 150
DEFAULT_SCAN_DPI = 200  # 无法精确估算扫描 dpi 时的回退默认值（与用户转图基准 1655×2340 一致）
MAX_PAGES = 30
JPEG_QUALITY = 85

PDF_EXTS = {".pdf"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp"}


class TemplatePrepareError(ParserError):
    pass


def _encode_jpeg(image) -> str:
    buf = BytesIO()
    image.convert("RGB").save(buf, format="JPEG", quality=JPEG_QUALITY)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def detect_skew(image) -> float:
    """检测内容倾斜角（投影轮廓法）：返回**修正角**（度，正值=需逆时针旋转摆正）。

    原理：文本行在正确角度下水平投影的方差最大（行间黑白分明）。
    在 ±10° 内粗扫（步长 1°）+ 峰值附近细扫（步长 0.1°）。
    失败或倾斜可忽略（<0.15°）返回 0。
    """
    try:
        import cv2
        import numpy as np

        arr = np.array(image.convert("L"))
        h, w = arr.shape
        if h < 40 or w < 40:
            return 0.0
        # 二值化：文字=1
        binary = cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1].astype(np.float32) / 255.0

        def score(deg: float) -> float:
            m = cv2.getRotationMatrix2D((w / 2, h / 2), deg, 1.0)
            rot = cv2.warpAffine(binary, m, (w, h), flags=cv2.INTER_LINEAR, borderValue=0)
            rows = rot.sum(axis=1)
            return float(np.var(rows))

        best = 0.0
        best_score = score(0.0)
        for deg in range(-100, 101, 10):  # -10.0 ~ 10.0 步长 1.0
            d = deg / 10
            s = score(d)
            if s > best_score:
                best_score, best = s, d
        for deg10 in range(int((best - 1) * 10), int((best + 1) * 10) + 1):  # 细扫 ±1° 步长 0.1
            d = deg10 / 10
            s = score(d)
            if s > best_score:
                best_score, best = s, d
        if abs(best) < 0.15:
            return 0.0
        return round(best, 2)
    except Exception:  # noqa: BLE001 - 检测失败不阻塞主流程
        return 0.0


def prepare_pages(data: bytes, filename: str) -> dict:
    """返回 {kind, preview_dpi, page_count, pages:[{index, preview_w/h, full_w/h, dpi, skew, data_url}]}。

    preview_w/h：data_url 图片像素尺寸（框选坐标系）。
    full_w/h：**与 preview_w/h 恒等**（方案 A：预览即真实尺寸渲染，不再换算）。
    skew：建议的自动修正角（度，正值=逆时针旋转可摆正），检测失败为 0。
    前端提交时：width = round(crop_w / preview_w * full_w) = crop_w（scale=1）。
    """
    from pathlib import Path

    suffix = Path(filename or "").suffix.lower()

    if suffix in IMAGE_EXTS:
        from PIL import Image

        try:
            img = Image.open(BytesIO(data))
        except Exception as exc:  # noqa: BLE001
            raise TemplatePrepareError(f"无法读取图片：{exc}") from exc
        dpi_estimated = False
        dpi = DEFAULT_SCAN_DPI
        try:
            meta_dpi = img.info.get("dpi")
            if meta_dpi and meta_dpi[0] > 20:
                dpi = int(round(meta_dpi[0]))
                dpi_estimated = True
        except Exception:  # noqa: BLE001
            pass
        rgb = img.convert("RGB")
        # 方案 A：预览 = 原始尺寸原图（不降采样、不缩放）。
        # 用户需要看到真实像素尺寸来设计模板门框；前端显示层自带缩放，不影响框选坐标。
        return {
            "kind": "image",
            "preview_dpi": dpi,
            "page_count": 1,
            "pages": [
                {
                    "index": 0,
                    "preview_w": rgb.width,
                    "preview_h": rgb.height,
                    "full_w": rgb.width,
                    "full_h": rgb.height,
                    "dpi": dpi,
                    "dpi_estimated": dpi_estimated,
                    "skew": detect_skew(rgb),
                    "data_url": _encode_jpeg(rgb),
                }
            ],
        }

    if suffix in PDF_EXTS:
        try:
            import fitz  # PyMuPDF
        except ImportError as exc:
            raise TemplatePrepareError("后端未安装 PyMuPDF") from exc
        try:
            doc = fitz.open(stream=data, filetype="pdf")
        except Exception as exc:  # noqa: BLE001
            raise TemplatePrepareError(f"无法打开 PDF：{exc}") from exc
        try:
            if doc.page_count > MAX_PAGES:
                raise TemplatePrepareError(f"页数 {doc.page_count} 超过上限 {MAX_PAGES}")
            pages = []
            for i, page in enumerate(doc):
                # 方案 A：预览即真实尺寸 —— 按估算扫描 dpi（估不出默认 200）渲染整页，
                # preview_w/h == full_w/h == 真实像素尺寸，前端所见即所得。
                # 不再用 150dpi 缩略 + 估算 dpi 换算两条链（两数恒差 2 倍且互不相干）。
                # dpi_estimated 标记该 dpi 是检测到的真实扫描 dpi 还是回退默认值，
                # 前端据此如实提示，避免用户误把默认值当真实分辨率。
                est_dpi = _estimate_pdf_dpi(page)
                dpi = est_dpi or DEFAULT_SCAN_DPI
                zoom = dpi / 72.0
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                from PIL import Image

                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                pages.append(
                    {
                        "index": i,
                        "preview_w": pix.width,
                        "preview_h": pix.height,
                        "full_w": pix.width,
                        "full_h": pix.height,
                        "dpi": dpi,
                        "dpi_estimated": est_dpi is not None,
                        "skew": detect_skew(img),
                        "data_url": _encode_jpeg(img),
                    }
                )
            return {
                "kind": "pdf",
                "preview_dpi": pages[0]["dpi"] if pages else DEFAULT_SCAN_DPI,
                "page_count": len(pages),
                "pages": pages,
            }
        finally:
            doc.close()

    raise TemplatePrepareError(f"不支持的文件类型：{suffix or '未知'}（仅 PDF 与常见图片）")


def _estimate_pdf_dpi(page) -> int | None:
    """扫描页：取最大内嵌图像的像素宽 / 页物理英寸，估算真实扫描 dpi。"""
    try:
        rect = page.rect
        inches_w = rect.width / 72.0
        best_px_w = 0
        for img in page.get_images(full=True):
            xref = img[0]
            info = page.doc.extract_image(xref)
            if info and info.get("width", 0) > best_px_w:
                best_px_w = info["width"]
        if best_px_w and inches_w > 0:
            return max(72, min(1200, int(round(best_px_w / inches_w))))
    except Exception:  # noqa: BLE001 - 估算失败不影响主流程
        pass
    return None
