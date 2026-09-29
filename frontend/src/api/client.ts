import type { ParseResult, SupportedTypes } from "../types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "/api";

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, init);
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const detail = payload?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : detail && typeof detail.message === "string"
          ? detail.message
          : null;
    throw new Error(message ?? `请求失败（${response.status}）`);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export function jsonRequest<T>(path: string, method: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method };
  if (body !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }
  return request<T>(path, init);
}

export async function fetchSupportedTypes(): Promise<SupportedTypes> {
  return request<SupportedTypes>("/parse/supported");
}

export async function uploadAndParse(file: File): Promise<ParseResult> {
  const form = new FormData();
  form.append("file", file);
  return request<ParseResult>("/parse/upload", { method: "POST", body: form });
}
