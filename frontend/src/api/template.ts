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
  /** true = dpi 是检测到的真实扫描 dpi；false = 估算不出，回退默认值（如 200）。前端据此如实提示。 */
  dpi_estimated?: boolean;
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

export interface RefRenderPayload {
  dpi: number;
  width: number;
  height: number;
  page_index: number;
  rotation_deg: number;
  crop_x: number;
  crop_y: number;
  crop_w: number;
  crop_h: number;
  preview_w: number;
  preview_h: number;
  /** 源文件：后端按门规格真重渲染；缺省时后端用库中既有范本作缩放底片。
   *  范本 base64 不走 multipart——Starlette 普通 form 字段有 1MB/part 上限，范本必超 */
  file?: File | null;
}

/** 模具保存范本：按 dpi/width/height 真重渲染 ref + 备份到共享目录。 */
export function saveRefRendered(id: number, p: RefRenderPayload): Promise<Template> {
  const form = new FormData();
  form.append("dpi", String(p.dpi));
  form.append("width", String(p.width));
  form.append("height", String(p.height));
  form.append("page_index", String(p.page_index));
  form.append("rotation_deg", String(p.rotation_deg));
  form.append("crop_x", String(p.crop_x));
  form.append("crop_y", String(p.crop_y));
  form.append("crop_w", String(p.crop_w));
  form.append("crop_h", String(p.crop_h));
  form.append("preview_w", String(p.preview_w));
  form.append("preview_h", String(p.preview_h));
  if (p.file) form.append("file", p.file);
  return request<Template>(`/template/${id}/ref-render`, { method: "POST", body: form });
}

export function deleteTemplate(id: number): Promise<void> {
  return request<void>(`/template/${id}`, { method: "DELETE" });
}
