"""工具类接口：QR-to-Text、OCR-to-Text、LLM-to-Text（/tools 命名空间，后续工具同模式追加）。"""

from __future__ import annotations

from pathlib import Path
from time import perf_counter

from fastapi import APIRouter, File, Form, Query, UploadFile
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.core.errors import ParserError
from app.core.llm_vision import SUPPORTED_IMAGE_EXTS as LLM_IMAGE_EXTS
from app.core.llm_vision import llm_extract_field, llm_ocr_bytes
from app.core.ocr_engine import SUPPORTED_IMAGE_EXTS as OCR_IMAGE_EXTS
from app.core.ocr_engine import ocr_bytes
from app.core.qr_decode import SUPPORTED_IMAGE_EXTS, decode_bytes
from app.schemas.tools import LlmFileResult, OcrFileResult, OcrLine, QrDecodeFileResult

router = APIRouter()


@router.post("/qr-decode", response_model=list[QrDecodeFileResult])
async def qr_decode(files: list[UploadFile] = File(...)) -> list[QrDecodeFileResult]:
    """解码上传图片中的二维码。

    支持一次请求携带多个文件；前端并发压测时通常每请求 1 个文件、
    以 N 个并行请求打满后端。逐文件返回结果与后端耗时，互不影响。
    """
    results: list[QrDecodeFileResult] = []
    max_bytes = settings.max_upload_size_mb * 1024 * 1024

    for file in files:
        start = perf_counter()
        filename = Path(file.filename or "unnamed").name
        suffix = Path(filename).suffix.lower()
        try:
            data = await file.read()
            if not data:
                raise ParserError("文件为空")
            if len(data) > max_bytes:
                raise ParserError(f"文件超过 {settings.max_upload_size_mb}MB 限制")
            if suffix not in SUPPORTED_IMAGE_EXTS:
                raise ParserError(f"不支持的图片格式：{suffix or '未知'}")
            hits = decode_bytes(data)
            results.append(
                QrDecodeFileResult(
                    filename=filename,
                    ok=True,
                    qr_count=len(hits),
                    texts=[h["text"] for h in hits],
                    server_ms=round((perf_counter() - start) * 1000, 2),
                )
            )
        except ParserError as exc:
            results.append(
                QrDecodeFileResult(
                    filename=filename,
                    ok=False,
                    server_ms=round((perf_counter() - start) * 1000, 2),
                    error=str(exc),
                )
            )
        except Exception as exc:  # noqa: BLE001 - 逐文件收集错误
            results.append(
                QrDecodeFileResult(
                    filename=filename,
                    ok=False,
                    server_ms=round((perf_counter() - start) * 1000, 2),
                    error=f"解码异常：{exc}",
                )
            )
        finally:
            await file.close()

    return results


@router.post("/ocr-to-text", response_model=list[OcrFileResult])
async def ocr_to_text(
    files: list[UploadFile] = File(...),
    tier: str = Query(default=settings.ocr_default_tier, description="模型档位：small/medium"),
    mode: str = Query(default="auto", description="识别通道：auto（按图自动）/ detect（整页 det+rec）/ crop（切块 rec-only 快速通道）"),
) -> list[OcrFileResult]:
    """OCR 识别图片中的文字（PP-OCRv6，中英文）。

    推理为 CPU 密集操作，放线程池执行避免阻塞事件循环；
    首次调用会懒加载引擎（约 0.5s~7s），之后进程内常驻共享。
    """
    results: list[OcrFileResult] = []
    max_bytes = settings.max_upload_size_mb * 1024 * 1024

    for file in files:
        start = perf_counter()
        filename = Path(file.filename or "unnamed").name
        suffix = Path(filename).suffix.lower()
        try:
            data = await file.read()
            if not data:
                raise ParserError("文件为空")
            if len(data) > max_bytes:
                raise ParserError(f"文件超过 {settings.max_upload_size_mb}MB 限制")
            if suffix not in OCR_IMAGE_EXTS:
                raise ParserError(f"不支持的图片格式：{suffix or '未知'}")
            channel, lines = await run_in_threadpool(ocr_bytes, data, tier, mode)
            results.append(
                OcrFileResult(
                    filename=filename,
                    ok=True,
                    line_count=len(lines),
                    lines=[OcrLine(**line) for line in lines],
                    full_text="\n".join(line["text"] for line in lines),
                    tier=tier,
                    channel=channel,
                    server_ms=round((perf_counter() - start) * 1000, 2),
                )
            )
        except ParserError as exc:
            results.append(
                OcrFileResult(
                    filename=filename,
                    ok=False,
                    tier=tier,
                    server_ms=round((perf_counter() - start) * 1000, 2),
                    error=str(exc),
                )
            )
        except Exception as exc:  # noqa: BLE001 - 逐文件收集错误
            results.append(
                OcrFileResult(
                    filename=filename,
                    ok=False,
                    tier=tier,
                    server_ms=round((perf_counter() - start) * 1000, 2),
                    error=f"识别异常：{exc}",
                )
            )
        finally:
            await file.close()

    return results


@router.post("/llm-to-text", response_model=list[LlmFileResult])
async def llm_to_text(
    files: list[UploadFile] = File(...),
    model: str = Query(default="", description="模型名，留空用 .env 的 LLM_MODEL"),
    field_key: str = Query(default="", description="字段键；与 label 同时传=单字段抽取模式（llm 块用），留空=全文逐行模式"),
    label: str = Query(default="", description="字段中文说明，注入抽取提示词"),
) -> list[LlmFileResult]:
    """多模态大模型识别图片文字（OpenAI 兼容接口）。

    两种模式：
    - 全文（默认）：作为 OCR-to-Text 的高精度兜底通道，适合模糊 / 倾斜 / 手写整页。
    - 单字段抽取：传 field_key + label（llm 块的模板字段定义），只回该字段的值；
      worker 流水线与流程测试页的 llm 块走此模式。
    网络调用为阻塞 I/O，放线程池执行避免阻塞事件循环。
    """
    results: list[LlmFileResult] = []
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    used_model = (model or settings.llm_model).strip()
    field_mode = bool(field_key and label)

    for file in files:
        start = perf_counter()
        filename = Path(file.filename or "unnamed").name
        suffix = Path(filename).suffix.lower()
        try:
            data = await file.read()
            if not data:
                raise ParserError("文件为空")
            if len(data) > max_bytes:
                raise ParserError(f"文件超过 {settings.max_upload_size_mb}MB 限制")
            if suffix not in LLM_IMAGE_EXTS:
                raise ParserError(f"不支持的图片格式：{suffix or '未知'}")
            if field_mode:
                text = await run_in_threadpool(llm_extract_field, data, suffix, field_key, label, None, used_model)
            else:
                text = await run_in_threadpool(llm_ocr_bytes, data, suffix, used_model)
            results.append(
                LlmFileResult(
                    filename=filename,
                    ok=True,
                    text=text,
                    model=used_model,
                    server_ms=round((perf_counter() - start) * 1000, 2),
                )
            )
        except ParserError as exc:
            results.append(
                LlmFileResult(
                    filename=filename,
                    ok=False,
                    model=used_model,
                    server_ms=round((perf_counter() - start) * 1000, 2),
                    error=str(exc),
                )
            )
        except Exception as exc:  # noqa: BLE001 - 逐文件收集错误
            results.append(
                LlmFileResult(
                    filename=filename,
                    ok=False,
                    model=used_model,
                    server_ms=round((perf_counter() - start) * 1000, 2),
                    error=f"识别异常：{exc}",
                )
            )
        finally:
            await file.close()

    return results
