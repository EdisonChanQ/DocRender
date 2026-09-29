import { jsonRequest, request } from "./client";
import type { Paginated } from "../types";

export interface FileJob {
  id: number;
  code: string;
  file_hash: string;
  file_name: string;
  rel_path: string;
  file_size: number;
  extension: string | null;
  category_id: number;
  category_name: string | null;
  template_id: number | null;
  state: number;
  state_label: string;
  step: string | null;
  instance_id: string | null;
  claimed_datetime: string | null;
  lease_expires_datetime: string | null;
  heartbeat_datetime: string | null;
  retry_count: number;
  max_retry: number;
  priority: number;
  error_msg: string | null;
  page_count: number | null;
  /** 页级进度：总页数（流水线登记产物后才 >0） */
  page_total: number;
  /** 页级进度：已提取完成的页数（成功/失败都算完成） */
  page_done: number;
  output_rel_path: string | null;
  created_by: string | null;
  created_datetime: string | null;
  updated_by: string | null;
  updated_datetime: string | null;
}

export function fetchFileJobs(
  page: number,
  pageSize: number,
  state?: number | null,
  keyword?: string | null,
): Promise<Paginated<FileJob>> {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (state != null) params.set("state", String(state));
  if (keyword) params.set("keyword", keyword);
  return request<Paginated<FileJob>>(`/file-job?${params.toString()}`);
}

export function registerFileJob(
  file: File,
  categoryId: number,
  templateId?: number | null,
): Promise<FileJob> {
  const form = new FormData();
  form.append("file", file);
  form.append("category_id", String(categoryId));
  if (templateId != null) form.append("template_id", String(templateId));
  return request<FileJob>("/file-job/register", { method: "POST", body: form });
}

/** 单个文件的上传结果（批量上传时逐个汇总，失败不中断整批） */
export interface BatchUploadItem {
  fileName: string;
  fileSize: number;
  ok: boolean;
  code?: string;
  /** 注册成功时返回的完整任务对象（供单文件场景展示详情，避免再拉一次） */
  job?: FileJob;
  error?: string;
  /** true = 前端按扩展名主动跳过（如文件夹里的 Thumbs.db），不算「上传失败」 */
  skipped?: boolean;
}

/**
 * 批量注册入队：**逐个串行**调用单文件端点。
 *
 * 后端没有批量端点，且串行可以：
 * 1. 拿到每个文件各自的成功 code / 失败原因，而不是整批一个笼统错误；
 * 2. 避免一次性并发几十个请求压垮后端（落盘 + 生成 code + 写库都是重操作）。
 * 单个失败只记录、不中断整批。
 */
export async function registerFileJobs(
  files: File[],
  categoryId: number,
  templateId?: number | null,
  onProgress?: (done: number, total: number, current: BatchUploadItem) => void,
): Promise<BatchUploadItem[]> {
  const results: BatchUploadItem[] = [];
  for (let i = 0; i < files.length; i += 1) {
    const f = files[i];
    let item: BatchUploadItem;
    try {
      const job = await registerFileJob(f, categoryId, templateId);
      item = { fileName: f.name, fileSize: f.size, ok: true, code: job.code, job };
    } catch (err) {
      item = {
        fileName: f.name,
        fileSize: f.size,
        ok: false,
        error: err instanceof Error ? err.message : "注册失败",
      };
    }
    results.push(item);
    onProgress?.(i + 1, files.length, item);
  }
  return results;
}

export function cancelFileJob(code: string): Promise<FileJob> {
  return jsonRequest<FileJob>(`/file-job/${code}/cancel`, "POST");
}

export function retryFileJob(code: string): Promise<FileJob> {
  return jsonRequest<FileJob>(`/file-job/${code}/retry`, "POST");
}
