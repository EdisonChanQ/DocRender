from datetime import datetime

from pydantic import BaseModel, Field


class RefRenderRequest(BaseModel):
    """模具重渲染 + 保存范本的参数（路由层以 multipart Form 逐字段接收）。

    crop/preview_size 为前端 150dpi 预览坐标系（旋转后）的值，与 applyCrop 同源。
    has_source 由路由按是否附带文件设置。
    """

    dpi: int = Field(ge=1, description="门 dpi：上传件与范本共同的渲染口径")
    width: int = Field(ge=1, description="声明宽（像素）= 范本最终尺寸")
    height: int = Field(ge=1, description="声明高（像素）= 范本最终尺寸")
    page_index: int = Field(default=0, ge=0, description="源文件取第几页")
    rotation_deg: float = Field(default=0.0, description="纠偏角（度，正=顺时针，与前端一致）")
    crop_x: float = Field(ge=0)
    crop_y: float = Field(ge=0)
    crop_w: float = Field(gt=0)
    crop_h: float = Field(gt=0)
    preview_w: int = Field(ge=1)
    preview_h: int = Field(ge=1)
    # 无源文件（二次框选旧范本）时的缩放底片 = 库中既有 ref_image，由服务层直接取，
    # 不经 multipart 传输 —— Starlette 对普通 form 字段有 1MB/part 硬上限
    # （"Part exceeded maximum size of 1024KB"），范本 base64 必超；文件 part 反而不限。


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
    backup_path: str | None = Field(
        default=None, description="范本图共享目录备份相对路径（categories/分类code/模板code/ref_image.*）"
    )
    backup_ok: bool | None = Field(
        default=None, description="本次保存的共享目录备份是否成功（未涉及范本更新时为 null）"
    )
    backup_error: str | None = Field(default=None, description="备份失败原因（backup_ok=false 时给出）")
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
