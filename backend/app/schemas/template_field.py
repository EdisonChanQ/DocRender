from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

FIELD_KEY_PATTERN = r"^[A-Za-z][A-Za-z0-9_]{0,63}$"
FieldType = Literal["text", "qr"]


class TemplateFieldCreate(BaseModel):
    field_key: str = Field(
        min_length=1, max_length=64, pattern=FIELD_KEY_PATTERN,
        description="字段命名（OCR JSON 输出键，字母开头，允许字母数字下划线，如 payee/amount_upper）",
    )
    label: str = Field(min_length=1, max_length=128, description="字段说明（如 收款人、金额大写）")
    field_type: FieldType = Field(default="text", description="块类型：text 走 OCR，qr 走二维码解码")
    x: int = Field(ge=0, description="块左上角 X（范本图像素）")
    y: int = Field(ge=0, description="块左上角 Y（范本图像素）")
    width: int = Field(ge=1, description="块宽（像素）")
    height: int = Field(ge=1, description="块高（像素）")
    pad: int = Field(default=0, ge=0, le=200, description="外扩像素（识别时四周留量）")
    sort_order: int = Field(default=0, ge=0, description="排序号")
    ref_image: str | None = Field(
        default=None,
        description="字段块样本图（base64 PNG，可带 data: 前缀），前端从范本裁出提交",
    )


class TemplateFieldUpdate(BaseModel):
    field_key: str | None = Field(default=None, min_length=1, max_length=64, pattern=FIELD_KEY_PATTERN)
    label: str | None = Field(default=None, min_length=1, max_length=128)
    field_type: FieldType | None = Field(default=None, description="text / qr")
    x: int | None = Field(default=None, ge=0)
    y: int | None = Field(default=None, ge=0)
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    pad: int | None = Field(default=None, ge=0, le=200)
    sort_order: int | None = Field(default=None, ge=0)
    ref_image: str | None = None


class TemplateFieldPublic(BaseModel):
    id: int
    template_id: int
    field_key: str
    label: str
    field_type: FieldType = "text"
    x: int
    y: int
    width: int
    height: int
    pad: int
    sort_order: int
    ref_image_b64: str | None = Field(
        default=None, description="字段块样本图 base64（仅单查详情返回，列表为 null）"
    )
    created_by: str | None = None
    created_datetime: datetime | None = None
    updated_by: str | None = None
    updated_datetime: datetime | None = None
