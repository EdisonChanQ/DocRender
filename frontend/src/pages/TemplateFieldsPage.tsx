import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { getTemplateDetail } from "../api/template";
import {
  createField,
  deleteField,
  fetchFields,
  updateField,
  type FieldType,
  type TemplateField,
} from "../api/templateField";
import { CropPicker, type CropRect, type OverlayRect } from "../components/CropPicker";
import type { Template } from "../types";

interface FieldForm {
  field_key: string;
  label: string;
  field_type: FieldType;
  pad: number;
}

const EMPTY_FORM: FieldForm = { field_key: "", label: "", field_type: "text", pad: 0 };

const KEY_RE = /^[A-Za-z][A-Za-z0-9_]{0,63}$/;

/**
 * 模板字段标注页：在范本图上框选文字目标块，命名后保存。
 * 字段坐标以范本图像素为准，供后续 OCR 按字段输出 JSON。
 */
export default function TemplateFieldsPage() {
  const { id } = useParams<{ id: string }>();
  const templateId = Number(id);
  const navigate = useNavigate();

  const [template, setTemplate] = useState<Template | null>(null);
  const [refUrl, setRefUrl] = useState<string | null>(null);
  const [refSize, setRefSize] = useState<{ w: number; h: number } | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [fields, setFields] = useState<TemplateField[]>([]);
  const [listLoading, setListLoading] = useState(true);

  const [crop, setCrop] = useState<CropRect | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null); // null=新建
  const [form, setForm] = useState<FieldForm>(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [showAll, setShowAll] = useState(false);

  const loadAll = useCallback(async () => {
    try {
      const [detail, list] = await Promise.all([
        getTemplateDetail(templateId),
        fetchFields(templateId),
      ]);
      setTemplate(detail);
      setFields(list);
      if (detail.ref_image_b64) {
        const url = `data:image/png;base64,${detail.ref_image_b64}`;
        setRefUrl(url);
        const img = new Image();
        img.onload = () => setRefSize({ w: img.naturalWidth, h: img.naturalHeight });
        img.src = url;
      }
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setListLoading(false);
    }
  }, [templateId]);

  useEffect(() => {
    if (!Number.isInteger(templateId) || templateId <= 0) {
      setLoadError("模板 ID 无效");
      setListLoading(false);
      return;
    }
    void loadAll();
  }, [templateId, loadAll]);

  function startCreate(c: CropRect | null) {
    setCrop(c);
    setEditingId(null);
    setForm(EMPTY_FORM);
    setFormError(null);
  }

  function selectField(f: TemplateField) {
    setCrop({ x: f.x, y: f.y, w: f.width, h: f.height });
    setEditingId(f.id);
    setForm({ field_key: f.field_key, label: f.label, field_type: f.field_type ?? "text", pad: f.pad });
    setFormError(null);
  }

  /** 从范本图裁出指定块，返回 base64 PNG（data_url）；失败返回 null（不阻塞保存） */
  function cropRefImage(c: CropRect): Promise<string | null> {
    return new Promise((resolve) => {
      if (!refUrl) {
        resolve(null);
        return;
      }
      const img = new Image();
      img.onload = () => {
        try {
          const canvas = document.createElement("canvas");
          canvas.width = c.w;
          canvas.height = c.h;
          const ctx = canvas.getContext("2d");
          if (!ctx) {
            resolve(null);
            return;
          }
          ctx.drawImage(img, c.x, c.y, c.w, c.h, 0, 0, c.w, c.h);
          resolve(canvas.toDataURL("image/png"));
        } catch {
          resolve(null);
        }
      };
      img.onerror = () => resolve(null);
      img.src = refUrl;
    });
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!crop) {
      setFormError("请先在范本图上框选字段区域");
      return;
    }
    const key = form.field_key.trim();
    const label = form.label.trim();
    if (!KEY_RE.test(key)) {
      setFormError("字段命名需字母开头，仅含字母/数字/下划线（如 payee、amount_upper）");
      return;
    }
    if (!label) {
      setFormError("请填写字段说明");
      return;
    }
    setSaving(true);
    setFormError(null);
    try {
      // 从范本图裁出字段块样本（OCR 按块匹配的底片）
      const refImage = await cropRefImage(crop);
      if (editingId === null) {
        await createField(templateId, {
          field_key: key,
          label,
          field_type: form.field_type,
          x: crop.x,
          y: crop.y,
          width: crop.w,
          height: crop.h,
          pad: form.pad,
          sort_order: fields.length,
          ref_image: refImage,
        });
      } else {
        await updateField(templateId, editingId, {
          field_key: key,
          label,
          field_type: form.field_type,
          x: crop.x,
          y: crop.y,
          width: crop.w,
          height: crop.h,
          pad: form.pad,
          ref_image: refImage,
        });
      }
      await loadAll();
      startCreate(null);
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "保存字段失败");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(f: TemplateField) {
    if (!window.confirm(`确定删除字段「${f.label}（${f.field_key}）」吗？`)) return;
    setBusyId(f.id);
    try {
      await deleteField(templateId, f.id);
      if (editingId === f.id) startCreate(null);
      await loadAll();
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "删除失败");
    } finally {
      setBusyId(null);
    }
  }

  // 默认只显示当前选中/编辑的框（作为 crop）；勾选"全部显示"才叠加其余字段，
  // 且叠加框为纯展示（interactive:false），不拦截拖拽，避免相邻框干扰调整宽高。
  const overlays: OverlayRect[] = (showAll ? fields : [])
    .filter((f) => f.id !== editingId)
    .map((f) => ({
      x: f.x,
      y: f.y,
      w: f.width,
      h: f.height,
      label: `${f.label} ${f.field_key}`,
      color: "#94a3b8",
      interactive: false,
    }));

  if (loadError) {
    return (
      <div className="page">
        <header className="page__header">
          <h1>模板字段标注</h1>
          <p className="error">{loadError}</p>
        </header>
        <button type="button" onClick={() => navigate("/templates")}>
          返回模板管理
        </button>
      </div>
    );
  }

  return (
    <div className="maker-page">
      <header className="maker-page__header">
        <button type="button" onClick={() => navigate("/templates")}>
          ← 返回
        </button>
        <h1>模板字段标注</h1>
        {template && (
          <span className="maker-page__tpl">
            {template.code} · {template.name}
            {template.width && template.height ? ` · ${template.width}×${template.height}px @ ${template.dpi ?? "?"}dpi` : ""}
          </span>
        )}
      </header>

      <div className="maker-page__body">
        <section className="panel maker-page__canvas">
          <div className="panel__toolbar">
            <h2>范本图框选</h2>
            <div className="panel__toolbar-actions">
              <label className="filter-select">
                <input
                  type="checkbox"
                  checked={showAll}
                  onChange={(e) => setShowAll(e.target.checked)}
                />
                全部显示
              </label>
              <span className="muted">
                {refSize ? `范本 ${refSize.w}×${refSize.h}px · 已标注 ${fields.length} 个字段` : ""}
              </span>
            </div>
          </div>

          {!refUrl ? (
            <div className="upload-dropzone">
              {listLoading
                ? "加载中…"
                : "该模板尚未上传范本。请先在「范本」制作页完成上传与框选。"}
              <div style={{ marginTop: 10 }}>
                <button type="button" onClick={() => navigate(`/templates/${templateId}/maker`)}>
                  去制作范本
                </button>
              </div>
            </div>
          ) : (
            <CropPicker
              imageUrl={refUrl}
              imgWidth={refSize?.w ?? 1}
              imgHeight={refSize?.h ?? 1}
              crop={crop}
              onCropChange={(c) => {
                // 编辑态下重画/调手柄 = 更新当前编辑字段的坐标（点"保存修改"生效）
                setCrop(c);
              }}
              overlays={overlays}
              maxHeight={620}
              hint="在范本图上拖拽框选一个文字区域；选中字段后用框四周手柄调整"
            />
          )}
        </section>

        <aside className="panel maker-page__side">
          <h2>{editingId === null ? "新增字段" : "编辑字段"}</h2>
          <form className="form" onSubmit={handleSubmit}>
            <label className="form__field">
              <span>字段命名（JSON 键）*</span>
              <input
                value={form.field_key}
                placeholder="如 payee / amount_upper / date"
                maxLength={64}
                disabled={!refUrl}
                onChange={(e) => {
                  setForm((f) => ({ ...f, field_key: e.target.value }));
                  setFormError(null);
                }}
              />
            </label>
            <label className="form__field">
              <span>字段说明 *</span>
              <input
                value={form.label}
                placeholder="如 收款人、金额大写"
                maxLength={128}
                disabled={!refUrl}
                onChange={(e) => {
                  setForm((f) => ({ ...f, label: e.target.value }));
                  setFormError(null);
                }}
              />
            </label>
            <label className="form__field">
              <span>块类型（提取工具）*</span>
              <select
                value={form.field_type}
                disabled={!refUrl}
                onChange={(e) => {
                  setForm((f) => ({ ...f, field_type: e.target.value as FieldType }));
                  setFormError(null);
                }}
              >
                <option value="text">文字（OCR）</option>
                <option value="qr">二维码（QR）</option>
              </select>
            </label>
            <label className="form__field">
              <span>外扩像素（pad）</span>
              <input
                type="number"
                min={0}
                max={200}
                value={form.pad}
                disabled={!refUrl}
                onChange={(e) =>
                  setForm((f) => ({
                    ...f,
                    pad: Math.max(0, Math.min(200, Number(e.target.value) || 0)),
                  }))
                }
              />
            </label>

            <div className="field-coords">
              <span className="muted">坐标（范本像素）：</span>
              {crop ? `x=${crop.x} y=${crop.y} w=${crop.w} h=${crop.h}` : "未框选"}
            </div>

            {formError && <p className="error">{formError}</p>}

            <div className="form__actions">
              {editingId !== null && (
                <button type="button" onClick={() => startCreate(null)} disabled={saving}>
                  取消编辑
                </button>
              )}
              <button
                type="submit"
                className="primary"
                disabled={saving || !refUrl || !crop}
              >
                {saving ? "保存中…" : editingId === null ? "添加字段" : "保存修改"}
              </button>
            </div>
          </form>

          <h2 className="maker-page__side-title">字段列表（{fields.length}）</h2>
          <div className="field-list">
            {fields.length === 0 && <p className="muted">尚无字段，先在左侧范本图上框选。</p>}
            {fields.map((f) => (
              <div
                key={f.id}
                className={`field-item${f.id === editingId ? " field-item--active" : ""}`}
              >
                <button
                  type="button"
                  className="field-item__main"
                  onClick={() => selectField(f)}
                  title="点击在图上定位并编辑"
                >
                  <span className="field-item__label">
                    {f.label}
                    <span
                      className={`status status--${f.field_type === "qr" ? "info" : "muted"}`}
                      style={{ marginLeft: 6 }}
                      title={f.field_type === "qr" ? "二维码解码" : "OCR 文字识别"}
                    >
                      {f.field_type === "qr" ? "QR" : "文字"}
                    </span>
                  </span>
                  <span className="field-item__key mono">{f.field_key}</span>
                  <span className="field-item__pos muted">
                    {f.x},{f.y} {f.width}×{f.height}
                    {f.pad ? ` +${f.pad}` : ""}
                  </span>
                </button>
                <button
                  type="button"
                  className="row-action row-action--danger"
                  onClick={() => void handleDelete(f)}
                  disabled={busyId === f.id}
                >
                  删除
                </button>
              </div>
            ))}
          </div>
        </aside>
      </div>
    </div>
  );
}
