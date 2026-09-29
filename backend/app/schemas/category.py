from datetime import datetime

from pydantic import BaseModel, Field


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128, description="分类名称")
    sort_order: int = Field(default=0, ge=0, description="排序值，越小越靠前")
    is_enabled: bool = Field(default=True, description="是否启用")
    remark: str | None = Field(default=None, max_length=512, description="备注")


class CategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    sort_order: int | None = Field(default=None, ge=0)
    is_enabled: bool | None = None
    remark: str | None = Field(default=None, max_length=512)


class CategoryPublic(BaseModel):
    id: int
    code: str = Field(description="分类ID，自动生成，如 CAT2026090001")
    name: str
    sort_order: int
    is_enabled: bool
    remark: str | None = None
    created_by: str | None = None
    created_datetime: datetime | None = None
    updated_by: str | None = None
    updated_datetime: datetime | None = None


class CategoryPage(BaseModel):
    items: list[CategoryPublic]
    total: int = Field(description="符合条件的总记录数")
    page: int = Field(description="当前页码（从 1 开始）")
    page_size: int = Field(description="每页条数")
