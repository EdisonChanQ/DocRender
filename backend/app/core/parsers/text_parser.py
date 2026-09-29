from pathlib import Path

from app.core.parsers.base import BaseParser
from app.schemas.parse import ExtractMethod, PageResult, ParseResult


class TextParser(BaseParser):
    extensions = (".txt", ".md", ".csv", ".log", ".json", ".xml", ".html", ".htm")
    media_type = "text/plain"

    def parse(self, path: Path, filename: str) -> ParseResult:
        text = self._read(path)
        return ParseResult(
            filename=filename,
            extension=path.suffix.lower(),
            media_type=self.media_type,
            method=ExtractMethod.TEXT,
            page_count=1,
            char_count=len(text),
            text=text,
            pages=[PageResult(index=0, text=text, method=ExtractMethod.TEXT)],
        )

    def _read(self, path: Path) -> str:
        for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
            try:
                return path.read_text(encoding=encoding)
            except UnicodeDecodeError:
                continue
        return path.read_bytes().decode("utf-8", errors="ignore")
