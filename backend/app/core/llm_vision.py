"""LLM-to-Text 识别引擎（多模态大模型看图输出文字）。

作为 OCR-to-Text（PP-OCRv6）之外的高精度兜底通道：对模糊 / 倾斜 / 手写 /
低对比度等传统 OCR 吃力的扫描件，交给多模态大模型直接识别整图文字。

实现要点：
- 走 OpenAI 兼容的 chat/completions 接口（base_url 可指向任意兼容网关，
  如 GPT-4o / Qwen-VL / 通义千问 / 本地 vLLM 等），不绑定单一厂商。
- 图片以 data URL（base64）内联进 message content，标准多模态消息格式。
- 只用标准库 urllib，不新增第三方依赖（保持 requirements 不变）。
- 网络调用为 I/O 阻塞，由路由层放进线程池执行，避免阻塞事件循环。
- 配置来自 settings（llm_api_key / llm_base_url / llm_model / llm_timeout_seconds）。

安全：api_key 仅存在于 .env，绝不写入日志或返回给客户端；错误信息只回显
HTTP 状态与泛化原因，不泄露密钥。
"""

from __future__ import annotations

import base64
import json
import mimetypes
import urllib.error
import urllib.request
from typing import Any

from app.config import settings
from app.core.errors import ParserError

SUPPORTED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp"}

# 识别提示词：要求逐行还原图片文字，不解释、不翻译、不添加多余内容。
_SYSTEM_PROMPT = (
    "你是票据扫描件的文字识别助手。请识别图片中出现的全部文字，"
    "按自上而下、自左到右的顺序逐行输出原文，不要翻译、不要解释、"
    "不要补全或臆测缺失内容。若某处无法辨认，请原样保留为空。"
)


class LlmError(ParserError):
    """LLM 识别失败。"""


def _data_url(data: bytes, suffix: str) -> str:
    """把图片字节转成 data URL；未知类型回退 application/octet-stream。"""
    mime = mimetypes.guess_type(f"x{suffix}")[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _build_payload(data_url: str, model: str, temperature: float) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "请识别这张图片中的所有文字，逐行输出。"},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
        "temperature": temperature,
    }


def _resolve_base_url() -> str:
    base = (settings.llm_base_url or "").strip().rstrip("/")
    if not base:
        return "https://api.openai.com/v1"
    # 用户可能只填 host（如 https://api.openai.com），也可能已带 /v1
    if base.endswith("/v1"):
        return base
    return f"{base}/v1"


def llm_ocr_bytes(data: bytes, suffix: str, model: str | None = None, temperature: float = 0.0) -> str:
    """识别图片字节流中的文字，返回纯文本（多行）。"""
    return _call_chat(_build_payload(_data_url(data, suffix), model or settings.llm_model, temperature))


# ---- 单字段兜底抽取（worker 流水线：OCR 低置信度时调用） ----

_FIELD_SYSTEM_PROMPT = (
    "你是票据字段识别助手。图片是按模板坐标裁出的**单个字段区域**，"
    "你的任务只输出该字段的值本身：不要字段名、不要解释、不要引号或 JSON。"
    "区域内若含相邻文字（坐标有外扩），只提取属于该字段的内容。"
    "务必忠实转写：按图中原样输出字符，不要翻译、不要归一化、不要把符号"
    "（如 xx、ditto、斜杠、涂改、无法辨认的手写记号）替换成你推测的含义或汉字。"
    "看不清就按最接近的原字符输出，确实无内容时输出空字符串。"
)


def _field_user_prompt(field_key: str, label: str, ocr_text: str | None) -> str:
    parts = [
        f"字段：{label}（键 {field_key}）。请从图片中识别该字段的值。",
        "该图位于整页单据中，可利用上下文（如表格列位、前后文语义）辅助判断。",
    ]
    if ocr_text:
        parts.append(f"参考：OCR 初步识别为「{ocr_text}」，但置信度不足，可能含误识字符。")
    return "\n".join(parts)


def llm_extract_field(
    data: bytes,
    suffix: str,
    field_key: str,
    label: str,
    ocr_text: str | None = None,
    model: str | None = None,
    temperature: float = 0.0,
) -> str:
    """单字段裁剪图 → 字段值文本（LLM 兜底通道，与 OCR 同坐标同裁块）。

    提示词带上模板字段定义（field_key + 中文标签）；OCR 已识别出的文本作为
    参考注入（明确告知低置信、可能误识），帮助模型对着上下文校正。
    """
    payload = {
        "model": (model or settings.llm_model).strip(),
        "messages": [
            {"role": "system", "content": _FIELD_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _field_user_prompt(field_key, label, ocr_text)},
                    {"type": "image_url", "image_url": {"url": _data_url(data, suffix)}},
                ],
            },
        ],
        "temperature": temperature,
    }
    return _call_chat(payload)


def _call_chat(payload: dict[str, Any]) -> str:
    """执行一次 chat/completions 调用并取回复文本（api_key 不进日志与返回）。"""
    if not settings.llm_api_key:
        raise LlmError("未配置 LLM_API_KEY，请在 .env 中填写后重启服务")
    model = str(payload.get("model") or "").strip()
    if not model:
        raise LlmError("未配置 LLM_MODEL，请在 .env 中填写")

    body = json.dumps(payload).encode("utf-8")
    url = f"{_resolve_base_url()}/chat/completions"
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.llm_api_key}",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=settings.llm_timeout_seconds) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise LlmError(f"LLM 接口返回 HTTP {exc.code}：{detail[:300]}") from exc
    except urllib.error.URLError as exc:
        raise LlmError(f"无法连接 LLM 接口：{exc.reason}") from exc
    except TimeoutError as exc:
        raise LlmError(f"LLM 接口超时（>{settings.llm_timeout_seconds}s）") from exc

    try:
        result = json.loads(raw)
        text = result["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise LlmError("LLM 返回结构无法解析") from exc

    return str(text).strip()
