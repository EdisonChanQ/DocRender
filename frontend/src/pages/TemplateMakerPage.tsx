import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import {
  getTemplateDetail,
  prepareTemplateFile,
  updateTemplate,
  type PreparedFile,
  type PreparedPage,
} from "../api/template";
import { CropPicker, type CropRect } from "../components/CropPicker";
import type { Template } from "../types";

interface SpecForm {
  dpi: string;
  width: string;
  height: string;
}

const EMPTY_SPEC: SpecForm = { dpi: "", width: "", height: "" };

function num(v: string): number | null {
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? Math.round(n) : null;
}

/**
 * 模板范本制作页（独立全屏工作台）：
 * 上传 PDF/图片 → 选页 → 缩放框选支票 → 自动填入规格（可手动修正）→ 保存落库。
 */
export default function TemplateMakerPage() {
  const { id } = useParams<{ id: string }>();
  const templateId = Number(id);
  const navigate = useNavigate();

  const [template, setTemplate] = useState<Template | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(true);

  const [preparing, setPreparing] = useState(false);
  const [prepError, setPrepError] = useState<string | null>(null);
  const [prepared, setPrepared] = useState<PreparedFile | null>(null);
  const [pageIndex, setPageIndex] = useState(0);
  const [crop, setCrop] = useState<CropRect | null>(null);
  const [spec, setSpec] = useState<SpecForm>(EMPTY_SPEC);
  const [refDataUrl, setRefDataUrl] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);
  // 把既有范本直接载入画布做二次框选（无需重新上传源文件）
  const [refAsPage, setRefAsPage] = useState<PreparedPage | null>(null);
  // 用户清除范本后待提交的删除标记（保存时 ref_image 传 null）
  const [refDeleted, setRefDeleted] = useState(false);
  const hadRefRef = useRef(false);
  // 倾斜修正：rotation（canvas 度，正=顺时针）+ 参考网格开关
  const [rotation, setRotation] = useState(0);
  const [grid, setGrid] = useState(false);
  const [rotatedUrl, setRotatedUrl] = useState<string | null>(null);
  const [rotatedSize, setRotatedSize] = useState<{ w: number; h: number } | null>(null);

  const basePage: PreparedPage | null =
    refAsPage ?? (prepared ? prepared.pages[pageIndex] : null);

  // 生成旋转后的显示图（rotation≈0 时直接用原图，省一次编码）
  useEffect(() => {
    if (!basePage) {
      setRotatedUrl(null);
      setRotatedSize(null);
      return;
    }
    if (Math.abs(rotation) < 0.05) {
      setRotatedUrl(null);
      setRotatedSize(null);
      return;
    }
    let cancelled = false;
    const img = new Image();
    img.onload = () => {
      const rad = (rotation * Math.PI) / 180;
      const cos = Math.abs(Math.cos(rad));
      const sin = Math.abs(Math.sin(rad));
      const w = img.naturalWidth;
      const h = img.naturalHeight;
      const nw = Math.round(w * cos + h * sin);
      const nh = Math.round(w * sin + h * cos);
      const canvas = document.createElement("canvas");
      canvas.width = nw;
      canvas.height = nh;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.fillStyle = "#fff";
      ctx.fillRect(0, 0, nw, nh);
      ctx.translate(nw / 2, nh / 2);
      ctx.rotate(rad);
      ctx.drawImage(img, -w / 2, -h / 2);
      if (!cancelled) {
        setRotatedUrl(canvas.toDataURL("image/jpeg", 0.9));
        setRotatedSize({ w: nw, h: nh });
      }
    };
    img.src = basePage.data_url;
    return () => {
      cancelled = true;
    };
  }, [basePage?.data_url, rotation]);

  // 供 CropPicker/applyCrop 使用的"视图页"：坐标空间 = 旋转后图像
  const page: PreparedPage | null = (() => {
    if (!basePage) return null;
    const ratio = basePage.full_w / basePage.preview_w;
    const pw = rotatedSize?.w ?? basePage.preview_w;
    const ph = rotatedSize?.h ?? basePage.preview_h;
    return {
      index: basePage.index,
      preview_w: pw,
      preview_h: ph,
      full_w: Math.round(pw * ratio),
      full_h: Math.round(ph * ratio),
      dpi: basePage.dpi,
      skew: basePage.skew,
      data_url: rotatedUrl ?? basePage.data_url,
    };
  })();

  // 首次加载模板详情：回显既有规格与范本
  useEffect(() => {
    if (!Number.isInteger(templateId) || templateId <= 0) {
      setLoadError("模板 ID 无效");
      setLoadingDetail(false);
      return;
    }
    let cancelled = false;
    getTemplateDetail(templateId)
      .then((t) => {
        if (cancelled) return;
        setTemplate(t);
        setSpec({
          dpi: t.dpi ? String(t.dpi) : "",
          width: t.width ? String(t.width) : "",
          height: t.height ? String(t.height) : "",
        });
        if (t.ref_image_b64) {
          setRefDataUrl(`data:image/png;base64,${t.ref_image_b64}`);
          hadRefRef.current = true;
        }
      })
      .catch((err) => {
        if (!cancelled) setLoadError(err instanceof Error ? err.message : "加载模板失败");
      })
      .finally(() => {
        if (!cancelled) setLoadingDetail(false);
      });
    return () => {
      cancelled = true;
    };
  }, [templateId]);

  /** 框选变化 → 换算物理尺寸填入规格 + canvas 裁出范本。
   *  c=null 表示"不框选"：整页即范本（上传后默认可直接提交），
   *  需要页中单块时再拖框裁选。 */
  function applyCrop(c: CropRect | null, p: PreparedPage | null) {
    setCrop(c);
    if (!p) return;
    const eff: CropRect = c ?? { x: 0, y: 0, w: p.preview_w, h: p.preview_h };
    setRefDeleted(false);
    const scale = p.full_w / p.preview_w;
    setSpec({
      dpi: String(p.dpi),
      width: String(Math.max(1, Math.round(eff.w * scale))),
      height: String(Math.max(1, Math.round(eff.h * scale))),
    });
    const img = new Image();
    img.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = eff.w;
      canvas.height = eff.h;
      const ctx = canvas.getContext("2d");
      if (!ctx) {
        setPrepError("浏览器不支持 Canvas，无法生成范本");
        return;
      }
      ctx.drawImage(img, eff.x, eff.y, eff.w, eff.h, 0, 0, eff.w, eff.h);
      setRefDataUrl(canvas.toDataURL("image/png"));
    };
    img.onerror = () => setPrepError("预览图加载失败，无法生成范本");
    img.src = p.data_url;
  }

  async function handleFile(file: File) {
    setPreparing(true);
    setPrepError(null);
    try {
      const res = await prepareTemplateFile(file);
      setPrepared(res);
      setRefAsPage(null);
      setPageIndex(0);
      // 自动倾斜修正：后端 skew（正=需逆时针摆正）→ canvas rotation 取反
      setRotation(-res.pages[0].skew);
      applyCrop(null, res.pages[0]); // 默认整页即范本，直接可保存
    } catch (err) {
      setPrepared(null);
      setPrepError(err instanceof Error ? err.message : "文件解析失败");
    } finally {
      setPreparing(false);
    }
  }

  function changePage(idx: number) {
    setPageIndex(idx);
    if (prepared) {
      setRotation(-prepared.pages[idx].skew);
      applyCrop(null, prepared.pages[idx]);
    }
  }

  // 旋转/换图后视图坐标空间已变：重置为"整页即范本"并重新生成
  const viewDataUrl = page?.data_url ?? null;
  useEffect(() => {
    if (page && viewDataUrl) applyCrop(null, page);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewDataUrl]);

  /** 点击范本缩略图：把既有范本载入画布，可二次框选精修 */
  function loadRefToCanvas() {
    if (!refDataUrl) return;
    const img = new Image();
    img.onload = () => {
      const w = num(spec.width) ?? img.naturalWidth;
      const h = num(spec.height) ?? img.naturalHeight;
      const d = num(spec.dpi) ?? 300;
      const refPage: PreparedPage = {
        index: 0,
        preview_w: img.naturalWidth,
        preview_h: img.naturalHeight,
        full_w: w,
        full_h: h,
        dpi: d,
        skew: 0,
        data_url: refDataUrl,
      };
      setRefAsPage(refPage);
      setPrepared(null);
      setRotation(0); // 范本已是摆正后的图，载入时归零避免二次旋转
      applyCrop(null, refPage); // 默认整块即范本（载入后直接可再保存），框选才裁块
    };
    img.src = refDataUrl;
  }

  function clearRef() {
    setRefDataUrl(null);
    setRefAsPage(null);
    setCrop(null);
    setRefDeleted(hadRefRef.current);
  }

  async function handleSave() {
    // 清除待删除：提交 ref_image=null（尺寸一并清空由后端置 null 处理）
    if (!refDataUrl) {
      if (!refDeleted) {
        setSaveMsg("请先框选并生成范本，或点击范本载入画布调整");
        return;
      }
      setSaving(true);
      setSaveMsg(null);
      try {
        await updateTemplate(templateId, { ref_image: null, dpi: null, width: null, height: null });
        navigate("/templates");
      } catch (err) {
        setSaveMsg(err instanceof Error ? err.message : "删除范本失败");
      } finally {
        setSaving(false);
      }
      return;
    }
    const w = num(spec.width);
    const h = num(spec.height);
    const d = num(spec.dpi);
    if (!w || !h || !d) {
      setSaveMsg("dpi / 宽 / 高 必须是正整数");
      return;
    }
    setSaving(true);
    setSaveMsg(null);
    try {
      await updateTemplate(templateId, {
        dpi: d,
        width: w,
        height: h,
        ref_image: refDataUrl,
      });
      navigate("/templates");
    } catch (err) {
      setSaveMsg(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  if (loadError) {
    return (
      <div className="page">
        <header className="page__header">
          <h1>模板范本制作</h1>
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
        <h1>模板范本制作</h1>
        {template && (
          <span className="maker-page__tpl">
            {template.code} · {template.name}
            {template.category_name ? ` · ${template.category_name}` : ""}
          </span>
        )}
        <div className="maker-page__header-actions">
          <button
            type="button"
            className={`primary primary--inline${refDeleted && !refDataUrl ? " primary--danger" : ""}`}
            onClick={() => void handleSave()}
            disabled={saving || (!refDataUrl && !refDeleted)}
            title={!refDataUrl && !refDeleted ? "尚未框选/生成范本" : refDeleted ? "确认删除范本并保存" : undefined}
          >
            {saving ? "保存中…" : refDeleted && !refDataUrl ? "确认删除范本" : "保存范本"}
          </button>
        </div>
      </header>

      {saveMsg && <p className="error">{saveMsg}</p>}

      <div className="maker-page__body">
        <section className="panel maker-page__canvas">
          <div className="panel__toolbar">
            <h2>页预览与框选</h2>
            <div className="panel__toolbar-actions">
              <label className="btn btn--file">
                {preparing ? "解析中…" : prepared ? "重新上传文件" : "上传模板文件（PDF/图片）"}
                <input
                  type="file"
                  accept=".pdf,image/*"
                  hidden
                  disabled={preparing}
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) void handleFile(f);
                    e.target.value = "";
                  }}
                />
              </label>
              {prepared && (
                <span className="muted">
                  {prepared.kind === "pdf" ? `PDF · ${prepared.page_count} 页` : "图片 · 单页"}
                </span>
              )}
            </div>
          </div>

          {prepError && <p className="error">{prepError}</p>}

          {prepared && prepared.page_count > 1 && (
            <div className="tpl-maker__pages">
              {prepared.pages.map((p) => (
                <button
                  key={p.index}
                  type="button"
                  className={`page-thumb${p.index === pageIndex ? " page-thumb--active" : ""}`}
                  onClick={() => changePage(p.index)}
                  title={`第 ${p.index + 1} 页`}
                >
                  <img src={p.data_url} alt={`第 ${p.index + 1} 页`} loading="lazy" />
                  <span>{p.index + 1}</span>
                </button>
              ))}
            </div>
          )}

          {page ? (
            <CropPicker
              imageUrl={page.data_url}
              imgWidth={page.preview_w}
              imgHeight={page.preview_h}
              crop={crop}
              onCropChange={(c) => applyCrop(c, page)}
              maxHeight={760}
              grid={grid}
              resetKey={
                prepared
                  ? `p-${prepared.kind}-${pageIndex}`
                  : refAsPage
                    ? "ref"
                    : "none"
              }
            />
          ) : (
            <div
              className="upload-dropzone"
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault();
                const f = e.dataTransfer.files?.[0];
                if (f) void handleFile(f);
              }}
            >
              {loadingDetail
                ? "加载模板信息…"
                : "上传 PDF 或图片开始制作：拖拽到此处，或点击右上角「上传模板文件」"}
            </div>
          )}
        </section>

        <aside className="panel maker-page__side">
          <h2>倾斜修正</h2>
          <div className="rotate-controls">
            <div className="rotate-row">
              <button
                type="button"
                onClick={() => setRotation((r) => Math.max(-15, +(r - 0.5).toFixed(2)))}
                title="逆时针 0.5°"
              >
                ↺0.5
              </button>
              <input
                type="range"
                min={-15}
                max={15}
                step={0.1}
                value={rotation}
                onChange={(e) => setRotation(Number(e.target.value))}
              />
              <button
                type="button"
                onClick={() => setRotation((r) => Math.min(15, +(r + 0.5).toFixed(2)))}
                title="顺时针 0.5°"
              >
                ↻0.5
              </button>
            </div>
            <div className="rotate-row">
              <span className="muted">角度</span>
              <input
                className="rotate-input"
                type="number"
                min={-15}
                max={15}
                step={0.1}
                value={rotation}
                onChange={(e) => setRotation(Math.max(-15, Math.min(15, Number(e.target.value) || 0)))}
              />
              <span className="muted">°（正=顺时针）</span>
              <label className="filter-select">
                <input type="checkbox" checked={grid} onChange={(e) => setGrid(e.target.checked)} />
                参考网格
              </label>
            </div>
            <div className="rotate-row">
              {basePage && basePage.skew !== 0 && (
                <button type="button" className="crop-picker__clear" onClick={() => setRotation(-basePage.skew)}>
                  用自动检测角 {-basePage.skew}°
                </button>
              )}
              <button type="button" className="crop-picker__clear" onClick={() => setRotation(0)}>
                归零
              </button>
            </div>
            {basePage && basePage.skew !== 0 && (
              <p className="page__hint">自动检测到倾斜 {basePage.skew}°，已应用；可微调或开网格对照横线。</p>
            )}
          </div>

          <h2>模板规格</h2>
          <p className="page__hint">
            框选后自动填入（按估算扫描 dpi 换算的物理像素），可手动修正。
          </p>

          <label className="form__field">
            <span>模板宽度（px）*</span>
            <input
              value={spec.width}
              inputMode="numeric"
              placeholder="如 1360"
              onChange={(e) => setSpec((s) => ({ ...s, width: e.target.value }))}
            />
          </label>
          <label className="form__field">
            <span>模板高度（px）*</span>
            <input
              value={spec.height}
              inputMode="numeric"
              placeholder="如 370"
              onChange={(e) => setSpec((s) => ({ ...s, height: e.target.value }))}
            />
          </label>
          <label className="form__field">
            <span>DPI *</span>
            <input
              value={spec.dpi}
              inputMode="numeric"
              placeholder="如 300"
              onChange={(e) => setSpec((s) => ({ ...s, dpi: e.target.value }))}
            />
          </label>

          {page && (
            <p className="page__hint">
              {crop
                ? `已框选块 ${crop.w}×${crop.h}px @ ${page.dpi}dpi；点「清除框选」恢复整页即范本`
                : `未框选：整页即范本（${page.full_w}×${page.full_h}px @ ${page.dpi}dpi），可直接保存；需裁页中单块再拖框`}
            </p>
          )}

          <h2 className="maker-page__side-title">范本预览</h2>
          {refDataUrl ? (
            <>
              <img
                className="maker-page__ref maker-page__ref--clickable"
                src={refDataUrl}
                alt="模板范本"
                title="点击载入画布二次框选"
                onClick={loadRefToCanvas}
              />
              <div className="maker-page__ref-actions">
                <button type="button" className="crop-picker__clear" onClick={loadRefToCanvas}>
                  载入画布调整
                </button>
                <button type="button" className="crop-picker__clear crop-picker__clear--danger" onClick={clearRef}>
                  清除范本
                </button>
              </div>
              {refDeleted && (
                <p className="page__hint">已清除，点击右上「确认删除范本」提交删除。</p>
              )}
            </>
          ) : (
            <p className="muted">{refDeleted ? "范本已清除，保存后生效删除" : "尚未生成范本"}</p>
          )}
        </aside>
      </div>
    </div>
  );
}
