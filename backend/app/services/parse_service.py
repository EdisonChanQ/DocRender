from pathlib import Path

from app.core.errors import FileTooLargeError, ParserError, UnsupportedFileTypeError
from app.core.parsers.registry import get_parser, supported_extensions
from app.schemas.parse import ParseResult, SupportedTypes
from app.config import settings
from app.core.ocr import get_ocr_engine


class ParseService:
    def supported_types(self) -> SupportedTypes:
        engine = get_ocr_engine()
        return SupportedTypes(
            extensions=supported_extensions(),
            ocr_available=engine.available,
            ocr_languages=engine.languages(),
        )

    def parse(self, path: Path, filename: str) -> ParseResult:
        self._validate_size(path)
        extension = path.suffix.lower()
        parser = get_parser(extension)
        if parser is None:
            raise UnsupportedFileTypeError(extension)
        try:
            return parser.parse(path, filename)
        except (ParserError, UnsupportedFileTypeError):
            raise
        except Exception as exc:  # pragma: no cover
            raise ParserError(f"Failed to parse '{filename}': {exc}") from exc

    def _validate_size(self, path: Path) -> None:
        size = path.stat().st_size
        limit = settings.max_upload_size_mb
        if limit and size > limit * 1024 * 1024:
            raise FileTooLargeError(size, limit)


parse_service = ParseService()
