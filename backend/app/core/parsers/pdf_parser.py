from pathlib import Path

from app.config import settings
from app.core.errors import OcrUnavailableError, ParserError
from app.core.ocr import get_ocr_engine
from app.core.parsers.base import BaseParser
from app.schemas.parse import ExtractMethod, PageResult, ParseResult

MIN_TEXT_CHARS_PER_PAGE = 12


class PdfParser(BaseParser):
    extensions = (".pdf",)
    media_type = "application/pdf"

    def parse(self, path: Path, filename: str) -> ParseResult:
        try:
            import fitz  # PyMuPDF
        except ImportError as exc:  # pragma: no cover
            raise ParserError("PyMuPDF is required to parse PDF files.") from exc

        document = fitz.open(path)
        try:
            pages = self._extract_pages(fitz, document)
        finally:
            document.close()

        text = "\n\n".join(page.text for page in pages).strip()
        method = self._aggregate_method(pages)

        return ParseResult(
            filename=filename,
            extension=path.suffix.lower(),
            media_type=self.media_type,
            method=method,
            page_count=len(pages),
            char_count=len(text),
            text=text,
            pages=pages,
        )

    def _extract_pages(self, fitz, document) -> list[PageResult]:
        results: list[PageResult] = []
        ocr_engine = get_ocr_engine() if settings.pdf_ocr_fallback else None

        for index, page in enumerate(document):
            page_text = (page.get_text("text") or "").strip()
            if len(page_text) >= MIN_TEXT_CHARS_PER_PAGE:
                results.append(PageResult(index=index, text=page_text, method=ExtractMethod.TEXT))
                continue

            if not settings.pdf_ocr_fallback:
                results.append(PageResult(index=index, text=page_text, method=ExtractMethod.TEXT))
                continue

            if ocr_engine is None or not ocr_engine.available:
                results.append(PageResult(index=index, text=page_text, method=ExtractMethod.TEXT))
                continue

            results.append(PageResult(index=index, text=self._ocr_page(fitz, page, ocr_engine), method=ExtractMethod.OCR))

        return results

    def _ocr_page(self, fitz, page, ocr_engine) -> str:
        from PIL import Image

        zoom = settings.ocr_dpi / 72
        matrix = fitz.Matrix(zoom, zoom)
        pixmap = page.get_pixmap(matrix=matrix, alpha=False)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        try:
            return ocr_engine.image_to_text(image)
        except OcrUnavailableError:
            return ""

    def _aggregate_method(self, pages: list[PageResult]) -> ExtractMethod:
        methods = {page.method for page in pages}
        if len(methods) == 1:
            return next(iter(methods))
        return ExtractMethod.HYBRID
