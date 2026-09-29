from datetime import datetime

from pydantic import BaseModel, Field


class PathConfigCreate(BaseModel):
    code: str = Field(min_length=1, max_length=64, description="路径编码/用途标识，唯一（如 upload、export、template）")
    name: str = Field(min_length=1, max_length=128, description="路径显示名称")
    path: str = Field(min_length=1, max_length=1024, description="文件存放共享路径（UNC 或本地路径）")
    is_enabled: bool = Field(default=True, description="是否启用")
    remark: str | None = Field(default=None, max_length=512, description="备注")


class PathConfigUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    path: str | None = Field(default=None, min_length=1, max_length=1024)
    is_enabled: bool | None = None
    remark: str | None = Field(default=None, max_length=512)


class PathConfigPublic(BaseModel):
    id: int
    code: str = Field(description="路径编码，创建后不可修改")
    name: str
    path: str
    is_enabled: bool
    remark: str | None = None
    created_by: str | None = None
    created_datetime: datetime | None = None
    updated_by: str | None = None
    updated_datetime: datetime | None = None


class PathConfigPage(BaseModel):
    items: list[PathConfigPublic]
    total: int
    page: int
    page_size: int
