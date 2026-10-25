import { useEffect, useMemo, useRef, useState } from 'react'
import { useKbStore, DEFAULT_STRATEGIES } from '../stores/kbStore'
import { createKb, updateKb, deleteKb } from '../api/kb'
import { listDocuments, uploadDocument, deleteDocument, documentFileUrl } from '../api/document'
import { message } from '../utils/toast'
import StrategyBuilder from '../components/StrategyBuilder'

const SWITCH_LABELS = {
  vector: '向量',
  bm25: 'BM25',
  fusion: '融合',
  rerank: '精排',
}

const STATUS_LABELS = {
  pending: { text: '待入库', cls: 'tag--muted' },
  processing: { text: '入库中', cls: 'tag--accent' },
  ready: { text: '已就绪', cls: 'tag--success' },
  error: { text: '失败', cls: 'tag--muted' },
}

function fmtSize(bytes) {
  if (!bytes && bytes !== 0) return '-'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export default function KnowledgeBase() {
  const kb = useKbStore()

  const [selectedKbId, setSelectedKbId] = useState(null)
  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState(null) // 编辑时的 KB 对象；null 表示新建
  const [form, setForm] = useState({ name: '', description: '', chunk_strategy: 'fixed', strategies: { ...DEFAULT_STRATEGIES } })
  const [saving, setSaving] = useState(false)

  const [docs, setDocs] = useState([])
  const [docsLoading, setDocsLoading] = useState(false)
  const [uploading, setUploading] = useState(false)
  const fileRef = useRef(null)

  useEffect(() => {
    kb.fetchKbs()
  }, [])

  // 首次加载后默认选中一个知识库
  useEffect(() => {
    if (selectedKbId == null && kb.currentKbId != null) setSelectedKbId(kb.currentKbId)
  }, [kb.currentKbId])

  const selectedKb = useMemo(
    () => kb.kbs.find((k) => k.id === selectedKbId) || null,
    [kb.kbs, selectedKbId]
  )

  useEffect(() => {
    if (selectedKbId != null) loadDocuments(selectedKbId)
  }, [selectedKbId])

  async function loadDocuments(id) {
    setDocsLoading(true)
    try {
      const data = await listDocuments(id)
      setDocs(Array.isArray(data) ? data : data?.items ?? [])
    } catch (e) {
      setDocs([])
    } finally {
      setDocsLoading(false)
    }
  }

  function openCreate() {
    setEditing(null)
    setForm({ name: '', description: '', chunk_strategy: 'fixed', strategies: { ...DEFAULT_STRATEGIES } })
    setFormOpen(true)
  }

  function openEdit(k) {
    setEditing(k)
    setForm({
      name: k.name,
      description: k.description || '',
      chunk_strategy: k.chunk_strategy || 'fixed',
      strategies: { ...DEFAULT_STRATEGIES, ...(k.enabled_strategies || {}) },
    })
    setFormOpen(true)
  }

  function toggleSwitch(key) {
    setForm((f) => ({ ...f, strategies: { ...f.strategies, [key]: !f.strategies[key] } }))
  }

  async function submit() {
    if (!form.name.trim()) {
      message.warning('请填写知识库名称')
      return
    }
    setSaving(true)
    try {
      const payload = {
        name: form.name.trim(),
        description: form.description.trim() || null,
        chunk_strategy: form.chunk_strategy,
        enabled_strategies: form.strategies,
      }
      if (editing) {
        await updateKb(editing.id, payload)
        message.success('已更新知识库')
      } else {
        const created = await createKb(payload)
        setSelectedKbId(created.id)
        message.success('已创建知识库')
      }
      setFormOpen(false)
      await kb.fetchKbs()
    } catch (e) {
      /* 拦截器已提示 */
    } finally {
      setSaving(false)
    }
  }

  async function removeKb(k) {
    if (!window.confirm(`确定删除知识库「${k.name}」吗？其文档与索引将一并删除，不可恢复。`)) return
    try {
      await deleteKb(k.id)
      if (selectedKbId === k.id) setSelectedKbId(null)
      kb.afterRemove(k.id)
      await kb.fetchKbs()
      message.success('已删除知识库')
    } catch (e) {
      /* 拦截器已提示 */
    }
  }

  async function onPickFile(e) {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file || selectedKbId == null) return
    setUploading(true)
    try {
      await uploadDocument(selectedKbId, file)
      message.success('上传成功，后台入库中')
      await loadDocuments(selectedKbId)
    } catch (err) {
      /* 拦截器已提示 */
    } finally {
      setUploading(false)
    }
  }

  async function removeDoc(d) {
    if (!window.confirm(`确定删除文档「${d.filename}」吗？`)) return
    try {
      await deleteDocument(d.id)
      message.success('已删除文档')
      await loadDocuments(selectedKbId)
    } catch (err) {
      /* 拦截器已提示 */
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <div className="page-head__left">
          <div className="page-title">知识库</div>
          <div className="page-subtitle">管理知识库、检索构建块开关与文档入库。</div>
        </div>
        <button className="btn btn--primary" onClick={openCreate}>
          新建知识库
        </button>
      </div>

      <div className="panel">
        <table className="data-table">
          <thead>
            <tr>
              <th>名称</th>
              <th>分块策略</th>
              <th>检索开关</th>
              <th>状态</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {kb.kbs.map((k) => (
              <tr key={k.id} onClick={() => setSelectedKbId(k.id)} style={{ cursor: 'pointer' }}>
                <td style={{ fontWeight: 600 }}>{k.name}</td>
                <td>{k.chunk_strategy === 'recursive' ? '递归拆分' : '固定长度'}</td>
                <td>
                  <StrategyBuilder value={k.enabled_strategies || {}} readonly />
                </td>
                <td>{k.is_active ? <span className="tag tag--success">启用</span> : <span className="tag tag--muted">停用</span>}</td>
                <td>
                  <div className="row-actions" onClick={(e) => e.stopPropagation()}>
                    <button className="btn btn--sm" onClick={() => openEdit(k)}>
                      编辑
                    </button>
                    <button className="btn btn--sm btn--danger" onClick={() => removeKb(k)}>
                      删除
                    </button>
                  </div>
                </td>
              </tr>
            ))}
            {kb.kbs.length === 0 && (
              <tr>
                <td colSpan={5} className="empty-state">
                  暂无知识库，点击右上角「新建知识库」开始。
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {selectedKb && (
        <div className="panel">
          <div className="page-head" style={{ marginBottom: 10 }}>
            <div className="page-head__left">
              <div className="page-title" style={{ fontSize: 17 }}>
                {selectedKb.name} · 文档
              </div>
            </div>
            <input ref={fileRef} type="file" accept=".pdf,.docx,.txt,.md" hidden onChange={onPickFile} />
            <button className="btn btn--primary" disabled={uploading} onClick={() => fileRef.current?.click()}>
              {uploading ? '上传中…' : '上传文档'}
            </button>
          </div>

          {docsLoading ? (
            <div className="empty-state">加载中…</div>
          ) : docs.length === 0 ? (
            <div className="empty-state">暂无文档，上传 pdf/docx/txt/md 文件入库。</div>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>文件名</th>
                  <th>类型</th>
                  <th>大小</th>
                  <th>状态</th>
                  <th>分块数</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {docs.map((d) => (
                  <tr key={d.id}>
                    <td style={{ wordBreak: 'break-word' }}>{d.filename}</td>
                    <td className="mono-cell">{d.file_type}</td>
                    <td className="mono-cell">{fmtSize(d.file_size)}</td>
                    <td>
                      <span className={`tag ${(STATUS_LABELS[d.status] || STATUS_LABELS.pending).cls}`}>
                        {(STATUS_LABELS[d.status] || STATUS_LABELS.pending).text}
                      </span>
                      {d.status === 'error' && d.error_msg ? (
                        <span className="text-danger" style={{ fontSize: 12, marginLeft: 6 }}>
                          {d.error_msg}
                        </span>
                      ) : null}
                    </td>
                    <td className="mono-cell">{d.chunk_count ?? 0}</td>
                    <td>
                      <div className="row-actions">
                        <a className="btn btn--sm" href={documentFileUrl(d.id)} target="_blank" rel="noreferrer">
                          预览
                        </a>
                        <button className="btn btn--sm btn--danger" onClick={() => removeDoc(d)}>
                          删除
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {formOpen && (
        <div className="modal-mask" onClick={() => setFormOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal__title">{editing ? '编辑知识库' : '新建知识库'}</div>
            <div className="form-row">
              <label className="form-label">名称</label>
              <input className="field" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
            </div>
            <div className="form-row">
              <label className="form-label">描述</label>
              <textarea className="field" rows={3} value={form.description} onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))} />
            </div>
            <div className="form-row">
              <label className="form-label">分块策略</label>
              <select
                className="field"
                value={form.chunk_strategy}
                onChange={(e) => setForm((f) => ({ ...f, chunk_strategy: e.target.value }))}
              >
                <option value="fixed">固定长度</option>
                <option value="recursive">递归拆分</option>
              </select>
            </div>
            <div className="form-row">
              <label className="form-label">检索构建块</label>
              <div className="filter-bar">
                {Object.entries(SWITCH_LABELS).map(([key, label]) => (
                  <label key={key} style={{ display: 'inline-flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
                    <span className="switch" style={{ width: 40, height: 22 }}>
                      <input type="checkbox" checked={form.strategies[key]} onChange={() => toggleSwitch(key)} />
                      <span className="switch__track">
                        <span className="switch__thumb" />
                      </span>
                    </span>
                    <span>{label}</span>
                  </label>
                ))}
              </div>
            </div>
            <div className="modal__actions">
              <button className="btn" onClick={() => setFormOpen(false)}>
                取消
              </button>
              <button className="btn btn--primary" disabled={saving} onClick={submit}>
                {saving ? '保存中…' : '保存'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}