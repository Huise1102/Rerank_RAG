const LABELS = {
  vector: '向量',
  bm25: 'BM25',
  fusion: '融合',
  rerank: '精排',
}

function strategyName(s) {
  const v = !!s.vector
  const b = !!s.bm25
  const r = !!s.rerank
  let core = ''
  if (v && b) core = '混合召回'
  else if (v) core = '向量召回'
  else if (b) core = 'BM25 召回'
  else core = '未启用检索'
  return r ? `${core} + 精排` : core
}

export default function StrategyBuilder({ value, readonly }) {
  const s = value || {}
  return (
    <span className="sb-tags">
      {Object.entries(LABELS).map(([key, label]) => {
        const on = s[key] !== false
        return (
          <span key={key} className={`sb-tag${on ? ' is-on' : ''}`}>
            {label}
          </span>
        )
      })}
      <span className="sb-name">→ {strategyName(s)}</span>
    </span>
  )
}