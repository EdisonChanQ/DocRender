"""文件任务路由：/file-job

- 前端：register（上传落盘+注册）、list、cancel、retry
- 服务实例：claim / heartbeat / complete / fail（抢占式任务队列）
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status

from app.schemas.file_job import (
    ClaimRequest,
    CompleteRequest,
    FailRequest,
    FileJobPage,
    FileJobPublic,
    HeartbeatRequest,
)
from app.services import file_job_service

router = APIRouter()

ALLOWED_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}


def _wrap(exc: file_job_service.FileJobError) -> HTTPException:
    if isinstance(exc, file_job_service.DatabaseNotConfiguredError):
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    if isinstance(exc, file_job_service.FileJobNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/register", response_model=FileJobPublic, status_code=status.HTTP_201_CREATED)
async def register_file_job(
    file: UploadFile = File(...),
    category_id: int = Form(..., description="所属分类ID"),
    template_id: int | None = Form(default=None, description="实例强制模板（可选）"),
    priority: int = Form(default=0),
) -> FileJobPublic:
    """上传文件：hash 计算 → 落盘共享目录（相对路径）→ 注册任务入队。

    落盘结构：jobs/{分类code}/{yyyyMM}/{yyyyMMdd}/{job_code}/source/{原文件名}
    任务所有产物集中在 {job_code}/ 下（source 原件 / pages 页块 / reject 待审），
    删任务产物 = 删一个目录。重复文件（hash 相同）返回 409 + 已有任务 code。
    """
    from app.services import category_service
    from app.services.file_job_service import (
        get_shared_dir,
        job_dir,
        resolve_abs_path,
        sanitize_filename,
        source_rel_path,
    )

    filename = Path(file.filename or "unnamed").name
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"不支持的文件类型：{suffix or '未知'}（PDF/图片）",
        )
    data = await file.read()
    await file.close()
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="文件为空")
    from app.config import settings

    if len(data) > settings.max_upload_size_mb * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"超过 {settings.max_upload_size_mb}MB 限制",
        )

    # 落盘目录要带分类 code（CAT2026090001 这种有语义的名字），先取分类
    try:
        category = category_service.get_category(category_id)
    except category_service.CategoryError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        shared_dir = get_shared_dir("default")
    except file_job_service.FileJobError as exc:
        raise _wrap(exc)

    # 先定 code：路径含 job_code，必须与任务记录一致
    code = file_job_service.generate_code()
    registered_at = datetime.now(timezone.utc)
    rel_path = source_rel_path(category.code, registered_at, code, filename)

    try:
        abs_path = Path(resolve_abs_path(rel_path, shared_dir))
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        # 路径已含 job_code，天然不覆盖他人文件
        abs_path.write_bytes(data)
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"文件落盘失败：{exc}",
        ) from exc

    try:
        return file_job_service.register_job(
            data=data,
            file_name=filename,
            rel_path=rel_path,
            category_id=category_id,
            template_id=template_id,
            priority=priority,
            code=code,
        )
    except file_job_service.FileJobStateError as exc:
        # 注册被拒（重复文件 / code 冲突）→ 清理刚落的孤儿文件，避免留垃圾
        try:
            abs_path.unlink(missing_ok=True)
            parent = abs_path.parent
            task_dir = Path(resolve_abs_path(job_dir(category.code, registered_at, code), shared_dir))
            # 只删空目录，避免误删并发写入的他人内容
            for d in (parent, task_dir):
                if d.exists() and not any(d.iterdir()):
                    d.rmdir()
        except OSError:
            pass
        # 重复注册：409 + 已有任务信息
        detail = str(exc)
        if exc.existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"message": detail, "existing": exc.existing.model_dump(mode="json")},
            )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    except file_job_service.FileJobError as exc:
        raise _wrap(exc)


@router.get("", response_model=FileJobPage)
def list_file_jobs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=200),
    state: int | None = Query(default=None, ge=0, le=5, description="状态筛选 0~5"),
    keyword: str | None = Query(default=None, max_length=128, description="按 code/文件名模糊搜索"),
) -> FileJobPage:
    try:
        return file_job_service.list_jobs(page=page, page_size=page_size, state=state, keyword=keyword)
    except file_job_service.FileJobError as exc:
        raise _wrap(exc) from exc


@router.get("/{code}/result")
def job_result(code: str):
    """反查拼装完整结果，返回四段：

    - `info`      文件任务信息：文件、页数、附件实际页数、执行时间、状态
    - `legend`    提示图例：分类、模板、匹配度（分档）、附件及页数、执行时间、状态
    - `templates` 涉及到的所有模板坐标信息：字段名称、坐标（范本图空间）、是否二维码区域
    - `pages`     逐页产物：页图路径、匹配度、result 提取结果、切片坐标与值
    """
    from app.services import file_page_service

    try:
        result = file_page_service.get_job_result(code)
    except file_page_service.FilePageError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    return result


@router.post("/claim", response_model=FileJobPublic | None)
def claim_job(req: ClaimRequest) -> FileJobPublic | None:
    """服务实例抢占一个待处理任务；无任务返回 null。顺带回收到期租约。"""
    try:
        return file_job_service.claim_job(req)
    except file_job_service.FileJobError as exc:
        raise _wrap(exc) from exc


@router.post("/{code}/heartbeat", response_model=FileJobPublic)
def heartbeat(code: str, req: HeartbeatRequest) -> FileJobPublic:
    try:
        return file_job_service.heartbeat(code, req)
    except file_job_service.FileJobError as exc:
        raise _wrap(exc) from exc


@router.post("/{code}/complete", response_model=FileJobPublic)
def complete(code: str, req: CompleteRequest) -> FileJobPublic:
    try:
        return file_job_service.complete(code, req)
    except file_job_service.FileJobError as exc:
        raise _wrap(exc) from exc


@router.post("/{code}/fail", response_model=FileJobPublic)
def fail(code: str, req: FailRequest) -> FileJobPublic:
    try:
        return file_job_service.fail(code, req)
    except file_job_service.FileJobError as exc:
        raise _wrap(exc) from exc


@router.post("/{code}/cancel", response_model=FileJobPublic)
def cancel(code: str) -> FileJobPublic:
    try:
        return file_job_service.cancel(code)
    except file_job_service.FileJobError as exc:
        raise _wrap(exc) from exc


@router.post("/{code}/retry", response_model=FileJobPublic)
def retry(code: str) -> FileJobPublic:
    try:
        return file_job_service.retry(code)
    except file_job_service.FileJobError as exc:
        raise _wrap(exc) from exc
