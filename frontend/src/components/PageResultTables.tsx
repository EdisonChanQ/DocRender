import type { JobResultPage, MatchBand } from "../api/filePage";

/** 与服务端 file_page_service.PAGE_STATE_* 对齐：0待提取 1处理中 2成功 3失败 */
const PAGE_STATE_LABEL: Record<number, string> = {
  0: "待提取",
  1: "处理中",
  2: "成功",
  3: "失败",
};

const BAND_LABEL: Record<MatchBand, string> = {
  auto: "匹配良好",
  low: "匹配偏低",
  reject: "配准失败·待审",
  unknown: "未配准",
};

const BAND_CLASS: Record<MatchBand, string> = {
  auto: "ok",
  low: "warn",
  reject: "warn",
  unknown: "muted",
};

/**
 * 逐页「字段结果」表格（任务回执 / 历史任务共用）。
 *
 * 数据优先级：切片行（FileSlice，带 result_content/source/confidence）优先，
 * 退回落页汇总（FilePage.result_json）。两者都空则显示「无提取数据」。
 *
 * reject 页醒目标注：配准相关度低于阈值（<0.45），页图被隔离到 reject/ 目录、
 * 字段坐标未套用——state 仍为"成功"（整页文本提取完成），但内容不可直接使用，
 * 需人工审计。low 档同样标注分数，供复核。
 */
export default function PageResultTables({ pages }: { pages: JobResultPage[] }) {
  if (pages.length === 0) {
    return (
      <p className="muted" style={{ marginTop: 12 }}>
        暂无页数据：流水线实例尚未处理该任务。
      </p>
    );
  }

  return (
    <>
      {pages.map((page) => {
        const band: MatchBand = page.match_band ?? "unknown";
        const isReject = band === "reject";
        return (
          <div
            key={page.page_index}
            className="flow-page-result"
            style={isReject ? { borderLeft: "3px solid #b45309", paddingLeft: 10 } : undefined}
          >
            <h3>
              页 #{page.page_index}（{PAGE_STATE_LABEL[page.state] ?? page.state}）
              {page.template_id != null ? ` · 模板 #${page.template_id}` : ""}
              <span
                className={`status status--${BAND_CLASS[band]}`}
                style={{ marginLeft: 8 }}
                title={
                  page.match_score != null
                    ? `配准相关度 ${page.match_score.toFixed(4)}（auto ≥0.75 / low ≥0.45 / reject <0.45）`
                    : "无配准信息（整页提取）"
                }
              >
                {BAND_LABEL[band]}
                {page.match_score != null && ` ${(page.match_score * 100).toFixed(1)}%`}
              </span>
            </h3>
            {isReject && (
              <p className="status status--warn" style={{ display: "inline-block", margin: "4px 0" }}>
                本页与任何模板的配准相关度过低，已隔离到 reject/ 目录待人工审计：
                字段坐标未套用，以下整页文本仅供参考，不可直接使用。
              </p>
            )}
            {page.error_msg && <p className="error">{page.error_msg}</p>}
          <table className="data-table data-table--fields">
            <thead>
              <tr>
                <th style={{ width: "26%" }}>字段</th>
                <th>值</th>
                <th style={{ width: 70, textAlign: "center" }}>来源</th>
                <th style={{ width: 84, textAlign: "right" }}>置信度</th>
              </tr>
            </thead>
            <tbody>
              {page.slices.some((slice) => slice.field_key) ? (
                page.slices
                  .filter((slice) => slice.field_key)
                  .map((slice) => (
                    <tr key={slice.slice_index}>
                      <td className="mono">{slice.field_key}</td>
                      <td className="wrap">{slice.result_content ?? "—"}</td>
                      <td style={{ textAlign: "center" }}>{slice.source ?? "—"}</td>
                      <td style={{ textAlign: "right" }}>
                        {slice.confidence != null ? slice.confidence.toFixed(3) : "—"}
                      </td>
                    </tr>
                  ))
              ) : page.result?.data ? (
                Object.entries(page.result.data).map(([key, value]) => (
                  <tr key={key}>
                    <td className="mono">{key}</td>
                    <td className="wrap">{value.value ?? "—"}</td>
                    <td style={{ textAlign: "center" }}>{value.source}</td>
                    <td style={{ textAlign: "right" }}>
                      {value.confidence != null ? value.confidence.toFixed(3) : "—"}
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={4} className="muted">
                    无提取数据
                  </td>
                </tr>
              )}
            </tbody>
          </table>
          {page.result?.data && (
            <details className="flow-json">
              <summary>查看本页原始 JSON</summary>
              <pre>{JSON.stringify(page.result, null, 2)}</pre>
            </details>
          )}
          </div>
        );
      })}
    </>
  );
}
