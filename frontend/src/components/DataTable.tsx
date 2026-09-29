import type { ReactNode } from "react";

export interface Column<T> {
  key: string;
  header: ReactNode;
  render: (row: T) => ReactNode;
  /** 单元格附加 class（mono / nowrap / wrap 等，沿用现有样式） */
  cellClassName?: string;
  headerClassName?: string;
}

interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string | number;
  /** 固定总高度（px），与行数无关：0 行不塌缩，100 行内部滚动 */
  height?: number;
  emptyText?: ReactNode;
  loadingText?: ReactNode;
  loading?: boolean;
}

/**
 * 通用数据表格：容器高度恒定（默认 440px ≈ 表头 + 10 行）。
 * 行数 0 或 100 外框高度始终一致——少则底部留白，多则内部滚动，表头 sticky 固定；
 * 空态/加载态以占位行渲染在表体内，外框高度不变。
 */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  height = 440,
  emptyText = "暂无数据",
  loadingText = "加载中…",
  loading = false,
}: DataTableProps<T>) {
  return (
    <div className="data-table-frame" style={{ height }}>
      <div className="table-wrap data-table-frame__scroll">
        <table className="data-table">
          <thead>
            <tr>
              {columns.map((col) => (
                <th key={col.key} className={col.headerClassName}>
                  {col.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr className="data-table__placeholder">
                <td colSpan={columns.length}>{loading ? loadingText : emptyText}</td>
              </tr>
            ) : (
              rows.map((row) => (
                <tr key={rowKey(row)}>
                  {columns.map((col) => (
                    <td key={col.key} className={col.cellClassName}>
                      {col.render(row)}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default DataTable;
