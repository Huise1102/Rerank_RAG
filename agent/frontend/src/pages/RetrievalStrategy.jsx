import { useEffect, useState } from 'react'
import { updateStrategies } from '../api/kb'
import { useKbStore, DEFAULT_STRATEGIES } from '../stores/kbStore'
import { message } from '../utils/toast'
import StrategyBuilder from '../components/StrategyBuilder'

const COMBOS = [
  { name: '仅 向量', result: '纯向量检索' },
  { name: '仅 BM25', result: '纯 BM25 检索' },
  { name: '向量 + BM25 + 融合', result: '混合检索（RRF）' },
  { name: '全部开启', result: '混合检索 + Rerank 精排' },
]

const PARAMS = [
  { label: '双路召回', value: '各 Top-50' },
  { label: 'RRF 融合', value: 'k = 60' },
  { label: '候选池', value: '20' },
  { label: '精排保留', value: 'Top-5' },
  { label: '上下文预算', value: '6000 字' },
]

function comboLabel(s) {
  const bothRecall = s.vector && s.bm25
  if (!s.vector && !s.bm25) return '未启用召回'
  if (bothRecall) return s.rerank ? '混合 + Rerank' : '混合'
  const base = s.vector ? '纯向量' : '纯 BM25'
  return s.rerank ? `${base} + Rerank` : base
}

export default function RetrievalStrategy() {
  const kbStore = useKbStore()
  const [strategies, setStrategies] = useState({ ...DEFAULT_STRATEGIES })
  const [saving, setSaving] = useState(false)

  const currentKb = kbStore.kbs.find((k) => k.id === kbStore.currentKbId) || null

  function loadFromCurrent() {
    const raw = currentKb?.enabled_strategies
    setStrategies(raw ? { ...DEFAULT_STRATEGIES, ...raw } : { ...DEFAULT_STRATEGIES })
  }

  useEffect(() => {
    async function init() {
      if (!kbStore.kbs.length) await kbStore.fetchKbs()
    }
    init()
  }, [])

  useEffect(() => {
    loadFromCurrent()
  }, [kbStore.currentKbId])

  function resetForm() {
    loadFromCurrent()
    message.info('已重置为当前生效的策略')
  }

  async function save() {
    if (!kbStore.currentKbId) return
    setSaving(true)
    try {
      await updateStrategies(kbStore.currentKbId, { ...strategies })
      message.success('检索策略保存成功')
      await kbStore.fetchKbs()
    } catch (e) {
      /* 拦截器已提示 */
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <div className="page-head__left">
          <div className="page-title">检索策略</div>
          <div className="page-subtitle">
            用四个构建块开关自由组合检索链路，保存后对该知识库立即生效
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="kb-picker">
          <span className="picker-label">选择知识库</span>
          <select
            className="kb-select"
            style={{ width: 260 }}
            value={kbStore.currentKbId ?? ''}
            onChange={(e) => kbStore.setCurrentKb(e.target.value ? Number(e.target.value) : null)}
          >
            {kbStore.kbs.length === 0 && <option value="">请选择知识库</option>}
            {kbStore.kbs.map((kb) => (
              <option key={kb.id} value={kb.id}>
                {kb.name}
              </option>
            ))}
          </select>
          {currentKb && (
            <span className="picker-combo">
              当前组合：<b>{comboLabel(strategies)}</b>
            </span>
          )}
        </div>

        {!currentKb && <div className="empty-hint__sub">暂无知识库，请先在「知识库」页创建</div>}
      </div>

      {currentKb && (
        <>
          <div className="panel">
            <div className="section-label">构建块开关</div>
            <StrategyBuilder value={strategies} onChange={setStrategies} />
            <div className="save-bar">
              <button className="btn btn--primary" disabled={saving} onClick={save}>
                {saving ? '保存中…' : '保存策略'}
              </button>
              <button className="btn btn--ghost" onClick={resetForm}>
                重置
              </button>
            </div>
          </div>

          <div className="panel two-col">
            <div>
              <div className="section-label">组合说明</div>
              <ul className="combo-list">
                {COMBOS.map((item) => (
                  <li key={item.name}>
                    <span className="combo-name">{item.name}</span>
                    <span className="combo-arrow">→</span>
                    <span className="combo-result">{item.result}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <div className="section-label">链路参数</div>
              <ul className="param-list">
                {PARAMS.map((p) => (
                  <li key={p.label}>
                    <span className="param-label">{p.label}</span>
                    <span className="param-value">{p.value}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
