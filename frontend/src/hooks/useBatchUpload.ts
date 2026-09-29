import { useCallback, useState } from "react";

import { registerFileJobs, type BatchUploadItem } from "../api/fileJob";

/** 与后端 /file-job/register 的 ALLOWED_EXTS 对齐（仅 PDF/图片，不含文本类） */
export const BATCH_ACCEPT_EXTS = ".pdf,.png,.jpg,.jpeg,.bmp,.tif,.tiff";

const ALLOWED_EXTS = new Set(["pdf", "png", "jpg", "jpeg", "bmp", "tif", "tiff"]);

export interface BatchState {
  total: number;
  done: number;
  current: string;
  items: BatchUploadItem[];
  /** true = 全部跑完（进度条满、显示关闭按钮），false = 进行中 */
  finished: boolean;
}

/**
 * 批量上传状态机：过滤不支持类型 → 逐个串行注册 → 实时进度 → 结束汇总。
 *
 * 供「文件任务」与「文件解析」两页共用，避免各写一份。
 * 返回 { batch, uploading, run, reset }；run 结束后调用方可自行刷新列表。
 */
export function useBatchUpload() {
  const [batch, setBatch] = useState<BatchState | null>(null);
  const [uploading, setUploading] = useState(false);

  const reset = useCallback(() => setBatch(null), []);

  const run = useCallback(
    async (files: File[], categoryId: number, templateId: number | null): Promise<BatchUploadItem[]> => {
      if (files.length === 0) return [];

      // 前端先按扩展名过滤，避免把明显不支持的文件发到后端（后端也会再校验一次）。
      // 选文件夹时会把 Thumbs.db / .DS_Store 之类一并带进来 → 标 skipped（不算失败，不吓用户）。
      const accepted: File[] = [];
      const skipped: BatchUploadItem[] = [];
      for (const f of files) {
        const ext = f.name.includes(".") ? f.name.split(".").pop()!.toLowerCase() : "";
        if (!ALLOWED_EXTS.has(ext)) {
          skipped.push({
            fileName: f.name,
            fileSize: f.size,
            ok: false,
            skipped: true,
            error: `已跳过（不支持 ${ext ? `.${ext}` : "无扩展名"}）`,
          });
        } else {
          accepted.push(f);
        }
      }

      const grandTotal = files.length;
      setUploading(true);
      setBatch({ total: grandTotal, done: skipped.length, current: "", items: [...skipped], finished: false });

      if (accepted.length === 0) {
        setUploading(false);
        setBatch((prev) => (prev ? { ...prev, finished: true, current: "" } : prev));
        return skipped;
      }

      let accItems: BatchUploadItem[] = [...skipped];
      const acc = await registerFileJobs(accepted, categoryId, templateId, (done, _total, current) => {
        accItems = [...accItems, current];
        setBatch({
          // done 含前端跳过的，故 total 必须用 files.length，否则进度条会超 100%
          total: grandTotal,
          done: skipped.length + done,
          current: current.fileName,
          items: accItems,
          finished: false,
        });
      });

      // 保留结果面板（用户要看哪些失败），只标记完成
      setBatch({ total: grandTotal, done: grandTotal, current: "", items: acc, finished: true });
      setUploading(false);
      return acc;
    },
    [],
  );

  return { batch, uploading, run, reset };
}

/** 汇总文案：「成功 N，失败 N，跳过 N（共 N）」 */
export function summarizeBatch(items: BatchUploadItem[], total: number): string {
  const ok = items.filter((i) => i.ok).length;
  const fail = items.filter((i) => !i.ok && !i.skipped).length;
  const skip = items.filter((i) => i.skipped).length;
  return (
    `批量上传完成：成功 ${ok} 个` +
    (fail ? `，失败 ${fail} 个` : "") +
    (skip ? `，跳过 ${skip} 个非支持类型` : "") +
    `（共 ${total} 个）`
  );
}
