import { useRef, useState } from "react";

import { qrDecodeOne, type QrDecodeFileResult } from "../api/tools";
import { DataTable, type Column } from "../components/DataTable";

interface RowResult {
  key: string;
  filename: string;
  size_kb: number;
  status: "排队" | "进行中" | "成功" | "失败";
  qr_count: number | null;
  texts: string[];
  server_ms: number | null;
  client_ms: number | null;
  error: string | null;
}

interface RunStats {
  total: number;
  ok: number;
  fail: number;
  wall_ms: number; // 总墙钟时间
  avg_server_ms: number;
  avg_client_ms: number;
  throughput_rps: number;
}

const ACCEPT = "image/png,image/jpeg,image/bmp,image/gif,image/tiff,image/webp";

let rowSeq = 0;

export default function QrToTextPage() {
  const [files, setFiles] = useState<File[]>([]);
  const [concurrency, setConcurrency] = useState(5);
  const [repeat, setRepeat] = useState(1); // 每文件复制 N 份，模拟批量
  const [rows, setRows] = useState<RowResult[]>([]);
  const [running, setRunning] = useState(false);
  const [stats, setStats] = useState<RunStats | null>(null);
  const cancelRef = useRef(false);

  function addFiles(list: FileList | File[]) {
    const incoming = Array.from(list).filter((f) => f.size > 0);
    if (!incoming.length) return;
    setFiles((prev) => [...prev, ...incoming]);
    setRows((prev) => [
      ...prev,
      ...incoming.map((f, i) => ({
        key: `${Date.now()}-${i}-${rowSeq++}`,
        filename: f.name,
        size_kb: Math.round((f.size / 1024) * 10) / 10,
        status: "排队" as const,
        qr_count: null,
        texts: [],
        server_ms: null,
        client_ms: null,
        error: null,
      })),
    ]);
  }

  function clearAll() {
    setFiles([]);
    setRows([]);
    setStats(null);
  }

  function patchRow(key: string, patch: Partial<RowResult>) {
    setRows((prev) => prev.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  }

  async function startRun() {
    if (!rows.length || running) return;
    cancelRef.current = false;
    setRunning(true);
    setStats(null);

    // 重置所有行状态
    const snapshot = rows.map((r) => ({ ...r }));
    setRows(snapshot.map((r) => ({ ...r, status: "排队" as const, qr_count: null, texts: [], server_ms: null, client_ms: null, error: null })));

    // 构造任务队列：每个文件复制 repeat 份（第 2 份起文件名加 (副本N)）
    const tasks: { file: File; rowKey: string; label: string }[] = [];
    for (let rep = 0; rep < Math.max(1, repeat); rep += 1) {
      rows.forEach((row, idx) => {
        const file = files[idx];
        if (!file) return;
        tasks.push({
          file,
          rowKey: `${row.key}#${rep}`,
          label: rep === 0 ? row.filename : `${row.filename} (副本${rep + 1})`,
        });
      });
    }
    const taskRows: RowResult[] = tasks.map((t) => ({
      key: t.rowKey,
      filename: t.label,
      size_kb: Math.round((t.file.size / 1024) * 10) / 10,
      status: "排队",
      qr_count: null,
      texts: [],
      server_ms: null,
      client_ms: null,
      error: null,
    }));
    setRows(taskRows);

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
          const results = await qrDecodeOne(task.file);
          const clientMs = performance.now() - t0;
          const r: QrDecodeFileResult = results[0];
          patchRow(task.rowKey, {
            status: r.ok ? "成功" : "失败",
            qr_count: r.qr_count,
            texts: r.texts,
            server_ms: r.server_ms,
            client_ms: Math.round(clientMs * 10) / 10,
            error: r.error,
          });
          if (r.ok) {
            ok += 1;
            sumServer += r.server_ms;
            sumClient += clientMs;
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

  function stopRun() {
    cancelRef.current = true;
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
    { key: "qr", header: "二维码数", render: (r) => r.qr_count ?? "—" },
    {
      key: "text",
      header: "解码文本",
      cellClassName: "wrap",
      render: (r) =>
        r.texts.length ? (
          <span title={r.texts.join("\n")}>{r.texts.join(" | ")}</span>
        ) : r.error ? (
          <span style={{ color: "#b91c1c" }}>{r.error}</span>
        ) : (
          "—"
        ),
    },
    {
      key: "server_ms",
      header: "后端耗时(ms)",
      render: (r) => r.server_ms ?? "—",
    },
    { key: "client_ms", header: "总耗时(ms)", render: (r) => r.client_ms ?? "—" },
  ];

  return (
    <div className="page">
      <header className="page__header">
        <h1>QR-to-Text</h1>
        <p>批量上传二维码图片，后端识别并解析文本；可设置并发数测试解码速度。</p>
      </header>

      <section className="panel">
        <div className="panel__toolbar">
          <h2>压测控制台</h2>
          <div className="panel__toolbar-actions">
            {!running ? (
              <button
                type="button"
                className="primary primary--inline"
                disabled={!rows.length}
                onClick={() => void startRun()}
              >
                开始识别（{rows.length || files.length} 个任务）
              </button>
            ) : (
              <button type="button" onClick={stopRun}>
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
              max={100}
              value={concurrency}
              disabled={running}
              onChange={(e) => setConcurrency(Math.max(1, Math.min(100, Number(e.target.value) || 1)))}
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
            ? "拖拽图片到此处，或点击右上角「添加图片」（支持 PNG/JPG/BMP/GIF/TIFF/WEBP）"
            : `已选 ${files.length} 个文件（任务数 = 文件数 × 复制份数 = ${files.length * Math.max(1, repeat)}）`}
        </div>

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
          <div className="panel__toolbar-actions">
            <span className="muted">{new Date().toLocaleTimeString()}</span>
          </div>
        </div>
        <DataTable
          columns={columns}
          rows={rows}
          rowKey={(r) => r.key}
          loading={running && rows.length === 0}
          emptyText="尚未添加图片"
          loadingText="准备中…"
        />
      </section>
    </div>
  );
}
