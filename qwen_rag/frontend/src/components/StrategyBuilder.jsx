const BLOCKS = [
  {
    key: 'vector',
    short: '向量',
    name: '向量召回',
    desc: 'BGE 稠密向量，语义相似匹配',
  },
  {
    key: 'bm25',
    short: 'BM25',
    name: 'BM25 召回',
    desc: '关键词频率检索，精确匹配强',
  },
  {
    key: 'fusion',
    short: '融合',
    name: 'RRF 融合（k=60）',
    desc: '双路召回按名次融合，仅双路同时开启才有意义',
  },
  {
    key: 'rerank',
    short: '精排',
    name: '精排 Rerank',
    desc: 'bge-reranker-v2-m3 交叉编码精排',
  },
]

// 构建块组合 → 人类可读的策略名
function describeStrategies(s) {
  const vector = !!s.vector
  const bm25 = !!s.bm25
  const rerank = !!s.rerank
  const bothRecall = vector && bm25

  if (!vector && !bm25) return '未启用召回'
  if (bothRecall) return rerank ? '混合 + Rerank' : '混合'
  const base = vector ? '纯向量' : '纯 BM25'
  return rerank ? `${base} + Rerank` : base
}

export default function StrategyBuilder({ value, readonly = false, onChange }) {
  const model = value || {}
  const current = {
    vector: model.vector !== false,
    bm25: model.bm25 !== false,
    fusion: model.fusion !== false,
    rerank: model.rerank !== false,
  }

  if (readonly) {
    return (
      <div className="sb-readonly">
        {BLOCKS.map((block) => (
          <span key={block.key} className={`sb-tag${current[block.key] ? '' : ' is-off'}`}>
            {block.short}
          </span>
        ))}
        <span className="sb-combo">→ {describeStrategies(current)}</span>
      </div>
    )
  }

  function toggle(key, next) {
    const updated = { ...current, [key]: next }
    // 融合仅在两路召回同时开启时有意义，关闭任一路时自动关闭融合
    if ((key === 'vector' || key === 'bm25') && !next) {
      updated.fusion = false
    }
    onChange?.(updated)
  }

  const canFuse = current.vector && current.bm25

  return (
    <div className="sb-list">
      {BLOCKS.map((block) => (
        <div key={block.key} className="sb-row">
          <div className="sb-row__info">
            <div className="sb-row__name">{block.name}</div>
            <div className="sb-row__desc">{block.desc}</div>
          </div>
          <span className="switch">
            <input
              type="checkbox"
              checked={current[block.key]}
              disabled={block.key === 'fusion' && !canFuse}
              onChange={(e) => toggle(block.key, e.target.checked)}
            />
            <span className="switch__track">
              <span className="switch__thumb" />
            </span>
          </span>
        </div>
      ))}
    </div>
  )
}
