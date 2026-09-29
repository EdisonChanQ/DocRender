import { jsonRequest, request } from "./client";

/** 切片行（页内块）——产物 + 解析结果 */
export interface FileSliceRow {
  slice_index: number;
  field_key: string | null;
  rel_path: string | null;
  x: number;
  y: number;
  width: number;
  height: number;
  result_content?: string | null;
  source?: string | null;
  confidence?: number | null;
}

/** 页行（提取抢占单元） */
export interface FilePageRow {
  id: number;
  job_id: number;
  job_code: string;
  page_index: number;
  state: number;
  normalized_rel_path: string | null;
  orig_rel_path: string | null;
  skew: number | null;
  width: number | null;
  height: number | null;
  dpi: number | null;
  template_id: number | null;
  slice_count: number | null;
  error_msg: string | null;
  instance_id: string | null;
  retry_count: number;
  result?: { data: Record<string, FieldExtract> } | null;
  slices?: FileSliceRow[];
}

export interface FieldExtract {
  value: string | null;
  source: "OCR" | "QR";
  confidence: number | null;
}

export function claimJob(instanceId: string, leaseMinutes = 5): Promise<{ code: string } | null> {
  return jsonRequest<{ code: string } | null>("/file-job/claim", "POST", {
    instance_id: instanceId,
    lease_minutes: leaseMinutes,
  });
}

export function failJob(code: string, instanceId: string, errorMsg: string) {
  return jsonRequest(`/file-job/${code}/fail`, "POST", {
    instance_id: instanceId,
    error_msg: errorMsg,
  });
}

export interface RegisterPageItem {
  page_index: number;
  orig_rel_path?: string | null;
  normalized_rel_path?: string | null;
  skew?: number | null;
  width?: number | null;
  height?: number | null;
  dpi?: number | null;
  template_id?: number | null;
  slices: {
    slice_index: number;
    field_key?: string | null;
    rel_path?: string | null;
    x: number;
    y: number;
    width: number;
    height: number;
  }[];
}

export function registerPages(
  jobCode: string,
  instanceId: string,
  pages: RegisterPageItem[],
): Promise<{ job_code: string; page_count: number; slice_count: number; pending_pages: number }> {
  return jsonRequest("/file-page/register-pages", "POST", {
    job_code: jobCode,
    instance_id: instanceId,
    pages,
  });
}

export function claimPage(instanceId: string, leaseMinutes = 5): Promise<FilePageRow | null> {
  return jsonRequest<FilePageRow | null>("/file-page/claim", "POST", {
    instance_id: instanceId,
    lease_minutes: leaseMinutes,
  });
}

export function completePage(
  pageId: number,
  instanceId: string,
  data: Record<string, FieldExtract>,
): Promise<FilePageRow> {
  return jsonRequest<FilePageRow>(`/file-page/${pageId}/complete`, "POST", {
    instance_id: instanceId,
    data,
  });
}

export interface JobResultPage {
  page_index: number;
  state: number;
  template_id: number | null;
  result: { data: Record<string, FieldExtract> } | null;
  slices: FileSliceRow[];
  error_msg: string | null;
}

export interface JobResult {
  code: string;
  state: number;
  file_name: string | null;
  page_count: number;
  pages: JobResultPage[];
}

export function jobResult(code: string): Promise<JobResult> {
  return request<JobResult>(`/file-job/${code}/result`);
}
