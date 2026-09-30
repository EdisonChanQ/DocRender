"""文件页提取队列路由：/file-page

- 提取实例（OCR / QR）：claim → {id}/slices → {id}/heartbeat → {id}/complete / {id}/fail
- 流水线实例：register-pages 原子登记页+切片产物（match/slice 完成后调用）
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.schemas.file_job import ClaimRequest, HeartbeatRequest
from app.services import file_page_service

router = APIRouter()


class SliceCreateItem(BaseModel):
    slice_index: int = Field(ge=0, description="块序号（页内0基）")
    field_key: str | None = Field(default=None, max_length=64, description="对应模板字段命名（回写结果按此匹配）")
    rel_path: str | None = Field(default=None, max_length=512, description="切片图相对路径")
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(ge=1)
    height: int = Field(ge=1)


class PageCreateItem(BaseModel):
    page_index: int = Field(ge=0)
    orig_rel_path: str | None = Field(default=None, max_length=512)
    normalized_rel_path: str | None = Field(default=None, max_length=512)
    skew: float | None = None
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    dpi: int | None = Field(default=None, ge=1)
    template_id: int | None = None
    match_score: float | None = Field(default=None, ge=0, le=1, description="模板匹配度（ECC 相关系数）")
    page_mode: Literal["full_page", "block"] | None = Field(default=None, description="模板类型")
    slices: list[SliceCreateItem] = Field(default_factory=list)


class RegisterPagesRequest(BaseModel):
    job_code: str = Field(min_length=1, max_length=64)
    instance_id: str = Field(min_length=1, max_length=64)
    pages: list[PageCreateItem] = Field(min_length=0, max_length=500)


class FieldExtract(BaseModel):
    """单字段提取结果三元组。"""

    value: str | None = Field(description="解析值（OCR 文本 / 二维码载荷 / LLM 兜底值；未识别到为 null）")
    source: Literal["OCR", "QR", "LLM"] = Field(description="提取来源：text 块 OCR、qr 块二维码、低置信 OCR 兜底 LLM")
    confidence: float | None = Field(default=None, ge=0, le=1, description="识别置信度 0~1（LLM 兜底为 null）")


class PageResultRequest(BaseModel):
    instance_id: str = Field(min_length=1, max_length=64)
    data: dict[str, FieldExtract] = Field(
        description="页提取结果：{field_key: {value, source, confidence}}，"
        "键为模板标注的字段命名，一页 N 个标注字段即 N 个键",
    )


class PageFailRequest(BaseModel):
    instance_id: str = Field(min_length=1, max_length=64)
    error_msg: str = Field(min_length=1, max_length=1024)


def _wrap(exc: file_page_service.FilePageError) -> HTTPException:
    if isinstance(exc, file_page_service.DatabaseNotConfiguredError):
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    if isinstance(exc, file_page_service.FilePageNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/claim")
def claim_page(req: ClaimRequest):
    """提取实例抢占一个待提取页；无任务返回 null。"""
    try:
        return file_page_service.claim_page(req)
    except file_page_service.FilePageError as exc:
        raise _wrap(exc) from exc


@router.get("/{page_id}/slices")
def page_slices(page_id: int, instance_id: str = Query(..., min_length=1, max_length=64)):
    """拉页提取详情：页信息 + 该页切片清单（校验归属）。"""
    try:
        return file_page_service.get_page_for_extract(page_id, instance_id)
    except file_page_service.FilePageError as exc:
        raise _wrap(exc) from exc


@router.post("/{page_id}/heartbeat")
def heartbeat_page(page_id: int, req: HeartbeatRequest):
    try:
        return file_page_service.heartbeat_page(page_id, req)
    except file_page_service.FilePageError as exc:
        raise _wrap(exc) from exc


@router.post("/{page_id}/complete")
def complete_page(page_id: int, req: PageResultRequest):
    """回写整页提取结果（{"data": {field_key: {value, source, confidence}}}），页置成功并聚合任务。"""
    try:
        payload = {"data": {k: v.model_dump() for k, v in req.data.items()}}
        return file_page_service.complete_page(page_id, instance_id=req.instance_id, result=payload)
    except file_page_service.FilePageError as exc:
        raise _wrap(exc) from exc


@router.post("/{page_id}/fail")
def fail_page(page_id: int, req: PageFailRequest):
    try:
        return file_page_service.fail_page(page_id, instance_id=req.instance_id, error_msg=req.error_msg)
    except file_page_service.FilePageError as exc:
        raise _wrap(exc) from exc


@router.post("/register-pages")
def register_pages(req: RegisterPagesRequest):
    """流水线登记产物：页行（进提取队列）+ 切片行（纯产物），任务转等待OCR。"""
    try:
        return file_page_service.register_pipeline_output(
            req.job_code, req.instance_id, [p.model_dump() for p in req.pages]
        )
    except file_page_service.FilePageError as exc:
        raise _wrap(exc) from exc


@router.get("/by-job/{job_code}")
def list_pages_by_job(job_code: str):
    try:
        return file_page_service.list_pages_by_job(job_code)
    except file_page_service.FilePageError as exc:
        raise _wrap(exc) from exc
