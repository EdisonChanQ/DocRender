import { useEffect, useState } from "react";

import { jobResult, type JobResult } from "../api/filePage";
import type { FileJob } from "../api/fileJob";
import PageResultTables from "./PageResultTables";

interface Props {
  job: FileJob | null;
}

/** 与服务端 FILE_JOB_STATE 对齐：0待处理 1处理中 2成功 3失败 4已取消 5等待OCR */
function jobStatusClass(state: number): string {
  if (state === 2) return "ok";
  if (state === 3) return "error";
  if (state === 1 || state === 5) return "info";
  return "muted";
}

function formatDateTime(value: string | null): string {
  if (!value) return "—";
  return value.replace("T", " ").slice(0, 19);
}

export default function ResultPanel({ job }: Props) {
  const [detail, setDetail] = useState<JobResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 换任务时清掉上一份反查快照，避免张冠李戴
  useEffect(() => {
    setDetail(null);
    setError(null);
  }, [job?.code]);

  async function handleQuery() {
    if (!job) return;
    setLoading(true);
    setError(null);
    try {
      setDetail(await jobResult(job.code));
    } catch (err) {
      setError(err instanceof Error ? err.message : "反查失败");
    } finally {
      setLoading(false);
    }
  }

  // 无任务时不占位（右列只保留历史任务面板）
  if (!job) return null;

  return (
    <section className="panel">
      <h2>任务回执</h2>
      <div className="panel__meta">
        <span className={`status status--${jobStatusClass(job.state)}`}>{job.state_label}</span>
        <span>文件ID {job.code}</span>
        <span>分类 {job.category_name ?? `#${job.category_id}`}</span>
        <span>模板 {job.template_id != null ? `#${job.template_id}` : "自动匹配"}</span>
        <span>{job.page_count != null ? `${job.page_count} 页` : "页数待定"}</span>
        <span>注册 {formatDateTime(job.created_datetime)}</span>
      </div>
      {job.error_msg && <p className="error">{job.error_msg}</p>}

      <div style={{ marginTop: 12, display: "flex", alignItems: "center", gap: 12 }}>
        <button type="button" onClick={() => void handleQuery()} disabled={loading}>
          {loading ? "查询中…" : "反查结果"}
        </button>
        <span className="muted">
          已入队，等待流水线实例处理；实例完成分页与提取后此处才有页数据。
        </span>
      </div>

      {error && <p className="error">{error}</p>}

      {detail && (
        <>
          <div className="panel__meta" style={{ marginTop: 16 }}>
            <strong>{detail.file_name ?? detail.code}</strong>
            <span>{detail.page_count} 页</span>
            <span>{detail.pages.length} 条页记录</span>
          </div>
          <PageResultTables pages={detail.pages} />
        </>
      )}
    </section>
  );
}
