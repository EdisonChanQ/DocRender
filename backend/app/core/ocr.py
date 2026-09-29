import shutil
from functools import lru_cache
from pathlib import Path

from app.config import settings
from app.core.errors import OcrUnavailableError


class OcrEngine:
    """Thin wrapper around pytesseract so the OCR backend can be swapped later."""

    def __init__(self, lang: str | None = None, tesseract_cmd: str | None = None) -> None:
        self.lang = lang or settings.ocr_lang
        self._tesseract_cmd = tesseract_cmd or settings.tesseract_cmd
        self._configure()

    def _configure(self) -> None:
        try:
            import pytesseract
        except ImportError:
            self._pytesseract = None
            return

        if self._tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = self._tesseract_cmd
        self._pytesseract = pytesseract

    @property
    def available(self) -> bool:
        if getattr(self, "_pytesseract", None) is None:
            return False
        if self._tesseract_cmd:
            return Path(self._tesseract_cmd).exists()
        return shutil.which("tesseract") is not None or Path(
            r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        ).exists()

    def languages(self) -> list[str]:
        if not self.available:
            return []
        try:
            return sorted(self._pytesseract.get_languages(config=""))
        except Exception:
            return []

    def image_to_text(self, image) -> str:
        if not self.available:
            raise OcrUnavailableError("Install Tesseract-OCR and/or set TESSERACT_CMD.")
        return (self._pytesseract.image_to_string(image, lang=self.lang) or "").strip()


@lru_cache
def get_ocr_engine() -> OcrEngine:
    return OcrEngine()
