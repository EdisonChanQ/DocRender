import { useEffect, useRef, useState } from "react";

import { fetchCategories } from "../api/category";
import {
  claimJob,
  claimPage,
  completePage,
  failJob,
  jobResult,
  registerPages,
  type FieldExtract,
} from "../api/filePage";
import { registerFileJob } from "../api/fileJob";
import {
  fetchTemplates,
  prepareTemplateFile,
  type PreparedFile,
  type PreparedPage,
} from "../api/template";
import { fetchFields, type TemplateField } from "../api/templateField";
import { llmToText, ocrToText, qrDecodeOne } from "../api/tools";
import type { Category, Template } from "../types";

/**
 * 流程测试页：浏览器扮演后端服务实例，真实走一遍「注册→抢占→分页→切片登记→
 * 页提取(OCR/QR)→双写回写→反查 JSON」整条链（真实落库、真实租约状态机、真实推理）。
 * 与生产流水线的差异（模拟部分）：
 * - 模板匹配不做：切片 = 模板标注字段（field_key + 物理坐标），一页一块字段一组；
 * - 未选模板时整页作单一 text 块（仅演示，结果写页 result_json 不写切片 field_key）。
 *
 * 坐标空间约定：字段坐标在模板「物理像素」空间（= 模板 width×height）；
 * 登记切片沿用物理空间；裁块时 × (preview_w / full_w) 映射到 prepare 预览图。
 */

interface StepState {
  key: string;
  label: string;
  status: "wait" | "run" | "ok" | "fail";
  detail?: string;
}

const BASE_STEPS: { key: string; label: string }[] = [
  { key: "register", label: "① 上传注册入队（/file-job/register）" },
  { key: "claimJob", label: "② 流水线抢占任务（/file-job/claim）" },
  { key: "prepare", label: "③ 分页渲染（/template/prepare）" },
  { key: "registerPages", label: "④ 登记页+切片（/file-page/register-pages）" },
  { key: "claimPage", label: "⑤ 提取实例抢占页（/file-page/claim）" },
  { key: "extract", label: "⑥ 逐字段裁块 → OCR / QR" },
  { key: "complete", label: "⑦ 回写结果（FileSlice + FilePage 双写）" },
  { key: "result", label: "⑧ 反查完整 JSON（/file-job/{code}/result）" },
];

function initSteps(): StepState[] {
  return BASE_STEPS.map((s) => ({ ...s, status: "wait" }));
}

const PAGE_STATE_LABEL: Record<number, string> = { 0: "待提取", 1: "处理中", 2: "成功", 3: "失败" };

/** 从页预览图按物理坐标（含 pad 外扩）裁出一块，返回 PNG File */
function cropPhysRect(
  img: HTMLImageElement,
  px: number,
  py: number,
  pw: number,
  ph: number,
  pad: number,
  scale: number,
  name: string,
): File | null {
  const sx = Math.max(0, Math.round((px - pad) * scale));
  const sy = Math.max(0, Math.round((py - pad) * scale));
  const ex = Math.min(img.naturalWidth, Math.round((px + pw + pad) * scale));
  const ey = Math.min(img.naturalHeight, Math.round((py + ph + pad) * scale));
  const w = ex - sx;
  const h = ey - sy;
  if (w <= 0 || h <= 0) return null;
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  ctx.drawImage(img, sx, sy, w, h, 0, 0, w, h);
  const url = canvas.toDataURL("image/png");
  const bin = atob(url.split(",", 2)[1]);
  const arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i += 1) arr[i] = bin.charCodeAt(i);
  return new File([arr], name, { type: "image/png" });
}

function loadImage(dataUrl: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("预览图加载失败"));
    img.src = dataUrl;
  });
}

interface ResultPage {
  page_index: number;
  state: number;
  template_id: number | null;
  result: { data: Record<string, FieldExtract> } | null;
  slices: {
    field_key: string | null;
    result_content: string | null;
    source: string | null;
    confidence: number | null;
  }[];
}

