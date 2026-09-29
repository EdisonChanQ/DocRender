from fastapi import APIRouter, HTTPException, Query, status

from app.schemas.path_config import (
    PathConfigCreate,
    PathConfigPage,
    PathConfigPublic,
    PathConfigUpdate,
)
from app.services import path_config_service

router = APIRouter()


def _wrap(exc: path_config_service.PathConfigError) -> HTTPException:
    if isinstance(exc, path_config_service.DatabaseNotConfiguredError):
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    if isinstance(exc, path_config_service.PathConfigNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, path_config_service.PathConfigCodeError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("", response_model=PathConfigPage)
def list_path_configs(
    page: int = Query(default=1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(default=10, ge=1, le=200, description="每页条数"),
    keyword: str | None = Query(default=None, max_length=128, description="按编码/名称/路径模糊搜索"),
) -> PathConfigPage:
    try:
        return path_config_service.list_path_configs(page=page, page_size=page_size, keyword=keyword)
    except path_config_service.PathConfigError as exc:
        raise _wrap(exc) from exc


@router.post("", response_model=PathConfigPublic, status_code=status.HTTP_201_CREATED)
def create_path_config(payload: PathConfigCreate) -> PathConfigPublic:
    try:
        return path_config_service.create_path_config(payload)
    except path_config_service.PathConfigError as exc:
        raise _wrap(exc) from exc


@router.get("/{path_id}", response_model=PathConfigPublic)
def get_path_config(path_id: int) -> PathConfigPublic:
    try:
        return path_config_service.get_path_config(path_id)
    except path_config_service.PathConfigError as exc:
        raise _wrap(exc) from exc


@router.put("/{path_id}", response_model=PathConfigPublic)
def update_path_config(path_id: int, payload: PathConfigUpdate) -> PathConfigPublic:
    try:
        return path_config_service.update_path_config(path_id, payload)
    except path_config_service.PathConfigError as exc:
        raise _wrap(exc) from exc


@router.delete("/{path_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_path_config(path_id: int) -> None:
    try:
        path_config_service.delete_path_config(path_id)
    except path_config_service.PathConfigError as exc:
        raise _wrap(exc) from exc
