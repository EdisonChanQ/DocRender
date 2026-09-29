import { useCallback, useState } from "react";

import {
  createCategory,
  deleteCategory,
  fetchCategories,
  updateCategory,
} from "../api/category";
import { Pagination } from "../components/Pagination";
import { DataTable, type Column } from "../components/DataTable";
import { usePagedList } from "../hooks/usePagedList";
import type { Category } from "../types";

interface FormState {
  name: string;
  sort_order: number;
  is_enabled: boolean;
  remark: string;
}

const EMPTY_FORM: FormState = {
  name: "",
  sort_order: 0,
  is_enabled: true,
  remark: "",
};

function formatDateTime(value: string | null): string {
  if (!value) return "—";
  return value.replace("T", " ").slice(0, 19);
}

export default function CategoryPage() {
  const [mode, setMode] = useState<"list" | "create" | "edit">("list");
  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  const fetcher = useCallback(
    (page: number, pageSize: number) => fetchCategories(page, pageSize),
    [],
  );
  const {
    items: categories,
    total,
    page,
    pageSize,
    loading,
    error: listError,
    reload: loadCategories,
    handlePageChange,
    handlePageSizeChange,
  } = usePagedList<Category>(fetcher, { initialPageSize: 10 });

  function updateField<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
    setFormError(null);
  }

  function openCreate() {
    setForm(EMPTY_FORM);
    setEditingId(null);
    setFormError(null);
    setNotice(null);
    setMode("create");
  }

  function openEdit(category: Category) {
    setForm({
      name: category.name,
      sort_order: category.sort_order,
      is_enabled: category.is_enabled,
      remark: category.remark ?? "",
    });
    setEditingId(category.id);
    setFormError(null);
    setNotice(null);
    setMode("edit");
  }

  function closeForm() {
    setMode("list");
    setEditingId(null);
    setForm(EMPTY_FORM);
    setFormError(null);
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    const name = form.name.trim();
    if (!name) {
      setFormError("请填写分类名称");
      return;
    }
    setSaving(true);
    setFormError(null);
    try {
      const remark = form.remark.trim() || null;
      if (mode === "edit" && editingId !== null) {
        await updateCategory(editingId, {
          name,
          sort_order: form.sort_order,
          is_enabled: form.is_enabled,
          remark,
        });
        setNotice(`分类「${name}」已更新`);
      } else {
        const created = await createCategory({
          name,
          sort_order: form.sort_order,
          is_enabled: form.is_enabled,
          remark,
        });
        setNotice(`分类已创建，编号 ${created.code}`);
      }
      closeForm();
      await loadCategories();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "保存分类失败");
    } finally {
      setSaving(false);
    }
  }

  async function handleToggle(category: Category) {
    setBusyId(category.id);
    try {
      await updateCategory(category.id, { is_enabled: !category.is_enabled });
      await loadCategories();
    } catch (err) {
      setNotice(null);
      window.alert(err instanceof Error ? err.message : "更新状态失败");
    } finally {
      setBusyId(null);
    }
  }

  async function handleDelete(category: Category) {
    if (!window.confirm(`确定删除分类「${category.name}」（${category.code}）吗？`)) {
      return;
    }
    setBusyId(category.id);
    try {
      await deleteCategory(category.id);
      if (editingId === category.id) closeForm();
      await loadCategories();
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "删除分类失败");
    } finally {
      setBusyId(null);
    }
  }

  const columns: Column<Category>[] = [
    { key: "code", header: "分类编号", cellClassName: "mono", render: (c) => c.code },
    { key: "name", header: "分类名称", render: (c) => c.name },
    { key: "sort_order", header: "排序", render: (c) => c.sort_order },
    {
      key: "status",
      header: "状态",
      render: (c) => (
        <span className={`status status--${c.is_enabled ? "ok" : "muted"}`}>
          {c.is_enabled ? "启用" : "禁用"}
        </span>
      ),
    },
    { key: "remark", header: "备注", cellClassName: "wrap", render: (c) => c.remark || "—" },
    { key: "created_by", header: "创建人", render: (c) => c.created_by || "—" },
    {
      key: "created_datetime",
      header: "创建时间",
      cellClassName: "nowrap",
      render: (c) => formatDateTime(c.created_datetime),
    },
    { key: "updated_by", header: "更新人", render: (c) => c.updated_by || "—" },
    {
      key: "updated_datetime",
      header: "更新时间",
      cellClassName: "nowrap",
      render: (c) => formatDateTime(c.updated_datetime),
    },
    {
      key: "actions",
      header: "操作",
      headerClassName: "data-table__actions",
      cellClassName: "data-table__actions",
      render: (c) => (
        <>
          <button
            type="button"
            className="row-action"
            onClick={() => void handleToggle(c)}
            disabled={busyId === c.id}
          >
            {c.is_enabled ? "禁用" : "启用"}
          </button>
          <button
            type="button"
            className="row-action"
            onClick={() => openEdit(c)}
            disabled={busyId === c.id}
          >
            编辑
          </button>
          <button
            type="button"
            className="row-action row-action--danger"
            onClick={() => void handleDelete(c)}
            disabled={busyId === c.id}
          >
            删除
          </button>
        </>
      ),
    },
  ];

  return (
    <div className="page">
      <header className="page__header">
        <h1>分类管理</h1>
        <p>维护解析文本所使用的分类信息，分类编号（CAT + 年月 + 序号）自动生成。</p>
      </header>

      {mode !== "create" && mode !== "edit" && (
        <section className="panel">
          <div className="panel__toolbar">
            <h2>分类列表</h2>
            <div className="panel__toolbar-actions">
              <span className="muted">共 {total} 个分类</span>
              <button type="button" onClick={() => void loadCategories()}>
                刷新
              </button>
              <button type="button" className="primary primary--inline" onClick={openCreate}>
                添加分类
              </button>
            </div>
          </div>

          {listError && <p className="error">{listError}</p>}
          {notice && <p className="notice">{notice}</p>}

          <DataTable
            columns={columns}
            rows={categories}
            rowKey={(c) => c.id}
            loading={loading}
            loadingText="加载中…"
            emptyText="暂无分类，点击「添加分类」创建第一个分类。"
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
      )}

      {(mode === "create" || mode === "edit") && (
        <section className="panel">
          <h2>{mode === "edit" ? "编辑分类" : "添加分类"}</h2>
          <form className="form" onSubmit={handleSubmit}>
            <div className="form__grid">
              <label className="form__field">
                <span>分类名称</span>
                <input
                  value={form.name}
                  placeholder="请输入分类名称"
                  maxLength={128}
                  autoFocus
                  onChange={(event) => updateField("name", event.target.value)}
                />
              </label>

              <label className="form__field">
                <span>排序值</span>
                <input
                  type="number"
                  min={0}
                  value={form.sort_order}
                  onChange={(event) =>
                    updateField(
                      "sort_order",
                      event.target.value === "" ? 0 : Math.max(0, Number(event.target.value)),
                    )
                  }
                />
              </label>

              <label className="form__field">
                <span>是否启用</span>
                <select
                  value={form.is_enabled ? "1" : "0"}
                  onChange={(event) => updateField("is_enabled", event.target.value === "1")}
                >
                  <option value="1">启用</option>
                  <option value="0">禁用</option>
                </select>
              </label>

              <label className="form__field form__field--full">
                <span>备注</span>
                <textarea
                  value={form.remark}
                  placeholder="可选，用于说明该分类的用途"
                  maxLength={512}
                  rows={3}
                  onChange={(event) => updateField("remark", event.target.value)}
                />
              </label>
            </div>

            {formError && <p className="error">{formError}</p>}

            <div className="form__actions">
              <button type="button" onClick={closeForm} disabled={saving}>
                取消
              </button>
              <button type="submit" className="primary" disabled={saving}>
                {saving ? "保存中…" : mode === "edit" ? "保存修改" : "创建分类"}
              </button>
            </div>
          </form>
        </section>
      )}
    </div>
  );
}
