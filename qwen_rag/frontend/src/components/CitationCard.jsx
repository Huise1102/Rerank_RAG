function num(v) {
  return typeof v === 'number' && !Number.isNaN(v) ? v : null
}

export default function CitationCard({ citation, index, id }) {
  const source = citation.source || '未知来源'
  const page = citation.page === null || citation.page === undefined || citation.page === '' ? null : citation.page
  const score = num(citation.score)
  const count = citation.count || 0

  return (
    <div className="citation-card" id={id}>
      <div className="citation-card__head">
        <span className="cc-index">[{index}]</span>
        <span className="cc-source">{source}</span>
      </div>
      <div className="cc-meta">
        {page !== null ? <span>第 {page} 页</span> : null}
        {score !== null ? <span>相关度 {score.toFixed(4)}</span> : null}
        <span>引用 {count} 次</span>
      </div>
      {citation.text ? <div className="cc-text">{citation.text}</div> : null}
    </div>
  )
}