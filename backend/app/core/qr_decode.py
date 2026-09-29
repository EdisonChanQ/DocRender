"""二维码解码核心（QR-to-Text 工具）。

内存中解码，不落盘，适合并发压测。仅识别 QRCODE（不含一维条码），
symbols 参数限制可提升速度并避免误读条形码。
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image, UnidentifiedImageError
from pyzbar.pyzbar import decode as _pyzbar_decode, ZBarSymbol

from app.core.errors import ParserError

# 允许作为图片打开的扩展名（小写，含点）
SUPPORTED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp"}

MAX_HITS = 200  # 单图二维码数量上限，防御异常图


class QrDecodeError(ParserError):
    """二维码解码失败。"""


class UnsupportedImageError(ParserError):
    """不支持的图片类型。"""


def decode_bytes(data: bytes) -> list[dict]:
    """从图片字节流解码所有二维码，返回 [{text, symbol, rect}]。"""
    try:
        image = Image.open(BytesIO(data))
    except (UnidentifiedImageError, OSError) as exc:
        raise UnsupportedImageError(f"无法读取图片：{exc}") from exc

    # 部分格式（如 GIF 动图/透明通道）转 RGB 更稳
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")

    try:
        results = _pyzbar_decode(image, symbols=[ZBarSymbol.QRCODE])
    except Exception as exc:  # noqa: BLE001 - pyzbar 底层 C 调用异常统一包装
        raise QrDecodeError(f"解码失败：{exc}") from exc

    hits: list[dict] = []
    for r in results[:MAX_HITS]:
        try:
            text = r.data.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            text = r.data.hex()
        rect = getattr(r, "rect", None)
        hits.append(
            {
                "text": text,
                "symbol": r.type.name if hasattr(r.type, "name") else str(r.type),
                "rect": (
                    {
                        "left": rect.left,
                        "top": rect.top,
                        "width": rect.width,
                        "height": rect.height,
                    }
                    if rect is not None
                    else None
                ),
            }
        )
    return hits
