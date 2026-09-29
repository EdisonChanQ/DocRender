import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { fetchCategories } from "../api/category";
import {
  createTemplate,
  deleteTemplate,
  fetchTemplates,
  updateTemplate,
} from "../api/template";
import { DataTable, type Column } from "../components/DataTable";
import { Pagination } from "../components/Pagination";
import { usePagedList } from "../hooks/usePagedList";
import type { Category, Template } from "../types";

interface FormState {
  category_id: number | null;
  name: string;
  is_enabled: boolean;
}

const EMPTY_FORM: FormState = { category_id: null, name: "", is_enabled: true };

function formatDateTime(value: string | null): string {
  if (!value) return "—";
  return value.replace("T", " ").slice(0, 19);
}

export default function TemplatePage() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<"list" | "create" | "edit">("list");
  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  // 分类下拉数据（量级小，一次拉取）
  const [categoryOptions, setCategoryOptions] = useState<Category[]>([]);
  // 列表筛选：null = 全部
  const [filterCategoryId, setFilterCategoryId] = useState<number | null>(null);

  useEffect(() => {
    fetchCategories(1, 200)
      .then((res) => setCategoryOptions(res.items))
      .catch(() => setCategoryOptions([]));
  }, []);

  const fetcher = useCallback(
    (page: number, pageSize: number) => fetchTemplates(page, pageSize, filterCategoryId),
    [filterCategoryId],
  );
  const {
    items: templates,
    total,
    page,
    pageSize,
    loading,
    error: listError,
    reload: loadTemplates,
    handlePageChange,
    handlePageSizeChange,
  } = usePagedList<Template>(fetcher, { initialPageSize: 10 });

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

  function openEdit(template: Template) {
    setForm({
      category_id: template.category_id,
      name: template.name,
      is_enabled: template.is_enabled,
    });
    setEditingId(template.id);
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
    if (form.category_id === null) {
      setFormError("请选择所属分类");
      return;
    }
    if (!name) {
      setFormError("请填写模板名称");
      return;
    }
    setSaving(true);
    setFormError(null);
    try {
      if (mode === "edit" && editingId !== null) {
        await updateTemplate(editingId, {
          category_id: form.category_id,
          name,
          is_enabled: form.is_enabled,
        });
        setNotice(`模板「${name}」已更新`);
        closeForm();
        await loadTemplates();
      } else {
        const created = await createTemplate({
          category_id: form.category_id,
          name,
          is_enabled: form.is_enabled,
        });
        setNotice(`模板已创建，编号 ${created.code}`);
        closeForm();
        await loadTemplates();
        // 新建后直接进入范本制作页
        if (window.confirm(`模板「${created.name}」已创建。是否现在上传文件并框选范本？`)) {
          navigate(`/templates/${created.id}/maker`);
        }
      }
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "保存模板失败");
    } finally {
      setSaving(false);
    }
  }

  async function handleToggle(template: Template) {
    setBusyId(template.id);
    setNotice(null);
    try {
      await updateTemplate(template.id, { is_enabled: !template.is_enabled });
      await loadTemplates();
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "更新状态失败");
    } finally {
      setBusyId(null);
    }
  }

  async function handleDelete(template: Template) {
    if (!window.confirm(`确定删除模板「${template.name}」（${template.code}）吗？`)) {
      return;
    }
    setBusyId(template.id);
    setNotice(null);
    try {
      await deleteTemplate(template.id);
      if (editingId === template.id) closeForm();
      await loadTemplates();
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "删除模板失败");
    } finally {
      setBusyId(null);
    }
  }

  function handleFilterChange(value: string) {
    setFilterCategoryId(value === "" ? null : Number(value));
  }

  const columns: Column<Template>[] = [
    { key: "code", header: "模板ID", cellClassName: "mono", render: (t) => t.code },
    {
      key: "category",
      header: "所属分类",
      render: (t) =>
        t.category_name ? (
          t.category_name
        ) : (
          <span className="status status--error" title="所属分类已被删除，需重新指定分类后才能启用">
            已失效
          </span>
        ),
    },
    { key: "name", header: "模板名称", render: (t) => t.name },
    {
      key: "spec",
      header: "图片规格",
      cellClassName: "nowrap",
      render: (t) =>
        t.width && t.height ? `${t.width} × ${t.height}px · ${t.dpi ?? "—"}dpi` : "—",
    },
    {
      key: "status",
      header: "状态",
      render: (t) => (
        <span className={`status status--${t.is_enabled ? "ok" : "muted"}`}>
          {t.is_enabled ? "启用" : "禁用"}
        </span>
      ),
    },
    { key: "created_by", header: "创建人", render: (t) => t.created_by || "—" },
    {
      key: "created_datetime",
      header: "创建时间",
      cellClassName: "nowrap",
      render: (t) => formatDateTime(t.created_datetime),
    },
    { key: "updated_by", header: "更新人", render: (t) => t.updated_by || "—" },
    {
      key: "updated_datetime",
      header: "更新时间",
      cellClassName: "nowrap",
      render: (t) => formatDateTime(t.updated_datetime),
    },
    {
      key: "actions",
      header: "操作",
      headerClassName: "data-table__actions",
      cellClassName: "data-table__actions",
      render: (t) => {
        // 所属分类失效时禁用「启用」操作：必须先改挂到有效分类
        const enableBlocked = !t.is_enabled && !t.category_name;
        return (
          <>
            <button
              type="button"
              className="row-action"
              onClick={() => void handleToggle(t)}
              disabled={busyId === t.id || enableBlocked}
              title={enableBlocked ? "所属分类已失效，请先编辑模板改挂到有效分类" : undefined}
            >
              {t.is_enabled ? "禁用" : "启用"}
            </button>
            <button
              type="button"
              className="row-action"
              onClick={() => openEdit(t)}
              disabled={busyId === t.id}
            >
              编辑
            </button>
            <button
              type="button"
              className="row-action"
              onClick={() => navigate(`/templates/${t.id}/maker`)}
              disabled={busyId === t.id}
              title="上传文件、框选模板范本"
            >
              范本
            </button>
            <button
              type="button"
              className="row-action"
              onClick={() => navigate(`/templates/${t.id}/fields`)}
              disabled={busyId === t.id}
              title="在范本图上标注文字字段块"
            >
              字段
            </button>
            <button
              type="button"
              className="row-action row-action--danger"
              onClick={() => void handleDelete(t)}
              disabled={busyId === t.id}
            >
              删除
            </button>
          </>
        );
      },
    },
  ];

  return (
    <div className="page">
      <header className="page__header">
        <h1>模板管理</h1>
        <p>模板基于分类创建，每个分类下可有多个模板；分类被删除后其模板自动失效，需改挂有效分类才能启用。</p>
      </header>

      {mode !== "create" && mode !== "edit" && (
        <section className="panel">
          <div className="panel__toolbar">
            <h2>模板列表</h2>
            <div className="panel__toolbar-actions">
              <label className="filter-select">
                分类筛选
                <select
                  value={filterCategoryId ?? ""}
                  onChange={(event) => handleFilterChange(event.target.value)}
                >
                  <option value="">全部分类</option>
                  {categoryOptions.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}（{c.code}）
                    </option>
                  ))}
                </select>
              </label>
              <span className="muted">共 {total} 个模板</span>
              <button type="button" onClick={() => void loadTemplates()}>
                刷新
              </button>
              <button type="button" className="primary primary--inline" onClick={openCreate}>
                添加模板
              </button>
            </div>
          </div>

          {listError && <p className="error">{listError}</p>}
          {notice && <p className="notice">{notice}</p>}

          <DataTable
            columns={columns}
            rows={templates}
            rowKey={(t) => t.id}
            loading={loading}
            loadingText="加载中…"
            emptyText="暂无模板，点击「添加模板」创建第一个模板。"
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
          <h2>{mode === "edit" ? "编辑模板" : "添加模板"}</h2>
          <form className="form" onSubmit={handleSubmit}>
            <div className="form__grid">
              <label className="form__field">
                <span>所属分类 *</span>
                <select
                  value={form.category_id ?? ""}
                  autoFocus
                  onChange={(event) =>
                    updateField("category_id", event.target.value === "" ? null : Number(event.target.value))
                  }
                >
                  <option value="" disabled>
                    请选择分类
                  </option>
                  {categoryOptions.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}（{c.code}）
                    </option>
                  ))}
                </select>
              </label>

              <label className="form__field">
                <span>模板名称</span>
                <input
                  value={form.name}
                  placeholder="请输入模板名称"
                  maxLength={128}
                  onChange={(event) => updateField("name", event.target.value)}
                />
              </label>

              <label className="form__field">
                <span>是否启用</span>
                <select
                  value={form.is_enabled ? "1" : "0"}
                  disabled={form.category_id === null}
                  title={form.category_id === null ? "请先选择有效分类" : undefined}
                  onChange={(event) => updateField("is_enabled", event.target.value === "1")}
                >
                  <option value="1">启用</option>
                  <option value="0">禁用</option>
                </select>
              </label>
            </div>

            {formError && <p className="error">{formError}</p>}

            <div className="form__actions">
              <button type="button" onClick={closeForm} disabled={saving}>
                取消
              </button>
              <button type="submit" className="primary" disabled={saving || form.category_id === null}>
                {saving ? "保存中…" : mode === "edit" ? "保存修改" : "创建模板"}
              </button>
            </div>
          </form>
        </section>
      )}
    </div>
  );
}
