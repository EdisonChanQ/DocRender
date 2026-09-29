import { jsonRequest, request } from "./client";
import type {
  Paginated,
  Template,
  TemplateCreatePayload,
  TemplateUpdatePayload,
} from "../types";

export interface PreparedPage {
  index: number;
  preview_w: number;
  preview_h: number;
  full_w: number;
  full_h: number;
  dpi: number;
  /** 后端检测的修正角（度，正值=需逆时针旋转摆正） */
  skew: number;
  data_url: string;
}

export interface PreparedFile {
  kind: "pdf" | "image";
  preview_dpi: number;
  page_count: number;
  pages: PreparedPage[];
}

export function prepareTemplateFile(file: File): Promise<PreparedFile> {
  const form = new FormData();
  form.append("file", file);
  return request<PreparedFile>("/template/prepare", { method: "POST", body: form });
}

export function fetchTemplates(
  page: number,
  pageSize: number,
  categoryId?: number | null,
): Promise<Paginated<Template>> {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (categoryId != null) params.set("category_id", String(categoryId));
  return request<Paginated<Template>>(`/template?${params.toString()}`);
}

export function getTemplateDetail(id: number): Promise<Template> {
  return request<Template>(`/template/${id}`);
}

export function createTemplate(payload: TemplateCreatePayload): Promise<Template> {
  return jsonRequest<Template>("/template", "POST", payload);
}

export function updateTemplate(id: number, payload: TemplateUpdatePayload): Promise<Template> {
  return jsonRequest<Template>(`/template/${id}`, "PUT", payload);
}

export function deleteTemplate(id: number): Promise<void> {
  return request<void>(`/template/${id}`, { method: "DELETE" });
}
