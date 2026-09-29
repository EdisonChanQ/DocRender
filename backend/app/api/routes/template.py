from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status

from app.schemas.template import (
    TemplateCreate,
    TemplatePage,
    TemplatePublic,
    TemplateUpdate,
)
from app.services import template_prepare, template_service

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


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(template_id: int) -> None:
    try:
        template_service.delete_template(template_id)
    except template_service.TemplateError as exc:
        raise _wrap(exc) from exc
