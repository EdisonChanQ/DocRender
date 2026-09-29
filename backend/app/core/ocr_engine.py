"""OCR-to-Text 推理引擎（PP-OCRv6 ONNX / rapidocr-onnxruntime）。

两种识别通道（对应用户 models/ 下两套 yaml 设计）：
- detect：det+rec 全套，整页/未裁剪图，先找文字位置再识别；
- crop：rec-only 快速通道，切块/单字段小图直接识别（实测 ~17ms/张，
  对窄长条走 det 会被 limit_type=min 放大到病态 7s+/张）。
- auto：按图片几何特征自动选择（短边小或宽高比极端视为切块）。

引擎按 (tier, channel) 懒加载为进程内单例；onnxruntime session 线程安全，
多线程并发请求直接共享实例，无需加锁。
模型路径来自 settings.ocr_model_dir（默认 backend/models）。
"""

from __future__ import annotations

import io
import threading
from pathlib import Path

from app.config import BASE_DIR, settings
from app.core.errors import ParserError

SUPPORTED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp"}

# 相对 ocr_model_dir（默认 backend/models）的路径
TIERS = {
    "small": {
        "det": "onnx/PP-OCRv6/det/PP-OCRv6_det_small.onnx",
        "rec": "onnx/PP-OCRv6/rec/PP-OCRv6_rec_small.onnx",
        "keys": "paddle/PP-OCRv6/rec/PP-OCRv6_rec_small/ppocrv6_dict.txt",
    },
    "medium": {
        "det": "onnx/PP-OCRv6/det/PP-OCRv6_det_medium.onnx",
        "rec": "onnx/PP-OCRv6/rec/PP-OCRv6_rec_medium.onnx",
        "keys": "paddle/PP-OCRv6/rec/PP-OCRv6_rec_medium/ppocrv6_dict.txt",
    },
}

DEFAULT_TIER = "small"
MODES = ("auto", "detect", "crop")

# auto 判定阈值：短边 <= 该值 或 宽高比 >= 该值 → 视为切块小图走 crop
AUTO_CROP_MAX_SHORT_SIDE = 128
AUTO_CROP_MIN_ASPECT = 6.0

_lock = threading.Lock()
_engines: dict[tuple[str, str], object] = {}


class OcrError(ParserError):
    """OCR 识别失败。"""


class UnsupportedImageError(ParserError):
    """不支持的图片类型。"""


def _model_dir() -> Path:
    return Path(settings.ocr_model_dir)


def _build_engine(tier: str, channel: str):
    """channel: 'detect'（det+rec）或 'crop'（rec-only）。"""
    if tier not in TIERS:
        raise OcrError(f"未知模型档位：{tier}（可选 {', '.join(TIERS)}）")
    key = (tier, channel)
    engine = _engines.get(key)
    if engine is not None:
        return engine

    with _lock:
        engine = _engines.get(key)
        if engine is not None:
            return engine
        paths = {k: _model_dir() / v for k, v in TIERS[tier].items()}
        missing = [str(p) for p in paths.values() if not p.exists()]
        if missing:
            raise OcrError("模型文件缺失：" + "；".join(missing))
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:
            raise OcrError("未安装 rapidocr-onnxruntime，请先 pip install") from exc
        if channel == "crop":
            engine = RapidOCR(
                rec_model_path=str(paths["rec"]),
                rec_keys_path=str(paths["keys"]),
                use_det=False,
                use_cls=False,
                use_rec=True,
            )
        else:
            engine = RapidOCR(
                det_model_path=str(paths["det"]),
                rec_model_path=str(paths["rec"]),
                rec_keys_path=str(paths["keys"]),
            )
        _engines[key] = engine
        return engine


def _normalize_size(data: bytes) -> bytes:
    """detect 通道尺寸保护：det 配置 limit_type=min/736 会把小图放大，
    窄长条会被放大到巨大尺寸导致耗时爆炸。保证最短边 >= 736 且总像素 <= ~160 万。
    """
    from PIL import Image

    with Image.open(io.BytesIO(data)) as img:
        w, h = img.size
        target = max(736, min(w, h, 1000))
        scale = 1.0
        if min(w, h) < target:
            scale = target / min(w, h)
        max_pixels = 1_600_000
        if scale > 1.0 and w * h * scale * scale > max_pixels:
            scale = (max_pixels / (w * h)) ** 0.5
        if scale == 1.0:
            return data
        new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
        resized = img.convert("RGB").resize(new_size, Image.LANCZOS)
        buf = io.BytesIO()
        resized.save(buf, format="PNG")
        return buf.getvalue()


def resolve_channel(data: bytes, mode: str) -> str:
    """auto 模式按几何特征选择通道；显式模式直接映射。"""
    if mode == "crop":
        return "crop"
    if mode == "detect":
        return "detect"
    from PIL import Image

    with Image.open(io.BytesIO(data)) as img:
        w, h = img.size
    short = min(w, h)
    aspect = max(w, h) / max(1, short)
    return "crop" if short <= AUTO_CROP_MAX_SHORT_SIDE or aspect >= AUTO_CROP_MIN_ASPECT else "detect"


def _merge_single_chars(text: str) -> str:
    """格子切块图里 CTC 常在字符间吐空格（'7 5 2 0…'）。
    仅当按空格切分后 >=3 个 token 且全部为单字符时才合并，
    避免误伤 'ROME FASTENER CORPORATION' 这类多字母词。
    """
    tokens = text.split()
    if len(tokens) >= 3 and all(len(t) == 1 for t in tokens):
        return "".join(tokens)
    return text


def ocr_bytes(data: bytes, tier: str = DEFAULT_TIER, mode: str = "auto") -> tuple[str, list[dict]]:
    """识别图片字节流中的文字。

    返回 (实际使用的通道, [{text, score, box}])；crop 通道无 box（整图即文本行）。
    """
    if mode not in MODES:
        raise OcrError(f"未知识别模式：{mode}（可选 {', '.join(MODES)}）")
    channel = resolve_channel(data, mode)

    if channel == "crop":
        # rec 模型内部会缩放到高 48，无需 _normalize_size（放大反而浪费）
        engine = _build_engine(tier, "crop")
        try:
            result, _elapse = engine(data)
        except Exception as exc:  # noqa: BLE001 - 底层推理异常统一包装
            raise OcrError(f"OCR 推理失败：{exc}") from exc
        lines: list[dict] = []
        for item in result or []:
            # rec-only 返回 [(text, score)]
            text, score = str(item[0]), float(item[1])
            lines.append({"text": _merge_single_chars(text), "score": round(score, 4), "box": None})
        return channel, lines

    engine = _build_engine(tier, "detect")
    data = _normalize_size(data)
    try:
        result, _elapse = engine(data)
    except Exception as exc:  # noqa: BLE001
        raise OcrError(f"OCR 推理失败：{exc}") from exc
    lines = []
    for item in result or []:
        box, text, score = item[0], item[1], float(item[2])
        lines.append(
            {
                "text": text,
                "score": round(score, 4),
                "box": [[int(round(float(p[0]))), int(round(float(p[1])))] for p in box],
            }
        )
    return channel, lines