export default function FlowTestPage() {
  const [categories, setCategories] = useState<Category[]>([]);
  const [categoryId, setCategoryId] = useState<number | null>(null);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [templateId, setTemplateId] = useState<number | null>(null);
  const [fields, setFields] = useState<TemplateField[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [running, setRunning] = useState(false);
  const [steps, setSteps] = useState<StepState[]>(initSteps);
  const [finalPages, setFinalPages] = useState<ResultPage[] | null>(null);
  const [finalCode, setFinalCode] = useState<string | null>(null);
  const [finalState, setFinalState] = useState<number | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const cancelRef = useRef(false);

  useEffect(() => {
    fetchCategories(1, 200)
      .then((res) => {
        setCategories(res.items);
        const first = res.items.find((c) => c.is_enabled) ?? res.items[0];
        if (first) setCategoryId(first.id);
      })
      .catch(() => setCategories([]));
  }, []);

  useEffect(() => {
    setTemplateId(null);
    setFields([]);
    if (categoryId === null) {
      setTemplates([]);
      return;
    }
    fetchTemplates(1, 200, categoryId)
      .then((res) => setTemplates(res.items.filter((t) => t.is_enabled)))
      .catch(() => setTemplates([]));
  }, [categoryId]);

  useEffect(() => {
    if (templateId === null) {
      setFields([]);
      return;
    }
    fetchFields(templateId).then(setFields).catch(() => setFields([]));
  }, [templateId]);

  function setStep(key: string, patch: Partial<StepState>) {
    setSteps((prev) => prev.map((s) => (s.key === key ? { ...s, ...patch } : s)));
  }

  async function runFlow() {
    if (!file || categoryId === null) {
      setErrorMsg("请先选择分类并上传文件");
      return;
    }
    setRunning(true);
    setErrorMsg(null);
    setFinalPages(null);
    setFinalCode(null);
    setFinalState(null);
    setSteps(initSteps());
    cancelRef.current = false;
    const inst = `web-pipe-${Math.random().toString(36).slice(2, 7)}`;
    const instExt = `web-ext-${Math.random().toString(36).slice(2, 7)}`;
    let jobCode: string | null = null;
    let claimed = false;
    const claimedPageIds: { id: number; inst: string }[] = [];

    try {
      // ① 注册入队（带强制模板）
      setStep("register", { status: "run" });
      const job = await registerFileJob(file, categoryId, templateId);
      jobCode = job.code;
      setStep("register", { status: "ok", detail: jobCode });

      // ② 流水线抢占本任务（跳过队列里的历史任务，抢回后归还）
      setStep("claimJob", { status: "run" });
      for (let guard = 0; guard < 15; guard += 1) {
        if (cancelRef.current) throw new Error("已手动停止");
        const j = await claimJob(inst);
        if (!j) break;
        if (j.code === jobCode) {
          claimed = true;
          break;
        }
        await failJob(j.code, inst, "流程测试归还历史任务").catch(() => undefined);
      }
      if (!claimed) throw new Error(`未能抢占本任务（${jobCode}），可能已被其它实例占用`);
      setStep("claimJob", { status: "ok", detail: `实例 ${inst}` });

      // ③ 分页渲染
      setStep("prepare", { status: "run" });
      const prep: PreparedFile = await prepareTemplateFile(file);
      setStep("prepare", { status: "ok", detail: `${prep.page_count} 页 · ${prep.preview_dpi}dpi` });

      // ④ 登记页 + 切片（切片=模板字段物理坐标；无字段则整页一块无 field_key）
      setStep("registerPages", { status: "run" });
      const pagesPayload = prep.pages.map((p: PreparedPage) => {
        const slices =
          fields.length > 0
            ? fields.map((f, i) => ({
                slice_index: i,
                field_key: f.field_key,
                rel_path: null,
                x: f.x,
                y: f.y,
                width: f.width,
                height: f.height,
              }))
            : [{ slice_index: 0, field_key: null, rel_path: null, x: 0, y: 0, width: p.full_w, height: p.full_h }];
        return {
          page_index: p.index,
          normalized_rel_path: null,
          skew: p.skew ?? null,
          width: p.full_w,
          height: p.full_h,
          dpi: p.dpi,
          template_id: templateId,
          slices,
        };
      });
      const reg = await registerPages(jobCode, inst, pagesPayload);
      setStep("registerPages", {
        status: "ok",
        detail: `${reg.page_count} 页 / ${reg.slice_count} 块，待提取 ${reg.pending_pages} 页`,
      });

      // ⑤⑥⑦ 逐页提取
      const imgCache = new Map<number, HTMLImageElement>();
      let donePages = 0;
      for (let guard = 0; guard < prep.page_count + 1; guard += 1) {
        if (cancelRef.current) throw new Error("已手动停止");
        setStep("claimPage", { status: "run" });
        const pg = await claimPage(instExt);
        if (!pg) {
          setStep("claimPage", { status: "ok", detail: "队列已清空" });
          break;
        }
        claimedPageIds.push({ id: pg.id, inst: instExt });
        setStep("claimPage", { status: "ok", detail: `页 #${pg.page_index}（id=${pg.id}）` });

        setStep("extract", { status: "run" });
        const prepPage = prep.pages[pg.page_index];
        const scale = prepPage.preview_w / (prepPage.full_w || prepPage.preview_w);
        let img = imgCache.get(pg.page_index);
        if (!img) {
          img = await loadImage(prepPage.data_url);
          imgCache.set(pg.page_index, img);
        }
        const data: Record<string, FieldExtract> = {};
        const note: string[] = [];
        for (const sl of pg.slices ?? []) {
          const f = sl.field_key ? fields.find((x) => x.field_key === sl.field_key) : null;
          const isQr = f?.field_type === "qr";
          const isLlm = f?.field_type === "llm";
          const cropFile = cropPhysRect(
            img,
            sl.x,
            sl.y,
            sl.width,
            sl.height,
            f?.pad ?? 0,
            scale,
            `p${pg.page_index}_${sl.field_key ?? "full"}.png`,
          );
          const key = sl.field_key ?? `__page${pg.page_index}__`;
          if (!cropFile) {
            data[key] = { value: null, source: isQr ? "QR" : isLlm ? "LLM" : "OCR", confidence: null };
            note.push(`${key}:空块`);
            continue;
          }
          try {
            if (isQr) {
              const r0 = (await qrDecodeOne(cropFile))[0];
              const text = r0?.texts?.[0] ?? null;
              data[key] = { value: text, source: "QR", confidence: text ? 1 : null };
              note.push(`${key}:${text ? "✓" : "未识别"}`);
            } else if (isLlm) {
              const r0 = (await llmToText(cropFile, "", sl.field_key ?? "", f?.label ?? ""))[0];
              const text = r0?.ok && r0.text ? r0.text : null;
              data[key] = { value: text, source: "LLM", confidence: null };
              note.push(`${key}:${text ? `LLM:${text.slice(0, 8)}` : "LLM空/失败"}`);
            } else {
              const r0 = (await ocrToText(cropFile, "small", "auto"))[0];
              const lines = r0?.lines ?? [];
              const text = r0?.full_text?.trim() || null;
              const avg = lines.length
                ? lines.reduce((a, l) => a + (l.score ?? 0), 0) / lines.length
                : null;
              data[key] = {
                value: text,
                source: "OCR",
                confidence: avg != null ? Math.round(avg * 1000) / 1000 : null,
              };
              note.push(`${key}:${text ? `${text.length}字` : "空"}`);
            }
          } catch {
            data[key] = { value: null, source: isQr ? "QR" : "OCR", confidence: null };
            note.push(`${key}:失败`);
          }
        }
        setStep("extract", { status: "ok", detail: `页#${pg.page_index} ${note.join(" ") || "无块"}` });

        setStep("complete", { status: "run" });
        await completePage(pg.id, instExt, data);
        donePages += 1;
        setStep("complete", {
          status: "ok",
          detail: `${donePages}/${prep.page_count} 页已双写回写`,
        });
      }

      // ⑧ 反查
      setStep("result", { status: "run" });
      const full = await jobResult(jobCode);
      setFinalCode(full.code);
      setFinalState(full.state);
      setFinalPages(full.pages as ResultPage[]);
      setStep("result", {
        status: "ok",
        detail: `job ${full.state === 2 ? "成功" : full.state === 3 ? "失败" : full.state} · ${full.pages.length} 页`,
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setErrorMsg(msg);
      setSteps((prev) => prev.map((s) => (s.status === "run" ? { ...s, status: "fail", detail: msg } : s)));
      if (jobCode && claimed && !cancelRef.current) {
        await failJob(jobCode, inst, `流程测试失败：${msg.slice(0, 200)}`).catch(() => undefined);
      }
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="page">
      <header className="page__header">
        <h1>流程测试</h1>
        <p>
          浏览器扮演服务实例，真实走一遍：注册入队 → 抢占 → 分页 → 切片登记 → 按字段类型裁块提取（OCR / QR）→
          双写回写 FileSlice + FilePage → 反查整任务 JSON。模板匹配 / 自动分切为模拟（切片直接取模板标注字段）。
        </p>
      </header>

      <section className="panel">
        <div className="bench-controls">
          <label className="filter-select">
            分类 *
            <select
              value={categoryId ?? ""}
              disabled={running}
              onChange={(e) => setCategoryId(e.target.value === "" ? null : Number(e.target.value))}
            >
              {categories.map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </select>
          </label>
          <label className="filter-select" title="选定=按该模板标注字段切块提取；不选=整页OCR（仅演示）">
            模板 *
            <select
              value={templateId ?? ""}
              disabled={running || categoryId === null}
              onChange={(e) => setTemplateId(e.target.value === "" ? null : Number(e.target.value))}
            >
              <option value="">（不选，整页OCR）</option>
              {templates.map((t) => (
                <option key={t.id} value={t.id}>{t.code} {t.name}</option>
              ))}
            </select>
          </label>
          <label className="btn btn--file">
            {file ? `已选：${file.name}` : "选择文件…"}
            <input
              type="file"
              accept=".pdf,.png,.jpg,.jpeg,.bmp,.tif,.tiff"
              hidden
              disabled={running}
              onChange={(e) => {
                setFile(e.target.files?.[0] ?? null);
                e.target.value = "";
              }}
            />
          </label>
          {!running ? (
            <button
              type="button"
              className="primary primary--inline"
              onClick={() => void runFlow()}
              disabled={!file || categoryId === null}
            >
              开始全流程
            </button>
          ) : (
            <button type="button" onClick={() => { cancelRef.current = true; }}>
              停止
            </button>
          )}
        </div>
        {templateId !== null && (
          <p className="muted" style={{ marginTop: 8 }}>
            模板标注字段 {fields.length} 个：
            {fields.map((f) => `${f.field_key}(${f.field_type === "qr" ? "QR" : "文字"})`).join("、") ||
              "（无，将整页OCR）"}
          </p>
        )}
        {errorMsg && <p className="error">{errorMsg}</p>}
      </section>

      <section className="panel">
        <div className="panel__toolbar"><h2>执行步骤</h2></div>
        <ol className="flow-steps">
          {steps.map((s) => (
            <li key={s.key} className={`flow-step flow-step--${s.status}`}>
              <span className="flow-step__label">{s.label}</span>
              <span className="flow-step__badge">
                {s.status === "wait" ? "待执行" : s.status === "run" ? "执行中…" : s.status === "ok" ? "✓ 完成" : "✗ 失败"}
              </span>
              {s.detail && <span className="flow-step__detail mono">{s.detail}</span>}
            </li>
          ))}
        </ol>
      </section>

      {finalPages && (
        <section className="panel">
          <div className="panel__toolbar">
            <h2>任务结果 {finalCode}</h2>
            <span className={`status status--${finalState === 2 ? "ok" : "error"}`}>
              {finalState === 2 ? "成功" : finalState === 3 ? "失败" : String(finalState)}
            </span>
          </div>
          {finalPages.map((p) => (
            <div key={p.page_index} className="flow-page-result">
              <h3>
                页 #{p.page_index}（{PAGE_STATE_LABEL[p.state] ?? p.state}）
                {p.template_id ? ` · 模板 #${p.template_id}` : ""}
              </h3>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>字段</th>
                    <th>值（result_content / value）</th>
                    <th>来源</th>
                    <th>置信度</th>
                  </tr>
                </thead>
                <tbody>
                  {p.slices.filter((s) => s.field_key).length > 0 ? (
                    p.slices
                      .filter((s) => s.field_key)
                      .map((s) => (
                        <tr key={s.field_key as string}>
                          <td className="mono">{s.field_key}</td>
                          <td className="wrap">{s.result_content ?? "—"}</td>
                          <td>{s.source ?? "—"}</td>
                          <td>{s.confidence != null ? s.confidence.toFixed(3) : "—"}</td>
                        </tr>
                      ))
                  ) : p.result?.data ? (
                    Object.entries(p.result.data).map(([k, v]) => (
                      <tr key={k}>
                        <td className="mono">{k}</td>
                        <td className="wrap">{v.value ?? "—"}</td>
                        <td>{v.source}</td>
                        <td>{v.confidence != null ? v.confidence.toFixed(3) : "—"}</td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={4} className="muted">无提取数据</td>
                    </tr>
                  )}
                </tbody>
              </table>
              <details className="flow-json">
                <summary>页 result_json（FilePage.result_json 原样）</summary>
                <pre>{JSON.stringify(p.result, null, 2)}</pre>
              </details>
            </div>
          ))}
        </section>
      )}
    </div>
  );
}
