"""模板字段明细路由：挂在 /template/{template_id}/fields 前缀下。"""

from fastapi import APIRouter, HTTPException, status

from app.schemas.template_field import (
    TemplateFieldCreate,
    TemplateFieldPublic,
    TemplateFieldUpdate,
)
from app.services import template_field_service

router = APIRouter()


def _wrap(exc: template_field_service.TemplateFieldError) -> HTTPException:
    if isinstance(exc, template_field_service.DatabaseNotConfiguredError):
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    if isinstance(
        exc,
        (
            template_field_service.TemplateFieldNotFoundError,
            template_field_service.TemplateNotFoundError,
        ),
    ):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, template_field_service.FieldKeyConflictError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("", response_model=list[TemplateFieldPublic])
def list_fields(template_id: int) -> list[TemplateFieldPublic]:
    try:
        return template_field_service.list_fields(template_id)
    except template_field_service.TemplateFieldError as exc:
        raise _wrap(exc) from exc


@router.post("", response_model=TemplateFieldPublic, status_code=status.HTTP_201_CREATED)
def create_field(template_id: int, payload: TemplateFieldCreate) -> TemplateFieldPublic:
    try:
        return template_field_service.create_field(template_id, payload)
    except template_field_service.TemplateFieldError as exc:
        raise _wrap(exc) from exc


@router.get("/{field_id}", response_model=TemplateFieldPublic)
def get_field(field_id: int) -> TemplateFieldPublic:
    try:
        return template_field_service.get_field(field_id)
    except template_field_service.TemplateFieldError as exc:
        raise _wrap(exc) from exc


@router.put("/{field_id}", response_model=TemplateFieldPublic)
def update_field(field_id: int, payload: TemplateFieldUpdate) -> TemplateFieldPublic:
    try:
        return template_field_service.update_field(field_id, payload)
    except template_field_service.TemplateFieldError as exc:
        raise _wrap(exc) from exc


@router.delete("/{field_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_field(field_id: int) -> None:
    try:
        template_field_service.delete_field(field_id)
    except template_field_service.TemplateFieldError as exc:
        raise _wrap(exc) from exc
