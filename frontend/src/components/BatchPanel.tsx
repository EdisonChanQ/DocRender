import type { BatchState } from "../hooks/useBatchUpload";

interface Props {
  batch: BatchState;
  onClose: () => void;
}

/**
 * 批量上传进度 / 结果面板：进度条 + 逐文件明细（成功/跳过/失败）。
 * 由 useBatchUpload 驱动，两个页面共用。
 */
export default function BatchPanel({ batch, onClose }: Props) {
  const okCount = batch.items.filter((i) => i.ok).length;
  const failCount = batch.items.filter((i) => !i.ok && !i.skipped).length;
  const skipCount = batch.items.filter((i) => i.skipped).length;

  return (
    <div className="panel" style={{ marginBottom: 12, padding: "10px 12px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
        <strong style={{ fontSize: 13 }}>
          批量上传 {batch.done}/{batch.total}
        </strong>
        <span className="muted" style={{ fontSize: 12 }}>
          {batch.finished
            ? `完成：成功 ${okCount}` +
              (failCount ? `，失败 ${failCount}` : "") +
              (skipCount ? `，跳过 ${skipCount}` : "")
            : batch.current
              ? `当前：${batch.current}`
              : "准备中…"}
        </span>
        {batch.finished && (
          <button type="button" style={{ marginLeft: "auto" }} onClick={onClose}>
            关闭明细
          </button>
        )}
      </div>
      <div
        style={{
          height: 6,
          borderRadius: 3,
          background: "var(--color-border-tertiary, #e5e7eb)",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            height: "100%",
            width: `${batch.total ? Math.min(100, Math.round((batch.done / batch.total) * 100)) : 0}%`,
            background: batch.finished ? "#15803d" : "#2563eb",
            transition: "width 0.15s",
          }}
        />
      </div>
      {batch.items.length > 0 && (
        <ul
          style={{
            margin: "8px 0 0",
            padding: 0,
            listStyle: "none",
            maxHeight: 160,
            overflowY: "auto",
            fontSize: 12,
          }}
        >
          {batch.items.map((it, i) => {
            const label = it.ok ? "成功" : it.skipped ? "跳过" : "失败";
            const color = it.ok ? "#15803d" : it.skipped ? "#6b7280" : "#b91c1c";
            return (
              <li key={`${it.fileName}-${i}`} style={{ display: "flex", gap: 8, padding: "2px 0" }}>
                <span className="mono" style={{ color, minWidth: 34 }}>
                  {label}
                </span>
                <span className="wrap" style={{ flex: 1 }}>
                  {it.fileName}
                </span>
                <span className="muted">{it.ok ? it.code : it.error}</span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
