import { useEffect, useState } from 'react'
import { listRetrievalLogs, systemLogs } from '../api/log'
import { useKbStore } from '../stores/kbStore'
import { message } from '../utils/toast'

function fmtTime(s) {
  if (!s) return '-'
  const d = new Date(s)
  if (Number.isNaN(d.getTime())) return String(s)
  return d.toLocaleString('zh-CN', { hour12: false })
}

export default function Logs() {
  const kb = useKbStore()
  const [tab, setTab] = useState('retrieval')
  const [kbFilter, setKbFilter] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [logs, setLogs] = useState({ items: [], total: 0 })
  const [loading, setLoading] = useState(false)

  const [sysLines, setSysLines] = useState([])
  const [sysMeta, setSysMeta] = useState(null)
  const [sysLoading, setSysLoading] = useState(false)

  // 检索日志筛选也依赖知识库列表（下拉）
  const allKbs = kb.kbs

  useEffect(() => {
    kb.fetchKbs()
  }, [])

  async function loadLogs() {
    setLoading(true)
    try {
      const data = await listRetrievalLogs({
        page,
        page_size: pageSize,
        kb_id: kbFilter ? Number(kbFilter) : undefined,
      })
      setLogs({ items: data.items || [], total: data.total || 0 })
    } catch (e) {
      /* 拦截器已提示 */
    } finally {
      setLoading(false)
    }
  }

  async function loadSystem() {
    setSysLoading(true)
    try {
      const data = await systemLogs({ lines: 200 })
      setSysLines(data.lines || [])
      setSysMeta(data)
    } catch (e) {
      /* 拦截器已提示 */
    } finally {
      setSysLoading(false)
    }
  }

  useEffect(() => {
    loadLogs()
  }, [page, pageSize])

  const totalPages = Math.max(1, Math.ceil(logs.total / pageSize))

  return (
    <div className="page">
      <div className="page-head">
        <div className="page-head__left">
          <div className="page-title">日志</div>
          <div className="page-subtitle">检索日志（数据库）与系统日志（app.log 尾部）。</div>
        </div>
      </div>

      <div className="filter-bar">
        <button className={`btn btn--sm${tab === 'retrieval' ? ' btn--primary' : ''}`} onClick={() => setTab('retrieval')}>
          检索日志
        </button>
        <button className={`btn btn--sm${tab === 'system' ? ' btn--primary' : ''}`} onClick={() => { setTab('system'); loadSystem() }}>
          系统日志
        </button>
      </div>

      {tab === 'retrieval' ? (
        <div className="panel">
          <div className="filter-bar">
            <select className="kb-select" value={kbFilter} onChange={(e) => setKbFilter(e.target.value)}>
              <option value="">全部知识库</option>
              {allKbs.map((k) => (
                <option key={k.id} value={k.id}>
                  {k.name}
                </option>
              ))}
            </select>
            <button className="btn btn--sm" onClick={() => { setPage(1); loadLogs() }}>
              查询
            </button>
            <span className="text-muted" style={{ fontSize: 13 }}>
              共 {logs.total} 条
            </span>
          </div>

          {loading ? (
            <div className="empty-state">加载中…</div>
          ) : logs.items.length === 0 ? (
            <div className="empty-state">暂无检索日志</div>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>知识库</th>
                  <th>查询</th>
                  <th>策略</th>
                  <th>命中</th>
                  <th>耗时</th>
                  <th>时间</th>
                </tr>
              </thead>
              <tbody>
                {logs.items.map((l) => (
                  <tr key={l.id}>
                    <td className="mono-cell">{l.id}</td>
                    <td>{kb.kbs.find((k) => k.id === l.kb_id)?.name ?? `#${l.kb_id ?? '-'}`}</td>
                    <td style={{ maxWidth: 360, wordBreak: 'break-word' }}>{l.query}</td>
                    <td>{l.strategy ? <span className="tag tag--accent">{l.strategy}</span> : '-'}</td>
                    <td className="mono-cell">{l.result_count ?? '-'}</td>
                    <td className="mono-cell">{typeof l.latency === 'number' ? `${l.latency.toFixed(2)}s` : '-'}</td>
                    <td style={{ fontSize: 12 }}>{fmtTime(l.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {logs.total > 0 && (
            <div className="filter-bar" style={{ marginTop: 12 }}>
              <button className="btn btn--sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                上一页
              </button>
              <span className="text-muted" style={{ fontSize: 13 }}>
                {page} / {totalPages}
              </span>
              <button className="btn btn--sm" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}>
                下一页
              </button>
            </div>
          )}
        </div>
      ) : (
        <div className="panel">
          <div className="filter-bar">
            <button className="btn btn--sm" disabled={sysLoading} onClick={loadSystem}>
              {sysLoading ? '加载中…' : '刷新'}
            </button>
            {sysMeta?.exists === false ? (
              <span className="text-danger" style={{ fontSize: 13 }}>
                日志文件不存在：{sysMeta.path}
              </span>
            ) : (
              <span className="text-muted" style={{ fontSize: 13 }}>
                {sysLines.length} 行 · {sysMeta?.path || ''}
              </span>
            )}
          </div>
          {sysLines.length === 0 ? (
            <div className="empty-state">暂无系统日志</div>
          ) : (
            <pre
              className="scroll-area"
              style={{
                margin: 0,
                padding: 12,
                background: 'var(--app-surface-soft)',
                border: '1px solid var(--app-border)',
                borderRadius: 6,
                fontSize: 12.5,
                fontFamily: 'var(--app-mono)',
                lineHeight: 1.7,
                maxHeight: 560,
                overflow: 'auto',
                color: 'var(--app-text-regular)',
              }}
            >
              {sysLines.join('\n')}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}