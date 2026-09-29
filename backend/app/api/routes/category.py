from fastapi import APIRouter, HTTPException, Query, status

from app.schemas.category import CategoryCreate, CategoryPage, CategoryPublic, CategoryUpdate
from app.services import category_service

router = APIRouter()


def _wrap(exc: category_service.CategoryError) -> HTTPException:
    if isinstance(exc, category_service.DatabaseNotConfiguredError):
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    if isinstance(exc, category_service.CategoryNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, category_service.CategoryCodeGenerationError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("", response_model=CategoryPage)
def list_categories(
    page: int = Query(default=1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(default=10, ge=1, le=200, description="每页条数"),
) -> CategoryPage:
    try:
        return category_service.list_categories(page=page, page_size=page_size)
    except category_service.CategoryError as exc:
        raise _wrap(exc) from exc


@router.post("", response_model=CategoryPublic, status_code=status.HTTP_201_CREATED)
def create_category(payload: CategoryCreate) -> CategoryPublic:
    try:
        return category_service.create_category(payload)
    except category_service.CategoryError as exc:
        raise _wrap(exc) from exc


@router.put("/{category_id}", response_model=CategoryPublic)
def update_category(category_id: int, payload: CategoryUpdate) -> CategoryPublic:
    try:
        return category_service.update_category(category_id, payload)
    except category_service.CategoryError as exc:
        raise _wrap(exc) from exc


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(category_id: int) -> None:
    try:
        category_service.delete_category(category_id)
    except category_service.CategoryError as exc:
        raise _wrap(exc) from exc
