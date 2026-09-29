class ParserError(Exception):
    """Base error for the parsing pipeline."""


class UnsupportedFileTypeError(ParserError):
    def __init__(self, extension: str) -> None:
        self.extension = extension
        super().__init__(f"Unsupported file type: {extension}")


class FileTooLargeError(ParserError):
    def __init__(self, size_bytes: int, limit_mb: int) -> None:
        self.size_bytes = size_bytes
        self.limit_mb = limit_mb
        super().__init__(f"File size {size_bytes} bytes exceeds limit of {limit_mb} MB")


class OcrUnavailableError(ParserError):
    def __init__(self, detail: str = "") -> None:
        super().__init__(f"OCR engine is not available. {detail}".strip())
