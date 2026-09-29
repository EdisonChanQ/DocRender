import { jsonRequest, request } from "./client";
import type {
  Category,
  CategoryCreatePayload,
  CategoryUpdatePayload,
  Paginated,
} from "../types";

export function fetchCategories(page: number, pageSize: number): Promise<Paginated<Category>> {
  return request<Paginated<Category>>(`/category?page=${page}&page_size=${pageSize}`);
}

export function createCategory(payload: CategoryCreatePayload): Promise<Category> {
  return jsonRequest<Category>("/category", "POST", payload);
}

export function updateCategory(id: number, payload: CategoryUpdatePayload): Promise<Category> {
  return jsonRequest<Category>(`/category/${id}`, "PUT", payload);
}

export function deleteCategory(id: number): Promise<void> {
  return request<void>(`/category/${id}`, { method: "DELETE" });
}
