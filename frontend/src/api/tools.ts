import { request } from "./client";

export interface QrDecodeFileResult {
  filename: string;
  ok: boolean;
  qr_count: number;
  texts: string[];
  server_ms: number;
  error: string | null;
}

/** 单请求解码一个图片文件（并发压测时以多请求并行） */
export function qrDecodeOne(file: File): Promise<QrDecodeFileResult[]> {
  const form = new FormData();
  form.append("files", file);
  return request<QrDecodeFileResult[]>("/tools/qr-decode", { method: "POST", body: form });
}

export type OcrTier = "small" | "medium";
export type OcrMode = "auto" | "detect" | "crop";

export interface OcrLine {
  text: string;
  score: number;
  box: number[][] | null;
}

export interface OcrFileResult {
  filename: string;
  ok: boolean;
  line_count: number;
  lines: OcrLine[];
  full_text: string;
  tier: string;
  channel: string | null;
  server_ms: number;
  error: string | null;
}

/** 单请求 OCR 一张图片（并发压测时以多请求并行） */
export function ocrToText(file: File, tier: OcrTier, mode: OcrMode = "auto"): Promise<OcrFileResult[]> {
  const form = new FormData();
  form.append("files", file);
  return request<OcrFileResult[]>(`/tools/ocr-to-text?tier=${tier}&mode=${mode}`, {
    method: "POST",
    body: form,
  });
}

export interface LlmFileResult {
  filename: string;
  ok: boolean;
  text: string;
  model: string;
  server_ms: number;
  error: string | null;
}

/** 单请求用多模态大模型识别一张图片文字（OpenAI 兼容接口）。
 *  传 fieldKey+label 进入单字段抽取模式（llm 块：只回字段值）；留空为全文逐行模式。 */
export function llmToText(file: File, model = "", fieldKey = "", label = ""): Promise<LlmFileResult[]> {
  const form = new FormData();
  form.append("files", file);
  const qs = new URLSearchParams();
  if (model) qs.set("model", model);
  if (fieldKey) qs.set("field_key", fieldKey);
  if (label) qs.set("label", label);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return request<LlmFileResult[]>(`/tools/llm-to-text${suffix}`, {
    method: "POST",
    body: form,
  });
}
