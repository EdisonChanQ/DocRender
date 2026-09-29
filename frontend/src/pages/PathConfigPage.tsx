import { useCallback, useState } from "react";

import {
  createPathConfig,
  deletePathConfig,
  fetchPathConfigs,
  updatePathConfig,
} from "../api/pathConfig";
import { DataTable, type Column } from "../components/DataTable";
import { Pagination } from "../components/Pagination";
import { usePagedList } from "../hooks/usePagedList";
import type { PathConfig } from "../types";

interface FormState {
  code: string;
  name: string;
  path: string;
  is_enabled: boolean;
  remark: string;
}

const EMPTY_FORM: FormState = {
  code: "",
  name: "",
  path: "",
  is_enabled: true,
  remark: "",
};

const CODE_RE = /^[A-Za-z0-9_\-]{1,64}$/;

function formatDateTime(value: string | null): string {
  if (!value) return "—";
  return value.replace("T", " ").slice(0, 19);
}

export default function PathConfigPage() {
  const [mode, setMode] = useState<"list" | "create" | "edit">("list");
  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [keywordInput, setKeywordInput] = useState("");
  const [keyword, setKeyword] = useState<string | null>(null);

  const fetcher = useCallback(
    (page: number, pageSize: number) => fetchPathConfigs(page, pageSize, keyword),
    [keyword],
  );
  const {
    items: configs,
    total,
    page,
    pageSize,
    loading,
    error: listError,
    reload: loadConfigs,
    handlePageChange,
    handlePageSizeChange,
  } = usePagedList<PathConfig>(fetcher, { initialPageSize: 10 });

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

  function openEdit(config: PathConfig) {
    setForm({
      code: config.code,
      name: config.name,
      path: config.path,
      is_enabled: config.is_enabled,
      remark: config.remark ?? "",
    });
    setEditingId(config.id);
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
    const code = form.code.trim();
    const name = form.name.trim();
    const path = form.path.trim();
    if (mode === "create") {
      if (!code) {
        setFormError("请填写路径编码");
        return;
      }
      if (!CODE_RE.test(code)) {
        setFormError("路径编码仅允许字母、数字、下划线和中划线（1-64 位）");
        return;
      }
    }
    if (!name) {
      setFormError("请填写路径名称");
      return;
    }
    if (!path) {
      setFormError("请填写存放路径");
      return;
    }
    setSaving(true);
    setFormError(null);
    try {
      const remark = form.remark.trim() || null;
      if (mode === "edit" && editingId !== null) {
        await updatePathConfig(editingId, {
          name,
          path,
          is_enabled: form.is_enabled,
          remark,
        });
        setNotice(`路径配置「${code}」已更新`);
      } else {
        await createPathConfig({ code, name, path, is_enabled: form.is_enabled, remark });
        setNotice(`路径配置「${code}」已创建`);
      }
      closeForm();
      await loadConfigs();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "保存路径配置失败");
    } finally {
      setSaving(false);
    }
  }

  async function handleToggle(config: PathConfig) {
    setBusyId(config.id);
    setNotice(null);
    try {
      await updatePathConfig(config.id, { is_enabled: !config.is_enabled });
      await loadConfigs();
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "更新状态失败");
    } finally {
      setBusyId(null);
    }
  }

  async function handleDelete(config: PathConfig) {
    if (!window.confirm(`确定删除路径配置「${config.name}」（${config.code}）吗？`)) {
      return;
    }
    setBusyId(config.id);
    setNotice(null);
    try {
      await deletePathConfig(config.id);
      if (editingId === config.id) closeForm();
      await loadConfigs();
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "删除失败");
    } finally {
      setBusyId(null);
    }
  }

  function applyKeyword() {
    setKeyword(keywordInput.trim() || null);
  }

  const columns: Column<PathConfig>[] = [
    { key: "code", header: "路径编码", cellClassName: "mono", render: (c) => c.code },
    { key: "name", header: "名称", render: (c) => c.name },
    { key: "path", header: "存放路径", cellClassName: "wrap", render: (c) => c.path },
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
        <h1>路径管理</h1>
        <p>维护项目文件存放的共享路径（上传、导出、模板等目录），路径编码创建后不可修改。</p>
      </header>

      {mode !== "create" && mode !== "edit" && (
        <section className="panel">
          <div className="panel__toolbar">
            <h2>路径列表</h2>
            <div className="panel__toolbar-actions">
              <label className="filter-select">
                搜索
                <input
                  value={keywordInput}
                  placeholder="编码 / 名称 / 路径"
                  style={{ width: 180, padding: "4px 8px", fontSize: 13 }}
                  onChange={(e) => setKeywordInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      applyKeyword();
                    }
                  }}
                />
              </label>
              <button type="button" onClick={applyKeyword}>
                查询
              </button>
              {keyword && (
                <button
                  type="button"
                  onClick={() => {
                    setKeywordInput("");
                    setKeyword(null);
                  }}
                >
                  清除
                </button>
              )}
              <span className="muted">共 {total} 条</span>
              <button type="button" onClick={() => void loadConfigs()}>
                刷新
              </button>
              <button type="button" className="primary primary--inline" onClick={openCreate}>
                添加路径
              </button>
            </div>
          </div>

          {listError && <p className="error">{listError}</p>}
          {notice && <p className="notice">{notice}</p>}

          <DataTable
            columns={columns}
            rows={configs}
            rowKey={(c) => c.id}
            loading={loading}
            loadingText="加载中…"
            emptyText="暂无路径配置，点击「添加路径」创建第一条。"
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
          <h2>{mode === "edit" ? "编辑路径配置" : "添加路径配置"}</h2>
          <form className="form" onSubmit={handleSubmit}>
            <div className="form__grid">
              <label className="form__field">
                <span>路径编码 *</span>
                <input
                  value={form.code}
                  placeholder="如 upload、export、template"
                  maxLength={64}
                  autoFocus
                  disabled={mode === "edit"}
                  title={mode === "edit" ? "路径编码创建后不可修改" : undefined}
                  onChange={(event) => updateField("code", event.target.value)}
                />
              </label>

              <label className="form__field">
                <span>名称</span>
                <input
                  value={form.name}
                  placeholder="请输入路径显示名称"
                  maxLength={128}
                  onChange={(event) => updateField("name", event.target.value)}
                />
              </label>

              <label className="form__field form__field--full">
                <span>存放路径 *</span>
                <input
                  value={form.path}
                  placeholder="UNC 共享路径（\\server\share）或本地/映射盘路径"
                  maxLength={1024}
                  onChange={(event) => updateField("path", event.target.value)}
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
                  placeholder="可选，用于说明该路径的用途"
                  maxLength={512}
                  rows={2}
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
                {saving ? "保存中…" : mode === "edit" ? "保存修改" : "创建路径"}
              </button>
            </div>
          </form>
        </section>
      )}
    </div>
  );
}
