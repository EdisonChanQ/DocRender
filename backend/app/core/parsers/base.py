from abc import ABC, abstractmethod
from pathlib import Path

from app.schemas.parse import ParseResult


class BaseParser(ABC):
    """Every concrete parser converts one file into a ParseResult."""

    extensions: tuple[str, ...] = ()
    media_type: str = "application/octet-stream"

    def supports(self, extension: str) -> bool:
        return extension.lower() in self.extensions

    @abstractmethod
    def parse(self, path: Path, filename: str) -> ParseResult:
        raise NotImplementedError
