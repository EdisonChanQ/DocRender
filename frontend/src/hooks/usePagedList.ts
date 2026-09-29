import { useCallback, useEffect, useState } from "react";

interface ListPageOptions {
  initialPageSize?: number;
}

/**
 * 后端分页状态管理：维护 page / pageSize / total，
 * 由调用方提供 fetcher 在当前页变化时拉取数据。
 */
export function usePagedList<T>(
  fetcher: (page: number, pageSize: number) => Promise<{ items: T[]; total: number }>,
  options: ListPageOptions = {},
) {
  const [items, setItems] = useState<T[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(options.initialPageSize ?? 10);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setError(null);
      const res = await fetcher(page, pageSize);
      setItems(res.items);
      setTotal(res.total);
      // 删除最后一页数据后可能停留在空页，自动回退
      if (res.items.length === 0 && res.total > 0 && page > 1) {
        setPage(Math.max(1, Math.ceil(res.total / pageSize)));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载数据失败");
    } finally {
      setLoading(false);
    }
  }, [fetcher, page, pageSize]);

  useEffect(() => {
    void reload();
  }, [reload]);

  function handlePageChange(nextPage: number) {
    setPage(nextPage);
    setLoading(true);
  }

  function handlePageSizeChange(nextSize: number) {
    setPageSize(nextSize);
    setPage(1);
    setLoading(true);
  }

  return {
    items,
    total,
    page,
    pageSize,
    loading,
    error,
    reload,
    handlePageChange,
    handlePageSizeChange,
  };
}
