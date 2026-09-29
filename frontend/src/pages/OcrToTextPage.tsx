import { useEffect, useRef, useState } from "react";

import { ocrToText, type OcrFileResult, type OcrMode, type OcrTier } from "../api/tools";
import { DataTable, type Column } from "../components/DataTable";

interface RowResult {
  key: string;
  filename: string;
  size_kb: number;
  status: "排队" | "进行中" | "成功" | "失败";
  line_count: number | null;
  text_preview: string;
  avg_score: number | null;
  channel: string | null;
  server_ms: number | null;
  client_ms: number | null;
  error: string | null;
}

interface RunStats {
  total: number;
  ok: number;
  fail: number;
  wall_ms: number;
  avg_server_ms: number;
  avg_client_ms: number;
  throughput_rps: number;
}

const ACCEPT = "image/png,image/jpeg,image/bmp,image/gif,image/tiff,image/webp";

let rowSeq = 0;

function copyText(text: string) {
  void navigator.clipboard.writeText(text).catch(() => window.alert("复制失败，请检查浏览器权限"));
}

export default function OcrToTextPage() {
  const [files, setFiles] = useState<{ key: string; file: File }[]>([]);
  const [concurrency, setConcurrency] = useState(5);
  const [repeat, setRepeat] = useState(1);
  const [tier, setTier] = useState<OcrTier>("small");
  const [mode, setMode] = useState<OcrMode>("auto");
  const [rows, setRows] = useState<RowResult[]>([]);
  const [running, setRunning] = useState(false);
  const [stats, setStats] = useState<RunStats | null>(null);
  const [warmupMs, setWarmupMs] = useState<number | null>(null);
  const [firstRun, setFirstRun] = useState(true);
  const [viewer, setViewer] = useState<{ key: string; filename: string; text: string } | null>(null);
  const cancelRef = useRef(false);

  // 离开页面再回来时重置"首次加载"标记
  useEffect(() => () => undefined, []);

  // ESC 关闭全文弹窗
  useEffect(() => {
    if (!viewer) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setViewer(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [viewer]);

  function addFiles(list: FileList | File[]) {
    const incoming = Array.from(list).filter((f) => f.size > 0);
    if (!incoming.length) return;
    setFiles((prev) => [
      ...prev,
      ...incoming.map((f) => ({ key: `${Date.now()}-${rowSeq++}`, file: f })),
    ]);
  }

  function clearAll() {
    setFiles([]);
    setRows([]);
    setStats(null);
    setWarmupMs(null);
    setFirstRun(true);
  }

  function patchRow(key: string, patch: Partial<RowResult>) {
    setRows((prev) => prev.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  }

  async function startRun() {
    if (!files.length || running) return;
    cancelRef.current = false;
    setRunning(true);
    setStats(null);
    setWarmupMs(null);
    setViewer(null);

    const tasks: { file: File; rowKey: string; label: string }[] = [];
    for (let rep = 0; rep < Math.max(1, repeat); rep += 1) {
      files.forEach((entry, idx) => {
        tasks.push({
          file: entry.file,
          rowKey: `${entry.key}#${rep}`,
          label: rep === 0 ? entry.file.name : `${entry.file.name} (副本${rep + 1})`,
        });
        void idx;
      });
    }
    setRows(
      tasks.map((t) => ({
        key: t.rowKey,
        filename: t.label,
        size_kb: Math.round((t.file.size / 1024) * 10) / 10,
        status: "排队",
        line_count: null,
        text_preview: "",
        avg_score: null,
        channel: null,
        server_ms: null,
        client_ms: null,
        error: null,
      })),
    );

    const started = performance.now();
    let cursor = 0;
    let ok = 0;
    let fail = 0;
    let sumServer = 0;
    let sumClient = 0;

    async function worker() {
      while (cursor < tasks.length && !cancelRef.current) {
        const idx = cursor++;
        const task = tasks[idx];
        patchRow(task.rowKey, { status: "进行中" });
        const t0 = performance.now();
        try {
          const results = await ocrToText(task.file, tier, mode);
          const clientMs = performance.now() - t0;
          const r: OcrFileResult = results[0];
          const scores = r.lines.map((l) => l.score);
          const avg = scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : null;
          patchRow(task.rowKey, {
            status: r.ok ? "成功" : "失败",
            line_count: r.line_count,
            text_preview: r.full_text,
            avg_score: avg === null ? null : Math.round(avg * 1000) / 10,
            channel: r.channel,
            server_ms: r.server_ms,
            client_ms: Math.round(clientMs * 10) / 10,
            error: r.error,
          });
          if (r.ok) {
            ok += 1;
            sumServer += r.server_ms;
            sumClient += clientMs;
            if (firstRun) {
              setWarmupMs(r.server_ms);
              setFirstRun(false);
            }
          } else {
            fail += 1;
          }
        } catch (err) {
          fail += 1;
          patchRow(task.rowKey, {
            status: "失败",
            client_ms: Math.round((performance.now() - t0) * 10) / 10,
            error: err instanceof Error ? err.message : "请求失败",
          });
        }
      }
    }

    const workers = Array.from({ length: Math.max(1, concurrency) }, () => worker());
    await Promise.all(workers);
    const wall = performance.now() - started;

    setStats({
      total: tasks.length,
      ok,
      fail,
      wall_ms: Math.round(wall),
      avg_server_ms: ok ? Math.round((sumServer / ok) * 10) / 10 : 0,
      avg_client_ms: ok ? Math.round((sumClient / ok) * 10) / 10 : 0,
      throughput_rps: wall > 0 ? Math.round((tasks.length / (wall / 1000)) * 100) / 100 : 0,
    });
    setRunning(false);
  }

  const columns: Column<RowResult>[] = [
    { key: "filename", header: "文件名", render: (r) => r.filename },
    { key: "size", header: "大小(KB)", render: (r) => r.size_kb },
    {
      key: "status",
      header: "状态",
      render: (r) => (
        <span
          className={`status status--${
            r.status === "成功" ? "ok" : r.status === "失败" ? "error" : "muted"
          }`}
        >
          {r.status}
        </span>
      ),
    },
    { key: "lines", header: "文本行数", render: (r) => r.line_count ?? "—" },
    {
      key: "channel",
      header: "通道",
      render: (r) =>
        r.channel ? (
          <span className={`status status--${r.channel === "crop" ? "ok" : "muted"}`}>
            {r.channel === "crop" ? "切块" : "整页"}
          </span>
        ) : (
          "—"
        ),
    },
    {
      key: "score",
      header: "平均置信度(%)",
      render: (r) => r.avg_score ?? "—",
    },
    {
      key: "text",
      header: "识别文本",
      cellClassName: "wrap",
      render: (r) =>
        r.error && !r.text_preview ? (
          <span style={{ color: "#b91c1c" }}>{r.error}</span>
        ) : r.text_preview ? (
          <pre
            className="ocr-text-cell"
            title="点击查看全文"
            onClick={() => setViewer({ key: r.key, filename: r.filename, text: r.text_preview })}
          >
            {r.text_preview.length > 200
              ? `${r.text_preview.slice(0, 200)}…（点击查看全文）`
              : r.text_preview}
          </pre>
        ) : (
          "—"
        ),
    },
    { key: "server_ms", header: "后端耗时(ms)", render: (r) => r.server_ms ?? "—" },
    { key: "client_ms", header: "总耗时(ms)", render: (r) => r.client_ms ?? "—" },
  ];

  return (
    <div className="page">
      <header className="page__header">
        <h1>OCR-to-Text</h1>
        <p>批量上传图片，后端 PP-OCRv6 识别中英文文字；可设置并发数与档位测试推理速度。</p>
      </header>

      <section className="panel">
        <div className="panel__toolbar">
          <h2>压测控制台</h2>
          <div className="panel__toolbar-actions">
            {!running ? (
              <button
                type="button"
                className="primary primary--inline"
                disabled={!files.length}
                onClick={() => void startRun()}
              >
                开始识别（{Math.max(1, repeat) * files.length} 个任务）
              </button>
            ) : (
              <button type="button" onClick={() => (cancelRef.current = true)}>
                停止派发
              </button>
            )}
            <button type="button" onClick={clearAll} disabled={running}>
              清空
            </button>
          </div>
        </div>

        <div className="bench-controls">
          <label className="filter-select">
            并发数
            <input
              type="number"
              min={1}
              max={50}
              value={concurrency}
              disabled={running}
              onChange={(e) => setConcurrency(Math.max(1, Math.min(50, Number(e.target.value) || 1)))}
            />
          </label>
          <label className="filter-select">
            每文件复制份数
            <input
              type="number"
              min={1}
              max={200}
              value={repeat}
              disabled={running}
              onChange={(e) => setRepeat(Math.max(1, Math.min(200, Number(e.target.value) || 1)))}
            />
          </label>
          <label className="filter-select">
            模型档位
            <select value={tier} disabled={running} onChange={(e) => setTier(e.target.value as OcrTier)}>
              <option value="small">small（快）</option>
              <option value="medium">medium（准）</option>
            </select>
          </label>
          <label className="filter-select">
            识别通道
            <select value={mode} disabled={running} onChange={(e) => setMode(e.target.value as OcrMode)}>
              <option value="auto">自动（按图判定）</option>
              <option value="crop">切块 rec-only（快）</option>
              <option value="detect">整页 det+rec（全）</option>
            </select>
          </label>
          <label className="btn btn--file">
            添加图片
            <input
              type="file"
              accept={ACCEPT}
              multiple
              hidden
              onChange={(e) => {
                if (e.target.files) addFiles(e.target.files);
                e.target.value = "";
              }}
            />
          </label>
        </div>

        <div
          className="upload-dropzone"
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            addFiles(e.dataTransfer.files);
          }}
        >
          {files.length === 0
            ? "拖拽图片到此处，或点击「添加图片」（支持 PNG/JPG/BMP/GIF/TIFF/WEBP）"
            : `已选 ${files.length} 个文件（任务数 = 文件数 × 复制份数 = ${files.length * Math.max(1, repeat)}）`}
        </div>

        {warmupMs !== null && (
          <p className="page__hint">
            首次请求含引擎懒加载约 {warmupMs.toFixed(0)}ms，之后的任务耗时为纯推理时间。
          </p>
        )}

        {stats && (
          <div className="bench-stats">
            <span>任务 <strong>{stats.total}</strong></span>
            <span className="status status--ok">成功 {stats.ok}</span>
            <span className="status status--error">失败 {stats.fail}</span>
            <span>总墙钟 <strong>{stats.wall_ms}</strong> ms</span>
            <span>平均后端 <strong>{stats.avg_server_ms}</strong> ms/张</span>
            <span>平均端到端 <strong>{stats.avg_client_ms}</strong> ms/张</span>
            <span>吞吐 <strong>{stats.throughput_rps}</strong> 张/秒</span>
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel__toolbar">
          <h2>识别结果</h2>
        </div>
        <DataTable
          columns={columns}
          rows={rows}
          rowKey={(r) => r.key}
          loading={running && rows.length === 0}
          emptyText="尚未添加图片"
          loadingText="准备中…"
          height={520}
        />
      </section>

      {viewer && (
        <div
          className="text-viewer-mask"
          onClick={(e) => {
            if (e.target === e.currentTarget) setViewer(null);
          }}
        >
          <div className="text-viewer" role="dialog" aria-modal="true" aria-label="识别全文">
            <div className="text-viewer__header">
              <strong className="text-viewer__title" title={viewer.filename}>
                {viewer.filename}
              </strong>
              <span className="muted">{viewer.text.length} 字符</span>
              <div className="text-viewer__actions">
                <button type="button" onClick={() => copyText(viewer.text)}>
                  复制全文
                </button>
                <button type="button" onClick={() => setViewer(null)}>
                  关闭
                </button>
              </div>
            </div>
            <pre className="text-viewer__body">{viewer.text}</pre>
          </div>
        </div>
      )}
    </div>
  );
}
