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
