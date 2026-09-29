from app.core.parsers.base import BaseParser
from app.core.parsers.image_parser import ImageParser
from app.core.parsers.pdf_parser import PdfParser
from app.core.parsers.text_parser import TextParser

_PARSERS: tuple[BaseParser, ...] = (
    PdfParser(),
    ImageParser(),
    TextParser(),
)


def get_parser(extension: str) -> BaseParser | None:
    ext = extension.lower()
    for parser in _PARSERS:
        if parser.supports(ext):
            return parser
    return None


def supported_extensions() -> list[str]:
    exts: list[str] = []
    for parser in _PARSERS:
        exts.extend(parser.extensions)
    return sorted(set(exts))


__all__ = ["get_parser", "supported_extensions"]
