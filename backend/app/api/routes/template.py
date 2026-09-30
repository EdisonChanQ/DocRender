from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status

from app.schemas.template import (
    TemplateCreate,
    TemplatePage,
    TemplatePublic,
    TemplateUpdate,
)
from app.services import template_prepare, template_ref_render, template_service

router = APIRouter()


def _wrap(exc: template_service.TemplateError) -> HTTPException:
    if isinstance(exc, template_service.DatabaseNotConfiguredError):
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    if isinstance(exc, template_service.TemplateNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, template_service.CategoryInvalidError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if isinstance(exc, template_service.TemplateCodeGenerationError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
@router.get("", response_model=TemplatePage)
def list_templates(
    page: int = Query(default=1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(default=10, ge=1, le=200, description="每页条数"),
    category_id: int | None = Query(default=None, description="按分类筛选"),
) -> TemplatePage:
    try:
        return template_service.list_templates(page=page, page_size=page_size, category_id=category_id)
    except template_service.TemplateError as exc:
        raise _wrap(exc) from exc


@router.post("/prepare")
async def prepare_template_file(file: UploadFile = File(...)) -> dict:
    """模板制作临时解析：上传 PDF/图片 → 返回逐页预览图（base64）+ 尺寸/dpi。

    纯交互，不落盘不落库；前端选页框选后，把裁出的范本 + 尺寸随创建/更新提交。
    """
    data = await file.read()
    try:
        return template_prepare.prepare_pages(data, file.filename or "")
    except template_prepare.TemplatePrepareError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    finally:
        await file.close()


@router.post("", response_model=TemplatePublic, status_code=status.HTTP_201_CREATED)
def create_template(payload: TemplateCreate) -> TemplatePublic:
    try:
        return template_service.create_template(payload)
    except template_service.TemplateError as exc:
        raise _wrap(exc) from exc


@router.get("/{template_id}", response_model=TemplatePublic)
def get_template(template_id: int) -> TemplatePublic:
    try:
        return template_service.get_template(template_id)
    except template_service.TemplateError as exc:
        raise _wrap(exc) from exc


@router.put("/{template_id}", response_model=TemplatePublic)
def update_template(template_id: int, payload: TemplateUpdate) -> TemplatePublic:
    try:
        return template_service.update_template(template_id, payload)
    except template_service.TemplateError as exc:
        raise _wrap(exc) from exc


@router.post("/{template_id}/ref-render", response_model=TemplatePublic)
async def save_ref_rendered(
    template_id: int,
    dpi: int = Form(..., ge=1),
    width: int = Form(..., ge=1),
    height: int = Form(..., ge=1),
    page_index: int = Form(0, ge=0),
    rotation_deg: float = Form(0.0),
    crop_x: float = Form(..., ge=0),
    crop_y: float = Form(..., ge=0),
    crop_w: float = Form(..., gt=0),
    crop_h: float = Form(..., gt=0),
    preview_w: int = Form(..., ge=1),
    preview_h: int = Form(..., ge=1),
    file: UploadFile | None = File(None, description="源文件（PDF/图片）；有则按门规格真重渲染，缺省用库中既有范本缩放"),
) -> TemplatePublic:
    """模具保存范本：按门规格（dpi/width/height）真重渲染 ref，字段坐标联动缩放，
    入库同一份字节原样备份到 {shared}/categories/{分类code}/{模板code}/ref_image.png。

    ⚠ 不要在这里加范本 base64 之类的普通 Form 字段：Starlette 对非文件 part 有
    1MB 上限（MultiPartParser.max_part_size），范本图必超（"Part exceeded maximum
    size of 1024KB"）。无源路径的底片由服务层直接读库。
    """
    from app.schemas.template import RefRenderRequest

    req = RefRenderRequest(
        dpi=dpi, width=width, height=height, page_index=page_index,
        rotation_deg=rotation_deg, crop_x=crop_x, crop_y=crop_y,
        crop_w=crop_w, crop_h=crop_h, preview_w=preview_w, preview_h=preview_h,
    )
    source = None
    filename = ""
    if file is not None:
        try:
            source = await file.read()
            filename = file.filename or ""
        finally:
            await file.close()
    try:
        return template_service.save_ref_rendered(template_id, req, source, filename)
    except template_ref_render.RefRenderError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except template_service.TemplateError as exc:
        raise _wrap(exc) from exc


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(template_id: int) -> None:
    try:
        template_service.delete_template(template_id)
    except template_service.TemplateError as exc:
        raise _wrap(exc) from exc
