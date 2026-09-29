from enum import Enum

from pydantic import BaseModel, Field


class ExtractMethod(str, Enum):
    TEXT = "text"
    OCR = "ocr"
    HYBRID = "hybrid"


class PageResult(BaseModel):
    index: int = Field(description="0-based page index")
    text: str = ""
    method: ExtractMethod = ExtractMethod.TEXT


class ParseResult(BaseModel):
    filename: str
    extension: str
    media_type: str
    method: ExtractMethod
    page_count: int
    char_count: int
    text: str
    pages: list[PageResult] = Field(default_factory=list)


class SupportedTypes(BaseModel):
    extensions: list[str]
    ocr_available: bool
    ocr_languages: list[str] = Field(default_factory=list)
