from datetime import datetime

from pydantic import BaseModel, Field


class TemplateCreate(BaseModel):
    category_id: int = Field(description="所属分类ID（必选）")
    name: str = Field(min_length=1, max_length=128, description="模板名称")
    is_enabled: bool = Field(default=True, description="是否启用")
    dpi: int | None = Field(default=None, ge=1, description="模板块分辨率")
    width: int | None = Field(default=None, ge=1, description="模板块宽度（像素）")
    height: int | None = Field(default=None, ge=1, description="模板块高度（像素）")
    ref_image: str | None = Field(
        default=None, description="参照范本图（base64 PNG，可带 data: 前缀），框选后由前端裁出提交"
    )


class TemplateUpdate(BaseModel):
    category_id: int | None = Field(default=None, description="迁移到新的有效分类")
    name: str | None = Field(default=None, min_length=1, max_length=128)
    is_enabled: bool | None = None
    dpi: int | None = Field(default=None, ge=1)
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    ref_image: str | None = None


class TemplatePublic(BaseModel):
    id: int
    code: str = Field(description="模板ID，自动生成，如 TPL2026090001")
    category_id: int
    category_name: str | None = Field(
        default=None, description="所属分类名称；分类被删除后为 None（失效状态）"
    )
    name: str
    dpi: int | None = Field(default=None, description="模板块分辨率")
    width: int | None = Field(default=None, description="模板块宽度（像素）")
    height: int | None = Field(default=None, description="模板块高度（像素）")
    ref_image_b64: str | None = Field(
        default=None, description="参照范本图 base64 PNG（仅单查详情返回，列表为 null）"
    )
    is_enabled: bool
    created_by: str | None = None
    created_datetime: datetime | None = None
    updated_by: str | None = None
    updated_datetime: datetime | None = None


class TemplatePage(BaseModel):
    items: list[TemplatePublic]
    total: int
    page: int
    page_size: int
