interface PaginationProps {
  page: number;
  pageSize: number;
  total: number;
  pageSizeOptions?: number[];
  onChangePage: (page: number) => void;
  onChangePageSize: (size: number) => void;
}

/** 通用分页条：总数 + 每页条数 + 上一页/页码/下一页 */
export function Pagination({
  page,
  pageSize,
  total,
  pageSizeOptions = [10, 20, 50],
  onChangePage,
  onChangePageSize,
}: PaginationProps) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const current = Math.min(page, pageCount);

  // 页码窗口：最多显示 7 个，当前页居中
  const windowSize = 7;
  let start = Math.max(1, current - Math.floor(windowSize / 2));
  const end = Math.min(pageCount, start + windowSize - 1);
  start = Math.max(1, end - windowSize + 1);
  const pages: number[] = [];
  for (let p = start; p <= end; p += 1) pages.push(p);

  return (
    <div className="pagination">
      <span className="pagination__total">
        共 {total} 条，第 {current}/{pageCount} 页
      </span>
      <label className="pagination__size">
        每页
        <select
          value={pageSize}
          onChange={(event) => onChangePageSize(Number(event.target.value))}
        >
          {pageSizeOptions.map((size) => (
            <option key={size} value={size}>
              {size}
            </option>
          ))}
        </select>
        条
      </label>
      <div className="pagination__nav">
        <button
          type="button"
          disabled={current <= 1}
          onClick={() => onChangePage(current - 1)}
        >
          上一页
        </button>
        {start > 1 && <span className="pagination__ellipsis">…</span>}
        {pages.map((p) => (
          <button
            key={p}
            type="button"
            className={p === current ? "pagination__page pagination__page--active" : "pagination__page"}
            onClick={() => onChangePage(p)}
          >
            {p}
          </button>
        ))}
        {end < pageCount && <span className="pagination__ellipsis">…</span>}
        <button
          type="button"
          disabled={current >= pageCount}
          onClick={() => onChangePage(current + 1)}
        >
          下一页
        </button>
      </div>
    </div>
  );
}
