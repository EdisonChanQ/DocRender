import { jsonRequest, request } from "./client";

export type FieldType = "text" | "qr";

export interface TemplateField {
  id: number;
  template_id: number;
  field_key: string;
  label: string;
  field_type: FieldType;
  x: number;
  y: number;
  width: number;
  height: number;
  pad: number;
  sort_order: number;
  ref_image_b64?: string | null;
  created_by: string | null;
  created_datetime: string | null;
  updated_by: string | null;
  updated_datetime: string | null;
}

export interface TemplateFieldPayload {
  field_key: string;
  label: string;
  field_type: FieldType;
  x: number;
  y: number;
  width: number;
  height: number;
  pad: number;
  sort_order: number;
  ref_image?: string | null;
}

export function fetchFields(templateId: number): Promise<TemplateField[]> {
  return request<TemplateField[]>(`/template/${templateId}/fields`);
}

export function createField(templateId: number, payload: TemplateFieldPayload): Promise<TemplateField> {
  return jsonRequest<TemplateField>(`/template/${templateId}/fields`, "POST", payload);
}

export function updateField(
  templateId: number,
  fieldId: number,
  payload: Partial<TemplateFieldPayload>,
): Promise<TemplateField> {
  return jsonRequest<TemplateField>(`/template/${templateId}/fields/${fieldId}`, "PUT", payload);
}

export function deleteField(templateId: number, fieldId: number): Promise<void> {
  return request<void>(`/template/${templateId}/fields/${fieldId}`, { method: "DELETE" });
}
