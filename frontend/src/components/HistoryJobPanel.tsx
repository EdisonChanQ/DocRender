import { useCallback, useEffect, useState } from "react";

import { jobResult, type JobResult } from "../api/filePage";
import { fetchFileJobs, type FileJob } from "../api/fileJob";
import PageResultTables from "./PageResultTables";

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

const PAGE_SIZE = 5;
const REFRESH_MS = 5000;

/**
 * 历史任务面板（右列）：列出已注册任务，**点击行弹窗**查看该任务逐页字段解析明细。
 *
 * - 页级进度：page_done / page_total（后端由 FilePage 聚合）
 * - 自动刷新：默认每 5 秒静默重拉（列表 + 弹窗内详情），可用开关关闭；
 *   静默刷新不置 loading，避免进度推进时界面闪烁。
 */
export default function HistoryJobPanel({ refreshToken = 0 }: { refreshToken?: number }) {
  const [jobs, setJobs] = useState<FileJob[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);

  // 弹窗：当前查看的任务 + 其明细快照
  const [active, setActive] = useState<FileJob | null>(null);
  const [detail, setDetail] = useState<JobResult | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const load = useCallback(
    async (silent = false) => {
      if (!silent) setLoading(true);
      try {
        const res = await fetchFileJobs(page, PAGE_SIZE, null, null);
        setJobs(res.items);
        setTotal(res.total);
        setError(null);
      } catch (err) {
        // 静默刷新失败不打断界面（下次轮询会重试），仅手动刷新时报错
        if (!silent) setError(err instanceof Error ? err.message : "加载任务失败");
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [page],
  );

  const loadDetail = useCallback(async (code: string, silent = false) => {
    if (!silent) setDetailLoading(true);
    try {
      setDetail(await jobResult(code));
      setDetailError(null);
    } catch (err) {
      if (!silent) setDetailError(err instanceof Error ? err.message : "查询失败");
    } finally {
      if (!silent) setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshToken]);

  // 5 秒自动刷新：列表 + 弹窗内详情（打开弹窗时页结果可随进度更新）
  useEffect(() => {
    if (!autoRefresh) return;
    const timer = window.setInterval(() => {
      void load(true);
      if (active) void loadDetail(active.code, true);
    }, REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [autoRefresh, load, loadDetail, active]);

  function openDetail(job: FileJob) {
    setActive(job);
    setDetail(null);
    setDetailError(null);
    void loadDetail(job.code);
  }

  function closeDetail() {
    setActive(null);
    setDetail(null);
    setDetailError(null);
  }

  // Esc 关闭弹窗
  useEffect(() => {
    if (!active) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") closeDetail();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <section className="panel">
      <div className="panel__toolbar">
        <h2>历史任务</h2>
        <div className="panel__toolbar-actions">
          <span className="muted">共 {total} 条</span>
          <label className="auto-refresh" title="每 5 秒自动刷新任务状态与进度">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
            />
            自动刷新
          </label>
          <button type="button" onClick={() => void load()} disabled={loading}>
            刷新
          </button>
        </div>
      </div>

      {error && <p className="error">{error}</p>}

      {/* 列头：与下方每行同网格，让字段严格对齐 */}
      <div className="history-head">
        <span>状态</span>
        <span>文件 / 文件ID</span>
        <span>进度</span>
        <span>注册时间 / 信息</span>
      </div>

      <div className="history-list">
        {jobs.length === 0 && !loading ? (
          <p className="muted" style={{ padding: "12px 0" }}>
            暂无历史任务。
          </p>
        ) : (
          jobs.map((job) => {
            const pct = job.page_total > 0 ? Math.round((job.page_done / job.page_total) * 100) : 0;
            return (
              <button
                key={job.code}
                type="button"
                className="history-item"
                onClick={() => openDetail(job)}
                title="点击查看解析明细"
              >
                <span className={`status status--${jobStatusClass(job.state)}`}>
                  {job.state_label}
                </span>
                <div className="history-item__main">
                  <span className="history-item__name">{job.file_name}</span>
                  <span className="history-item__code mono">{job.code}</span>
                </div>
                <div className="history-item__progress">
                  <div className="progress progress--slim">
                    <div className="progress__bar" style={{ width: `${pct}%` }} />
                  </div>
                  <span className="history-item__pages mono">
                    {job.error_msg
                      ? "—"
                      : job.page_total > 0
                        ? `${job.page_done}/${job.page_total}`
                        : "待分页"}
                  </span>
                </div>
                <div className="history-item__meta">
                  {job.error_msg ? (
                    <span className="history-item__err" title={job.error_msg}>
                      {job.error_msg.length > 20 ? `${job.error_msg.slice(0, 20)}…` : job.error_msg}
                    </span>
                  ) : (
                    <span className="muted">{formatDateTime(job.created_datetime)}</span>
                  )}
                </div>
              </button>
            );
          })
        )}
      </div>

      {totalPages > 1 && (
        <div className="history-pager">
          <button type="button" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
            上一页
          </button>
          <span className="muted">
            {page} / {totalPages}
          </span>
          <button
            type="button"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => p + 1)}
          >
            下一页
          </button>
        </div>
      )}

      {active && (
        <div className="text-viewer-mask" onClick={closeDetail}>
          <div className="text-viewer" onClick={(e) => e.stopPropagation()}>
            <div className="text-viewer__header">
              <div className="text-viewer__title">
                <strong>{active.file_name}</strong>
                <span className="muted mono"> {active.code}</span>
              </div>
              <div className="text-viewer__actions">
                <button type="button" onClick={() => void loadDetail(active.code)} disabled={detailLoading}>
                  {detailLoading ? "查询中…" : "刷新"}
                </button>
                <button type="button" onClick={closeDetail}>
                  关闭
                </button>
              </div>
            </div>
            <div className="text-viewer__body text-viewer__body--plain">
              <div className="panel__meta">
                <span className={`status status--${jobStatusClass(active.state)}`}>
                  {active.state_label}
                </span>
                <span>分类 {active.category_name ?? `#${active.category_id}`}</span>
                <span>模板 {active.template_id != null ? `#${active.template_id}` : "自动匹配"}</span>
                <span>
                  进度 {active.page_done}/{active.page_total || 0} 页
                </span>
                <span>注册 {formatDateTime(active.created_datetime)}</span>
              </div>
              {active.error_msg && <p className="error">{active.error_msg}</p>}
              {(detail?.legend?.match?.rejected_pages ?? 0) > 0 && (
                <p className="status status--warn" style={{ display: "inline-block", margin: "4px 0" }}>
                  本任务有 {detail?.legend?.match?.rejected_pages} 页配准失败，已隔离到 reject/ 目录待人工审计
                </p>
              )}
              {detailError && <p className="error">{detailError}</p>}
              {detailLoading && !detail && <p className="muted">查询中…</p>}
              {detail && <PageResultTables pages={detail.pages} />}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
