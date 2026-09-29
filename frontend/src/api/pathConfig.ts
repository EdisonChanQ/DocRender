import { jsonRequest, request } from "./client";
import type {
  Paginated,
  PathConfig,
  PathConfigCreatePayload,
  PathConfigUpdatePayload,
} from "../types";

export function fetchPathConfigs(
  page: number,
  pageSize: number,
  keyword?: string | null,
): Promise<Paginated<PathConfig>> {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (keyword) params.set("keyword", keyword);
  return request<Paginated<PathConfig>>(`/path-config?${params.toString()}`);
}

export function createPathConfig(payload: PathConfigCreatePayload): Promise<PathConfig> {
  return jsonRequest<PathConfig>("/path-config", "POST", payload);
}

export function updatePathConfig(id: number, payload: PathConfigUpdatePayload): Promise<PathConfig> {
  return jsonRequest<PathConfig>(`/path-config/${id}`, "PUT", payload);
}

export function deletePathConfig(id: number): Promise<void> {
  return request<void>(`/path-config/${id}`, { method: "DELETE" });
}
