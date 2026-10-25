import { useEffect, useState } from 'react'
import request from '../api/request'
import { useKbStore, DEFAULT_STRATEGIES } from '../stores/kbStore'
import { message } from '../utils/toast'
import RetrievalDetail from '../components/RetrievalDetail'

const SWITCH_LABELS = {
  vector: '向量',
  bm25: 'BM25',
  fusion: '融合',
  rerank: '精排',
}

// 将检索调试响应的 results 映射为检索详情组件所需字段（metadata.source/page 提升为顶层）
function mapResults(results) {
  return (results || []).map((r) => ({
    chunk_id: r.chunk_id,
    rank: r.rank,
    text: r.text,
    score: r.final_score ?? r.score,
    fused_score: r.fused_score,
    rerank_score: r.rerank_score,
    matched_by: r.matched_by || [],
    source: r.metadata?.source || '',
    page: r.metadata?.page,
  }))
}

export default function RetrievalStrategy() {
  const kb = useKbStore()

  const [query, setQuery] = useState('')
  const [topK, setTopK] = useState('')
  const [switches, setSwitches] = useState({ ...DEFAULT_STRATEGIES })
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)

  const activeKbs = kb.kbs.filter((k) => k.is_active)

  useEffect(() => {
    kb.fetchKbs()
  }, [])

  function toggle(key) {
    setSwitches((s) => ({ ...s, [key]: !s[key] }))
  }

  async function run() {
    const q = query.trim()
    if (!q) {
      message.warning('请输入检索查询')
      return
    }
    if (!kb.currentKbId) {
      message.warning('请选择知识库')
      return
    }
    setLoading(true)
    try {
      const data = await request.post(`/kb/${kb.currentKbId}/retrieve`, {
        query: q,
        top_k: topK ? Number(topK) : null,
        strategy: switches,
      })
      setResult(data)
    } catch (e) {
      /* 拦截器已提示 */
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <div className="page-head__left">
          <div className="page-title">检索策略调试</div>
          <div className="page-subtitle">只检索不生成，自由组合四个构建块开关，观察召回与耗时。</div>
        </div>
      </div>

      <div className="panel">
        <div className="section-label">检索参数</div>
        <div className="filter-bar">
          <select className="kb-select" value={kb.currentKbId ?? ''} onChange={(e) => kb.setCurrentKb(e.target.value ? Number(e.target.value) : null)}>
            {activeKbs.length === 0 && <option value="">无可用知识库</option>}
            {activeKbs.map((k) => (
              <option key={k.id} value={k.id}>
                {k.name}
              </option>
            ))}
          </select>
          <input
            className="field"
            style={{ flex: '1', minWidth: 260 }}
            placeholder="请输入检索查询…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') run()
            }}
          />
          <input
            className="field"
            style={{ width: 100 }}
            placeholder="top_k"
            value={topK}
            onChange={(e) => setTopK(e.target.value)}
          />
          <button className="btn btn--primary" disabled={loading} onClick={run}>
            {loading ? '检索中…' : '检索'}
          </button>
        </div>

        <div className="section-label" style={{ marginTop: 14 }}>
          构建块开关
        </div>
        <div className="filter-bar">
          {Object.entries(SWITCH_LABELS).map(([key, label]) => (
            <label
              key={key}
              style={{ display: 'inline-flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}
            >
              <span className="switch">
                <input type="checkbox" checked={switches[key]} onChange={() => toggle(key)} />
                <span className="switch__track">
                  <span className="switch__thumb" />
                </span>
              </span>
              <span>{label}</span>
            </label>
          ))}
        </div>
      </div>

      {result && (
        <div className="panel">
          <div className="section-label">
            检索结果：{result.strategy || '-'} · {result.count ?? 0} 条
            {result.warnings?.length ? <span className="text-muted"> ｜ 告警：{result.warnings.join('；')}</span> : null}
          </div>
          <RetrievalDetail
            docs={mapResults(result.results)}
            latency={result.latency || {}}
            stats={result.stats || null}
          />
        </div>
      )}
    </div>
  )
}