import { useCallback, useEffect, useState } from "react";

import { fetchCategories } from "../api/category";
import { cancelFileJob, fetchFileJobs, retryFileJob, type FileJob } from "../api/fileJob";
import { fetchTemplates } from "../api/template";
import BatchPanel from "../components/BatchPanel";
import { DataTable, type Column } from "../components/DataTable";
import { Pagination } from "../components/Pagination";
import { BATCH_ACCEPT_EXTS, summarizeBatch, useBatchUpload } from "../hooks/useBatchUpload";
import { usePagedList } from "../hooks/usePagedList";
import type { Category, Template } from "../types";

const STATE_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "全部状态" },
  { value: "0", label: "待处理" },
  { value: "1", label: "处理中" },
  { value: "5", label: "等待OCR" },
  { value: "2", label: "成功" },
  { value: "3", label: "失败" },
  { value: "4", label: "已取消" },
];

const STEP_LABELS: Record<string, string> = {
  split: "分页",
  normalize: "归一化",
  rectify: "纠正",
  match: "匹配模板",
  slice: "切片",
};

function formatDateTime(value: string | null): string {
  if (!value) return "—";
  return value.replace("T", " ").slice(0, 19);
}

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)}MB`;
  return `${Math.max(1, Math.round(bytes / 1024))}KB`;
}

export default function FileJobPage() {
  const [categoryOptions, setCategoryOptions] = useState<Category[]>([]);
  const [uploadCategoryId, setUploadCategoryId] = useState<number | null>(null);
  // 可选强制模板：选定分类后联动拉该分类下启用的模板
  const [templateOptions, setTemplateOptions] = useState<Template[]>([]);
  const [uploadTemplateId, setUploadTemplateId] = useState<number | null>(null);
  const [uploadMsg, setUploadMsg] = useState<string | null>(null);
  const { batch, uploading, run: runBatch, reset: resetBatch } = useBatchUpload();
  const [busyCode, setBusyCode] = useState<string | null>(null);

  const [stateFilter, setStateFilter] = useState<string>("");
  const [keywordInput, setKeywordInput] = useState("");
  const [keyword, setKeyword] = useState<string | null>(null);

  useEffect(() => {
    fetchCategories(1, 200)
      .then((res) => {
        setCategoryOptions(res.items);
        const first = res.items.find((c) => c.is_enabled) ?? res.items[0];
        if (first) setUploadCategoryId(first.id);
      })
      .catch(() => setCategoryOptions([]));
  }, []);

  // 分类变化 → 联动拉该分类下启用的模板；清空已选模板
  useEffect(() => {
    setUploadTemplateId(null);
    if (uploadCategoryId === null) {
      setTemplateOptions([]);
      return;
    }
    fetchTemplates(1, 200, uploadCategoryId)
      .then((res) => setTemplateOptions(res.items.filter((t) => t.is_enabled)))
      .catch(() => setTemplateOptions([]));
  }, [uploadCategoryId]);

  const fetcher = useCallback(
    (page: number, pageSize: number) =>
      fetchFileJobs(page, pageSize, stateFilter === "" ? null : Number(stateFilter), keyword),
    [stateFilter, keyword],
  );
  const {
    items: jobs,
    total,
    page,
    pageSize,
    loading,
    error: listError,
    reload: loadJobs,
    handlePageChange,
    handlePageSizeChange,
  } = usePagedList<FileJob>(fetcher, { initialPageSize: 10 });

  /** 批量上传：逻辑在 useBatchUpload，这里只管前置校验与结束后刷新列表 */
  async function handleUploadBatch(files: File[]) {
    if (uploadCategoryId === null) {
      setUploadMsg("请先选择分类");
      return;
    }
    if (files.length === 0) return;
    setUploadMsg(null);
    const items = await runBatch(files, uploadCategoryId, uploadTemplateId);
    setUploadMsg(summarizeBatch(items, files.length));
    await loadJobs();
  }

  async function handleCancel(job: FileJob) {
    if (!window.confirm(`确定取消任务「${job.code}」吗？`)) return;
    setBusyCode(job.code);
    try {
      await cancelFileJob(job.code);
      await loadJobs();
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "取消失败");
    } finally {
      setBusyCode(null);
    }
  }

  async function handleRetry(job: FileJob) {
    setBusyCode(job.code);
    try {
      await retryFileJob(job.code);
      await loadJobs();
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "重投失败");
    } finally {
      setBusyCode(null);
    }
  }

  const columns: Column<FileJob>[] = [
    { key: "code", header: "文件ID", cellClassName: "mono", render: (j) => j.code },
    { key: "name", header: "文件名", cellClassName: "wrap", render: (j) => j.file_name },
    { key: "size", header: "大小", render: (j) => formatSize(j.file_size) },
    { key: "category", header: "分类", render: (j) => j.category_name || "（分类已删除）" },
    { key: "template", header: "模板", render: (j) => j.template_id ?? "自动匹配" },
    {
      key: "state",
      header: "状态",
      render: (j) => (
        <span
          className={`status status--${
            j.state === 2 ? "ok" : j.state === 3 ? "error" : j.state === 1 || j.state === 5 ? "info" : "muted"
          }`}
        >
          {j.state_label}
          {j.step ? `·${STEP_LABELS[j.step] ?? j.step}` : ""}
        </span>
      ),
    },
    {
      key: "progress",
      header: "进度",
      render: (j) => {
        if (j.page_total <= 0) {
          return <span className="muted">待分页</span>;
        }
        const pct = Math.round((j.page_done / j.page_total) * 100);
        return (
          <div className="progress-cell">
            <div className="progress progress--slim">
              <div className="progress__bar" style={{ width: `${pct}%` }} />
            </div>
            <span className="muted">
              {j.page_done}/{j.page_total} 页
            </span>
          </div>
        );
      },
    },
    {
      key: "instance", header: "实例", cellClassName: "mono", render: (j) => j.instance_id || "—" },
    {
      key: "retry",
      header: "重试",
      render: (j) => `${j.retry_count}/${j.max_retry}`,
    },
    {
      key: "error",
      header: "信息",
      cellClassName: "wrap",
      render: (j) =>
        j.error_msg ? (
          <span title={j.error_msg} style={{ color: "#b91c1c" }}>
            {j.error_msg.length > 40 ? `${j.error_msg.slice(0, 40)}…` : j.error_msg}
          </span>
        ) : j.page_count != null ? (
          `${j.page_count} 页`
        ) : (
          "—"
        ),
    },
    {
      key: "created",
      header: "注册时间",
      cellClassName: "nowrap",
      render: (j) => formatDateTime(j.created_datetime),
    },
    {
      key: "actions",
      header: "操作",
      headerClassName: "data-table__actions",
      cellClassName: "data-table__actions",
      render: (j) => (
        <>
          {(j.state === 0 || j.state === 3) && (
            <button
              type="button"
              className="row-action"
              onClick={() => void handleCancel(j)}
              disabled={busyCode === j.code}
            >
              取消
            </button>
          )}
          {j.state === 3 && (
            <button
              type="button"
              className="row-action"
              onClick={() => void handleRetry(j)}
              disabled={busyCode === j.code}
            >
              重投
            </button>
          )}
        </>
      ),
    },
  ];

  return (
    <div className="page">
      <header className="page__header">
        <h1>文件任务</h1>
        <p>上传文件落盘共享目录并注册入队，后端服务实例从任务表抢占执行分页、归一化、纠正、匹配、切片与 OCR。</p>
      </header>

      <section className="panel">
        <div className="panel__toolbar">
          <h2>任务队列</h2>
          <div className="panel__toolbar-actions">
            <label className="filter-select">
              上传分类
              <select
                value={uploadCategoryId ?? ""}
                onChange={(e) => setUploadCategoryId(e.target.value === "" ? null : Number(e.target.value))}
              >
                {categoryOptions.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="filter-select" title="不选 = 由流水线按分类自动匹配模板；选定 = 强制使用该模板">
              模板（可选）
              <select
                value={uploadTemplateId ?? ""}
                disabled={uploadCategoryId === null}
                onChange={(e) => setUploadTemplateId(e.target.value === "" ? null : Number(e.target.value))}
              >
                <option value="">自动匹配</option>
                {templateOptions.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.code} {t.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="btn btn--file" title="可一次选多个文件，或选择整个文件夹">
              {uploading ? "注册中…" : "批量上传入队"}
              <input
                type="file"
                accept={BATCH_ACCEPT_EXTS}
                hidden
                multiple
                disabled={uploading}
                onChange={(e) => {
                  const list = Array.from(e.target.files ?? []);
                  if (list.length) void handleUploadBatch(list);
                  e.target.value = "";
                }}
              />
            </label>
            <label className="btn btn--file" title="选择一个文件夹，将其中的 PDF/图片全部入队">
              上传文件夹
              <input
                type="file"
                accept={BATCH_ACCEPT_EXTS}
                hidden
                multiple
                disabled={uploading}
                onChange={(e) => {
                  const list = Array.from(e.target.files ?? []);
                  if (list.length) void handleUploadBatch(list);
                  e.target.value = "";
                }}
                {...({ webkitdirectory: "", directory: "" } as Record<string, string>)}
              />
            </label>
            <label className="filter-select">
              状态
              <select
                value={stateFilter}
                onChange={(e) => setStateFilter(e.target.value)}
              >
                {STATE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="filter-select">
              搜索
              <input
                value={keywordInput}
                placeholder="文件ID / 文件名"
                style={{ width: 160, padding: "4px 8px", fontSize: 13 }}
                onChange={(e) => setKeywordInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    setKeyword(keywordInput.trim() || null);
                  }
                }}
              />
            </label>
            <button type="button" onClick={() => setKeyword(keywordInput.trim() || null)}>
              查询
            </button>
            <span className="muted">共 {total} 条</span>
            <button type="button" onClick={() => void loadJobs()}>
              刷新
            </button>
          </div>
        </div>

        {uploadMsg && <p className="notice">{uploadMsg}</p>}

        {batch && <BatchPanel batch={batch} onClose={resetBatch} />}

        {listError && <p className="error">{listError}</p>}

        <DataTable
          columns={columns}
          rows={jobs}
          rowKey={(j) => j.code}
          loading={loading}
          loadingText="加载中…"
          emptyText="队列为空，上传文件开始第一批任务。"
        />
        {total > 0 && (
          <Pagination
            page={page}
            pageSize={pageSize}
            total={total}
            onChangePage={handlePageChange}
            onChangePageSize={handlePageSizeChange}
          />
        )}
      </section>
    </div>
  );
}
