import { useState } from 'react'

// 支持数值安全格式化 + 空值返回 null
export function num(v) {
  return typeof v === 'number' && !Number.isNaN(v) ? v : null
}

export function fmt(v, digits = 4) {
  const n = num(v)
  return n === null ? '-' : n.toFixed(digits)
}

function pageText(doc) {
  const p = doc?.page
  return p === undefined || p === null || p === '' ? '' : String(p)
}

function matchedText(doc) {
  const m = doc?.matched_by
  return Array.isArray(m) && m.length ? m.join('+') : ''
}

// 单个文档块（供 RetrievalDetail 与 DecisionTrace 复用）。
// 正文默认折叠，只保留「序号 / 来源 / 页码 / 分数 / 命中」摘要，点击展开原文。
export function DocLine({ doc, index }) {
  const [expanded, setExpanded] = useState(false)
  const text = String(doc?.text || '')

  return (
    <div className="rd-doc">
      <div className="rd-doc__head">
        <span className="rd-rank">{doc?.rank ?? index}.</span>
        <span className="rd-source">{doc?.source || '未知来源'}</span>
        {pageText(doc) ? <span className="rd-page">第 {pageText(doc)} 页</span> : null}
      </div>
      <div className="rd-scores">
        {num(doc?.score) !== null ? <span>最终 {fmt(doc.score)}</span> : null}
        {num(doc?.fused_score) !== null ? <span>融合 {fmt(doc.fused_score, 5)}</span> : null}
        {num(doc?.rerank_score) !== null ? <span>精排 {fmt(doc.rerank_score)}</span> : null}
        {matchedText(doc) ? <span>命中 {matchedText(doc)}</span> : null}
      </div>
      {text ? (
        <>
          {expanded ? <div className="rd-text">{text}</div> : null}
          <button className="rd-toggle" type="button" onClick={() => setExpanded((v) => !v)}>
            {expanded ? '收起原文' : `展开原文（${text.length} 字）`}
          </button>
        </>
      ) : null}
    </div>
  )
}

const TIMING_LABELS = {
  recall_ms: '召回',
  retrieve_ms: '检索',
  rerank_ms: '精排',
  generate_ms: '生成',
  total_ms: '总计',
}

const STATS_LABELS = {
  dense: '向量召回',
  sparse: 'BM25 召回',
  overlap: '两路重叠',
  dense_only: '仅向量',
  sparse_only: '仅 BM25',
}

export default function RetrievalDetail({ docs = [], latency = {}, stats = null }) {
  const timingRows = Object.entries(TIMING_LABELS)
    .map(([key, label]) => {
      const v = latency?.[key]
      return typeof v === 'number' && v > 0
        ? { label, value: `${(v / 1000).toFixed(2)}s` }
        : null
    })
    .filter(Boolean)

  const statsRows = (() => {
    if (!stats || typeof stats !== 'object') return []
    return Object.entries(STATS_LABELS)
      .map(([key, label]) =>
        stats[key] !== undefined && stats[key] !== null && stats[key] !== ''
          ? { label, value: String(stats[key]) }
          : null
      )
      .filter(Boolean)
  })()

  return (
    <div className="retrieval-detail">
      <div className="rd-docs">
        {docs.map((doc, i) => (
          <DocLine key={doc?.chunk_id || i} doc={doc} index={doc?.rank || i + 1} />
        ))}
      </div>

      {timingRows.length ? (
        <div className="rd-block">
          <div className="rd-block__title">耗时分解</div>
          <div className="rd-lines">
            {timingRows.map((row) => (
              <div key={row.label} className="rd-line">
                <span>{row.label}</span>
                <span>{row.value}</span>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {statsRows.length ? (
        <div className="rd-block">
          <div className="rd-block__title">两路召回统计</div>
          <div className="rd-lines">
            {statsRows.map((row) => (
              <div key={row.label} className="rd-line">
                <span>{row.label}</span>
                <span>{row.value}</span>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  )
}