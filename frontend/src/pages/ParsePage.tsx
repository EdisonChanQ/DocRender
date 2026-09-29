import { useEffect, useRef, useState } from "react";

import { fetchCategories } from "../api/category";
import type { FileJob } from "../api/fileJob";
import { fetchTemplates } from "../api/template";
import BatchPanel from "../components/BatchPanel";
import HistoryJobPanel from "../components/HistoryJobPanel";
import ResultPanel from "../components/ResultPanel";
import { BATCH_ACCEPT_EXTS, summarizeBatch, useBatchUpload } from "../hooks/useBatchUpload";
import type { Category, Template } from "../types";

/**
 * 文件解析页：顶部横向上传条（分类/模板/选文件/提交），下方全宽历史任务列表。
 * 分类必选（不自动默认，未选则禁止提交）；模板可选（不选 = 交由流水线按分类自动匹配）。
 * 注册只落盘 + 入队；解析明细在下方历史任务面板点击记录弹窗查看。
 */
export default function ParsePage() {
  const [categories, setCategories] = useState<Category[]>([]);
  const [categoryId, setCategoryId] = useState<number | null>(null);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [templateId, setTemplateId] = useState<number | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const dirInputRef = useRef<HTMLInputElement>(null);

  const [submitting, setSubmitting] = useState(false);
  const [job, setJob] = useState<FileJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  // 上传成功后自增，触发历史任务列表刷新
  const [historyToken, setHistoryToken] = useState(0);
  const { batch, run: runBatch, reset: resetBatch } = useBatchUpload();

  // 分类下拉（不默认选中：必须人工确认归属分类）
  useEffect(() => {
    fetchCategories(1, 200)
      .then((res) => setCategories(res.items.filter((c) => c.is_enabled)))
      .catch(() => setCategories([]));
  }, []);

  // 分类变化 → 联动拉该分类下启用的模板，并清空已选模板
  useEffect(() => {
    setTemplateId(null);
    if (categoryId === null) {
      setTemplates([]);
      return;
    }
    fetchTemplates(1, 200, categoryId)
      .then((res) => setTemplates(res.items.filter((t) => t.is_enabled)))
      .catch(() => setTemplates([]));
  }, [categoryId]);

  const canSubmit = files.length > 0 && categoryId !== null && !submitting;

  function pickFiles(list: FileList | null) {
    const arr = Array.from(list ?? []);
    if (arr.length === 0) return;
    // 追加而非替换：多次点选可累积；同名同大小视为重复，去掉
    setFiles((prev) => {
      const seen = new Set(prev.map((f) => `${f.name}|${f.size}`));
      const merged = [...prev];
      for (const f of arr) {
        const key = `${f.name}|${f.size}`;
        if (!seen.has(key)) {
          seen.add(key);
          merged.push(f);
        }
      }
      return merged;
    });
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (categoryId === null) {
      setError("请先选择分类");
      return;
    }
    if (files.length === 0) {
      setError("请先选择文件");
      return;
    }
    setSubmitting(true);
    setError(null);
    setSuccessMsg(null);
    try {
      const items = await runBatch(files, categoryId, templateId);
      // 单文件时复用结果面板（展示该任务详情），直接用手头返回的 job，不再拉一次
      setJob(files.length === 1 ? (items.find((i) => i.ok)?.job ?? null) : null);
      setSuccessMsg(summarizeBatch(items, files.length));
      setFiles([]);
      setHistoryToken((n) => n + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "注册失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="page">
      <header className="page__header">
        <h1>文件解析</h1>
        <p>
          上传 PDF 或图片，落盘共享目录并注册为解析任务。分类必选，模板可不选（由流水线按分类自动匹配）。
        </p>
      </header>

      {/* —— 顶部横向上传条 —— */}
      <section className="panel panel--upload">
        <form
          className="upload-bar"
          onSubmit={handleSubmit}
          onDragOver={(e) => {
            e.preventDefault();
            if (!submitting) setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            if (submitting) return;
            pickFiles(e.dataTransfer.files);
          }}
        >
          <label className="field-inline">
            <span>分类 *</span>
            <select
              value={categoryId ?? ""}
              disabled={submitting}
              autoFocus
              onChange={(event) =>
                setCategoryId(event.target.value === "" ? null : Number(event.target.value))
              }
            >
              <option value="" disabled>
                请选择分类
              </option>
              {categories.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}（{c.code}）
                </option>
              ))}
            </select>
          </label>

          <label className="field-inline">
            <span>模板</span>
            <select
              value={templateId ?? ""}
              disabled={submitting || categoryId === null}
              title={
                categoryId === null
                  ? "请先选择分类"
                  : "不选 = 由流水线按分类自动匹配模板；选定 = 强制使用该模板"
              }
              onChange={(event) =>
                setTemplateId(event.target.value === "" ? null : Number(event.target.value))
              }
            >
              <option value="">自动匹配</option>
              {templates.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.code} {t.name}
                </option>
              ))}
            </select>
          </label>

          <span className="upload-bar__divider" />

          <input
            ref={fileInputRef}
            type="file"
            accept={BATCH_ACCEPT_EXTS}
            hidden
            multiple
            disabled={submitting}
            onChange={(e) => {
              pickFiles(e.target.files);
              e.target.value = "";
            }}
          />
          <input
            ref={dirInputRef}
            type="file"
            accept={BATCH_ACCEPT_EXTS}
            hidden
            multiple
            disabled={submitting}
            onChange={(e) => {
              pickFiles(e.target.files);
              e.target.value = "";
            }}
            {...({ webkitdirectory: "", directory: "" } as Record<string, string>)}
          />

          <button
            type="button"
            className="btn--file"
            disabled={submitting}
            onClick={() => fileInputRef.current?.click()}
          >
            选择文件
          </button>
          <button
            type="button"
            className="btn--file"
            disabled={submitting}
            onClick={() => dirInputRef.current?.click()}
          >
            选择文件夹
          </button>
          {files.length > 0 && !submitting && (
            <button type="button" className="btn--ghost" onClick={() => setFiles([])}>
              清空
            </button>
          )}

          <span className="upload-bar__spacer" />

          <span className={`upload-bar__hint ${dragging ? "upload-bar__hint--active" : ""}`}>
            {files.length === 0
              ? "可拖拽文件到此处，支持 PDF / PNG / JPG / BMP / TIFF"
              : `已选 ${files.length} 个文件`}
          </span>

          <button type="submit" className="primary primary--inline" disabled={!canSubmit}>
            {submitting
              ? "注册中…"
              : files.length > 1
                ? `上传并注册 ${files.length} 个文件`
                : "上传并注册"}
          </button>
        </form>

        {files.length > 0 && (
          <ul className="file-chips">
            {files.map((f, i) => (
              <li key={`${f.name}-${f.size}-${i}`} className="file-chip" title={f.name}>
                <span className="file-chip__name">{f.name}</span>
                <span className="file-chip__size">
                  {f.size >= 1024 * 1024
                    ? `${(f.size / 1024 / 1024).toFixed(1)}MB`
                    : `${Math.max(1, Math.round(f.size / 1024))}KB`}
                </span>
                <button
                  type="button"
                  className="file-chip__remove"
                  disabled={submitting}
                  title="移除"
                  onClick={() => setFiles((prev) => prev.filter((_, idx) => idx !== i))}
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        )}

        {batch && <BatchPanel batch={batch} onClose={resetBatch} />}
        {successMsg && <p className="notice">{successMsg}</p>}
        {error && <p className="error">{error}</p>}
        {categoryId === null && <p className="muted">请先选择分类后再提交。</p>}
      </section>

      {/* 单文件上传后的即时回执（多文件入队时无此项） */}
      <ResultPanel job={job} />

      {/* —— 下方全宽历史任务 —— */}
      <HistoryJobPanel refreshToken={historyToken} />
    </div>
  );
}
