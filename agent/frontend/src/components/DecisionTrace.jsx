import { DocLine } from './RetrievalDetail'

// 行动标签：agent 后端 trace 里的 action 字段
const ACTION_LABELS = {
  answer: '直接作答',
  retrieve: '检索',
  ask_clarification: '澄清',
  force_answer: '强制作答',
}

const STRATEGY_LABELS = {
  vector: '纯向量',
  bm25: '纯关键词',
  hybrid: '混合检索',
  hybrid_rerank: '混合 + 精排',
}

function fmtMs(ms) {
  if (typeof ms !== 'number' || ms <= 0) return ''
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${ms}ms`
}

export default function DecisionTrace({ trace = [] }) {
  const steps = Array.isArray(trace) ? trace : []
  if (!steps.length) {
    return <div className="dt-empty">暂无决策轨迹</div>
  }

  const totalDocs = steps.reduce((sum, s) => sum + (s.result_count || 0), 0)
  const retrieveCount = steps.filter((s) => s.action === 'retrieve').length

  return (
    <div className="decision-trace">
      <div className="dt-summary">
        共 {steps.length} 轮 · 检索 {retrieveCount} 次 · 累计召回 {totalDocs} 条
      </div>
      <div className="dt-list">
        {steps.map((step, i) => (
          <StepCard key={i} step={step} />
        ))}
      </div>
    </div>
  )
}

function StepCard({ step }) {
  const label = ACTION_LABELS[step.action] || step.action || '决策'
  const isRetrieve = step.action === 'retrieve'

  return (
    <div className="dt-card">
      <div className="dt-card__head">
        <span className="dt-round">第 {step.round ?? '-'} 轮</span>
        <span className={`dt-action dt-action--${step.action}`}>{label}</span>
        <span className="dt-elapsed">{fmtMs(step.elapsed_ms)}</span>
      </div>

      {isRetrieve ? (
        <>
          <div className="dt-meta">
            <span className="dt-kv">
              <b>改写 query</b>{' '}
              {step.args?.query ? <span className="mono">{step.args.query}</span> : '（无）'}
            </span>
            <span className="dt-kv">
              <b>策略</b> {STRATEGY_LABELS[step.strategy] || step.strategy || '-'}
            </span>
            <span className="dt-kv">
              <b>命中</b> {step.result_count ?? 0} 条
            </span>
          </div>
          {step.docs?.length ? (
            <div className="dt-docs">
              {step.docs.map((d, i) => (
                <DocLine key={d?.chunk_id || i} doc={d} index={i + 1} />
              ))}
            </div>
          ) : null}
        </>
      ) : step.action === 'ask_clarification' ? (
        <div className="dt-clarify">向用户澄清：{step.args?.question || '（无）'}</div>
      ) : (
        <div className="dt-answer">直接输出最终回答，不再调用工具</div>
      )}
    </div>
  )
}