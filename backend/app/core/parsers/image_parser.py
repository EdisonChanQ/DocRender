from pathlib import Path

from PIL import Image

from app.core.errors import OcrUnavailableError
from app.core.ocr import get_ocr_engine
from app.core.parsers.base import BaseParser
from app.schemas.parse import ExtractMethod, PageResult, ParseResult


class ImageParser(BaseParser):
    extensions = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp", ".gif")
    media_type = "image/*"

    def parse(self, path: Path, filename: str) -> ParseResult:
        engine = get_ocr_engine()
        if not engine.available:
            raise OcrUnavailableError("Image parsing requires OCR.")

        with Image.open(path) as image:
            text = engine.image_to_text(image.convert("RGB"))

        return ParseResult(
            filename=filename,
            extension=path.suffix.lower(),
            media_type=self.media_type,
            method=ExtractMethod.OCR,
            page_count=1,
            char_count=len(text),
            text=text,
            pages=[PageResult(index=0, text=text, method=ExtractMethod.OCR)],
        )
