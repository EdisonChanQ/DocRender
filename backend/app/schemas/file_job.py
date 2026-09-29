from datetime import datetime

from pydantic import BaseModel, Field

JOB_STATES = {0: "待处理", 1: "处理中", 2: "成功", 3: "失败", 4: "已取消", 5: "等待OCR"}
JOB_STEPS = {"split", "normalize", "rectify", "match", "slice"}

# 页级状态（与 file_page_service.PAGE_STATE_* 一致，供聚合进度用）
PAGE_STATE_PENDING = 0
PAGE_STATE_PROCESSING = 1
PAGE_STATE_DONE = 2
PAGE_STATE_FAILED = 3


class FileJobPublic(BaseModel):
    id: int
    code: str = Field(description="文件ID（JOB+时间+随机）")
    file_hash: str = Field(description="SHA-256 十六进制")
    file_name: str
    rel_path: str = Field(description="相对路径（与路径表 default 共享目录拼接）")
    file_size: int
    extension: str | None = None
    category_id: int
    category_name: str | None = None
    template_id: int | None = None
    state: int
    state_label: str
    step: str | None = None
    instance_id: str | None = None
    claimed_datetime: datetime | None = None
    lease_expires_datetime: datetime | None = None
    heartbeat_datetime: datetime | None = None
    retry_count: int
    max_retry: int
    priority: int
    error_msg: str | None = None
    page_count: int | None = None
    # —— 页级进度（由 FilePage 聚合而来，非任务表字段）——
    # 流水线登记产物前 total=0；登记后 total=分页页数，done=已提取完成的页数。
    page_total: int = 0
    page_done: int = 0
    output_rel_path: str | None = None
    created_by: str | None = None
    created_datetime: datetime | None = None
    updated_by: str | None = None
    updated_datetime: datetime | None = None


class FileJobPage(BaseModel):
    items: list[FileJobPublic]
    total: int
    page: int
    page_size: int


class ClaimRequest(BaseModel):
    instance_id: str = Field(min_length=1, max_length=64, description="服务实例标识")
    lease_minutes: int = Field(default=10, ge=1, le=120, description="租约时长（分钟）")


class HeartbeatRequest(BaseModel):
    instance_id: str = Field(min_length=1, max_length=64)
    step: str | None = Field(default=None, description="当前阶段（可选上报）")
    lease_minutes: int = Field(default=10, ge=1, le=120)


class CompleteRequest(BaseModel):
    instance_id: str = Field(min_length=1, max_length=64)
    page_count: int | None = Field(default=None, ge=0)
    output_rel_path: str | None = Field(default=None, max_length=512)


class FailRequest(BaseModel):
    instance_id: str = Field(min_length=1, max_length=64)
    error_msg: str = Field(min_length=1, max_length=1024)
