import { useEffect, useRef, useState } from 'react'
import { marked } from 'marked'
import { useKbStore } from '../stores/kbStore'
import { createKb, updateKb, deleteKb } from '../api/kb'
import { listDocuments, uploadDocument, deleteDocument, documentFileUrl } from '../api/document'
import { message } from '../utils/toast'

const ALLOWED_EXT = ['.pdf', '.docx', '.txt', '.md']

// 与后端 documents.status 枚举保持一致
const STATUS_META = {
  pending: { text: '待处理', cls: 'tag--muted' },
  parsing: { text: '解析中', cls: 'tag--accent' },
  indexing: { text: '索引中', cls: 'tag--accent' },
  completed: { text: '已完成', cls: 'tag--success' },
  failed: { text: '失败', cls: 'tag--danger' },
}

function statusMeta(status) {
  return STATUS_META[status] || { text: status || '未知', cls: 'tag--muted' }
}

function formatSize(bytes) {
  if (bytes === null || bytes === undefined || bytes === '') return '—'
  const n = Number(bytes)
  if (Number.isNaN(n)) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(2)} MB`
}

function formatTime(value) {
  if (!value) return '—'
  return String(value).replace('T', ' ').replace(/\.\d+$/, '').slice(0, 19)
}

function isProcessing(status) {
  return ['pending', 'parsing', 'indexing'].includes(status)
}

function previewKind(fileType) {
  if (fileType === 'pdf') return 'pdf'
  if (fileType === 'md') return 'markdown'
  if (fileType === 'txt') return 'text'
  return 'unsupported'
}

const UploadIcon = () => (
  <svg viewBox="0 0 24 24" width="34" height="34" fill="none" stroke="currentColor" strokeWidth="1.6">
    <path d="M12 16V4m0 0 4 4m-4-4-4 4" strokeLinecap="round" strokeLinejoin="round" />
    <path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3" strokeLinecap="round" />
  </svg>
)

export default function KnowledgeBase() {
  const kbStore = useKbStore()

  const [detailKb, setDetailKb] = useState(null)
  const [documents, setDocuments] = useState([])
  const [docsLoading, setDocsLoading] = useState(false)
  const [uploading, setUploading] = useState(false)
  const pollTimer = useRef(null)
  const fileRef = useRef(null)
  const dragRef = useRef(null)

  const [createOpen, setCreateOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [createForm, setCreateForm] = useState({ name: '', description: '', chunk_strategy: 'recursive' })

  const [editOpen, setEditOpen] = useState(false)
  const [updating, setUpdating] = useState(false)
  const [editingId, setEditingId] = useState(null)
  const [editForm, setEditForm] = useState({ name: '', description: '', chunk_strategy: 'recursive' })

  const [togglingId, setTogglingId] = useState(null)

  const [previewOpen, setPreviewOpen] = useState(false)
  const [previewDoc, setPreviewDoc] = useState(null)
  const [previewText, setPreviewText] = useState('')
  const [previewLoading, setPreviewLoading] = useState(false)

  useEffect(() => {
    kbStore.fetchKbs()
    return stopPolling
  }, [])

  /* ---------------- 知识库列表 ---------------- */

  function openCreate() {
    setCreateForm({ name: '', description: '', chunk_strategy: 'recursive' })
    setCreateOpen(true)
  }

  async function submitCreate() {
    if (!createForm.name.trim()) {
      message.warning('请填写知识库名称')
      return
    }
    setCreating(true)
    try {
      await createKb({
        name: createForm.name.trim(),
        description: createForm.description.trim(),
        chunk_strategy: createForm.chunk_strategy,
      })
      message.success('知识库创建成功')
      setCreateOpen(false)
      await kbStore.fetchKbs()
    } catch (e) {
      /* 拦截器已提示 */
    } finally {
      setCreating(false)
    }
  }

  function openEdit(row) {
    setEditingId(row.id)
    setEditForm({
      name: row.name || '',
      description: row.description || '',
      chunk_strategy: row.chunk_strategy || 'recursive',
    })
    setEditOpen(true)
  }

  async function submitEdit() {
    if (!editForm.name.trim()) {
      message.warning('请填写知识库名称')
      return
    }
    setUpdating(true)
    try {
      await updateKb(editingId, {
        name: editForm.name.trim(),
        description: editForm.description.trim(),
        chunk_strategy: editForm.chunk_strategy,
      })
      message.success('知识库已更新')
      setEditOpen(false)
      await kbStore.fetchKbs()
      setDetailKb((cur) => (cur && cur.id === editingId ? { ...cur, ...editForm } : cur))
    } catch (e) {
      /* 拦截器已提示 */
    } finally {
      setUpdating(false)
    }
  }

  async function toggleActive(row, val) {
    setTogglingId(row.id)
    try {
      await updateKb(row.id, { is_active: val })
      message.success(val ? `已启用「${row.name}」` : `已停用「${row.name}」`)
      await kbStore.fetchKbs()
    } catch (e) {
      await kbStore.fetchKbs() // 失败时以后端状态为准
    } finally {
      setTogglingId(null)
    }
  }

  async function removeKb(row) {
    if (!window.confirm(`确定删除知识库「${row.name}」吗？其下的文档、向量索引与 BM25 索引都会被删除。`)) return
    try {
      await deleteKb(row.id)
      message.success('已删除知识库')
      kbStore.afterRemove(row.id)
      await kbStore.fetchKbs()
    } catch (e) {
      /* 拦截器已提示 */
    }
  }

  /* ---------------- 文档详情 ---------------- */

  async function openDetail(row) {
    setDetailKb(row)
    await fetchDocuments(row.id)
  }

  function closeDetail() {
    stopPolling()
    setDetailKb(null)
    setDocuments([])
  }

  async function fetchDocuments(kbId) {
    const id = kbId ?? detailKb?.id
    if (!id) return []
    setDocsLoading(true)
    try {
      const data = await listDocuments(id)
      const items = Array.isArray(data) ? data : data?.items ?? []
      setDocuments(items)
      if (items.some((d) => isProcessing(d.status))) startPolling(id)
      else stopPolling()
      return items
    } catch (e) {
      return []
    } finally {
      setDocsLoading(false)
    }
  }

  function startPolling(kbId) {
    stopPolling()
    pollTimer.current = setInterval(async () => {
      const data = await listDocuments(kbId)
      const items = Array.isArray(data) ? data : data?.items ?? []
      setDocuments(items)
      if (!items.some((d) => isProcessing(d.status))) stopPolling()
    }, 3000)
  }

  function stopPolling() {
    if (pollTimer.current) {
      clearInterval(pollTimer.current)
      pollTimer.current = null
    }
  }

  function checkFile(file) {
    const name = (file?.name || '').toLowerCase()
    return ALLOWED_EXT.some((ext) => name.endsWith(ext))
  }

  async function uploadFiles(files) {
    if (!detailKb || !files?.length) return
    setUploading(true)
    try {
      for (const file of files) {
        if (!checkFile(file)) {
          message.error('仅支持 .pdf / .docx / .txt / .md 四种格式')
          continue
        }
        const doc = await uploadDocument(detailKb.id, file)
        message.success(`「${file.name}」上传成功，正在解析入库`)
        if (doc?.id) {
          setDocuments((prev) => [doc, ...prev.filter((d) => d.id !== doc.id)])
        }
      }
      await fetchDocuments(detailKb.id)
    } catch (e) {
      /* 拦截器已提示 */
    } finally {
      setUploading(false)
    }
  }

  function onPickFiles(e) {
    const files = Array.from(e.target.files || [])
    e.target.value = ''
    uploadFiles(files)
  }

  function onDrop(e) {
    e.preventDefault()
    dragRef.current?.classList.remove('is-dragover')
    uploadFiles(Array.from(e.dataTransfer.files || []))
  }

  async function removeDoc(row) {
    if (!window.confirm(`确定删除文档「${row.filename}」吗？`)) return
    try {
      await deleteDocument(row.id)
      message.success('已删除文档')
      setDocuments((prev) => prev.filter((d) => d.id !== row.id))
      await fetchDocuments(detailKb?.id)
    } catch (e) {
      /* 拦截器已提示 */
    }
  }

  /* ---------------- 文档预览 ---------------- */

  function previewUrl(doc, download = false) {
    return doc ? documentFileUrl(doc.id, download) : ''
  }

  async function openPreview(row) {
    setPreviewDoc(row)
    setPreviewText('')
    setPreviewOpen(true)
    const kind = previewKind(row.file_type)
    if (kind !== 'text' && kind !== 'markdown') return
    setPreviewLoading(true)
    try {
      const resp = await fetch(documentFileUrl(row.id))
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      setPreviewText(await resp.text())
    } catch (e) {
      setPreviewText('')
      message.error('文档内容读取失败，可尝试下载后查看')
    } finally {
      setPreviewLoading(false)
    }
  }

  function downloadDoc() {
    if (!previewDoc) return
    const a = document.createElement('a')
    a.href = documentFileUrl(previewDoc.id, true)
    a.download = previewDoc.filename || ''
    document.body.appendChild(a)
    a.click()
    a.remove()
  }

  const isMarkdown = previewDoc ? previewKind(previewDoc.file_type) === 'markdown' : false
  const previewHtml = isMarkdown
    ? (() => {
        try {
          return marked.parse(previewText || '', { gfm: true, breaks: true })
        } catch (e) {
          return ''
        }
      })()
    : ''
  const hasFailed = documents.some((d) => d.status === 'failed')

  /* ---------------- 渲染 ---------------- */

  return (
    <div className="page">
      {!detailKb ? (
        <>
          <div className="page-head">
            <div className="page-head__left">
              <div className="page-title">知识库</div>
              <div className="page-subtitle">管理知识库及其文档，支持 pdf / docx / txt / md 四种格式</div>
            </div>
            <button className="btn btn--primary" onClick={openCreate}>
              新建知识库
            </button>
          </div>

          <div className="panel">
            <table className="data-table">
              <thead>
                <tr>
                  <th style={{ minWidth: 160 }}>名称</th>
                  <th style={{ minWidth: 220 }}>描述</th>
                  <th style={{ width: 130 }}>分块策略</th>
                  <th style={{ width: 100, textAlign: 'center' }}>启用</th>
                  <th style={{ width: 180 }}>创建时间</th>
                  <th style={{ width: 300, textAlign: 'right' }}>操作</th>
                </tr>
              </thead>
              <tbody>
                {kbStore.kbs.map((row) => (
                  <tr key={row.id}>
                    <td style={{ fontWeight: 600 }}>{row.name}</td>
                    <td>
                      <span className="muted">{row.description || '—'}</span>
                    </td>
                    <td>
                      <span className="tag">{row.chunk_strategy || 'recursive'}</span>
                    </td>
                    <td style={{ textAlign: 'center' }}>
                      <span className="switch">
                        <input
                          type="checkbox"
                          checked={!!row.is_active}
                          disabled={togglingId === row.id}
                          onChange={(e) => toggleActive(row, e.target.checked)}
                        />
                        <span className="switch__track">
                          <span className="switch__thumb" />
                        </span>
                      </span>
                    </td>
                    <td className="mono-cell">{formatTime(row.created_at)}</td>
                    <td>
                      <div className="row-actions" style={{ justifyContent: 'flex-end' }}>
                        <button className="btn btn--sm btn--link" onClick={() => openDetail(row)}>
                          进入详情
                        </button>
                        <button className="btn btn--sm btn--link" onClick={() => openEdit(row)}>
                          修改
                        </button>
                        <button className="btn btn--sm btn--link btn--link-danger" onClick={() => removeKb(row)}>
                          删除
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
                {kbStore.kbs.length === 0 && (
                  <tr>
                    <td colSpan={6}>
                      <div className="table-empty">暂无知识库，点击右上角「新建知识库」创建</div>
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <>
          <div className="page-head">
            <div className="page-head__left">
              <div className="detail-crumb">
                <button className="btn btn--sm btn--link" onClick={closeDetail}>
                  ← 返回列表
                </button>
              </div>
              <div className="page-title">{detailKb.name}</div>
              <div className="page-subtitle">
                分块策略 {detailKb.chunk_strategy || 'recursive'} · {detailKb.description || '暂无描述'}
              </div>
            </div>
            <input ref={fileRef} type="file" accept=".pdf,.docx,.txt,.md" multiple hidden onChange={onPickFiles} />
            <button className="btn btn--primary" disabled={uploading} onClick={() => fileRef.current?.click()}>
              {uploading ? '上传中…' : '上传文档'}
            </button>
          </div>

          <div className="panel upload-panel">
            <div
              ref={dragRef}
              className="upload-zone"
              onClick={() => fileRef.current?.click()}
              onDragOver={(e) => {
                e.preventDefault()
                dragRef.current?.classList.add('is-dragover')
              }}
              onDragLeave={() => dragRef.current?.classList.remove('is-dragover')}
              onDrop={onDrop}
            >
              <div className="upload-panel__icon">
                <UploadIcon />
              </div>
              <div className="upload-panel__text">
                将文档拖到此处，或<em>点击选择文件</em>
              </div>
              <div className="upload-panel__sub">支持 .pdf / .docx / .txt / .md，可一次拖入多个文件</div>
            </div>
            <div className="upload-tip">上传后系统自动解析并建立索引，处理状态每 3 秒自动刷新。</div>
          </div>

          <div className="panel">
            <table className="data-table">
              <thead>
                <tr>
                  <th style={{ minWidth: 220 }}>文件名</th>
                  <th style={{ width: 90 }}>类型</th>
                  <th style={{ width: 110 }}>大小</th>
                  <th style={{ width: 110 }}>状态</th>
                  <th style={{ width: 90 }}>分块数</th>
                  <th style={{ width: 180 }}>创建时间</th>
                  <th style={{ width: 160, textAlign: 'right' }}>操作</th>
                </tr>
              </thead>
              <tbody>
                {docsLoading && documents.length === 0 && (
                  <tr>
                    <td colSpan={7}>
                      <div className="table-empty">加载中…</div>
                    </td>
                  </tr>
                )}
                {!docsLoading && documents.length === 0 && (
                  <tr>
                    <td colSpan={7}>
                      <div className="table-empty">暂无文档，点击右上角「上传文档」添加</div>
                    </td>
                  </tr>
                )}
                {documents.map((row) => (
                  <tr key={row.id}>
                    <td>
                      <span className="file-link" title={row.filename} onClick={() => openPreview(row)}>
                        {row.filename}
                      </span>
                    </td>
                    <td>
                      <span className="tag">{row.file_type}</span>
                    </td>
                    <td className="mono-cell">{formatSize(row.file_size)}</td>
                    <td>
                      <span className={`tag ${statusMeta(row.status).cls}`}>{statusMeta(row.status).text}</span>
                    </td>
                    <td className="mono-cell">{row.chunk_count ?? 0}</td>
                    <td className="mono-cell">{formatTime(row.created_at)}</td>
                    <td>
                      <div className="row-actions" style={{ justifyContent: 'flex-end' }}>
                        <button className="btn btn--sm btn--link" onClick={() => openPreview(row)}>
                          查看
                        </button>
                        <button className="btn btn--sm btn--link btn--link-danger" onClick={() => removeDoc(row)}>
                          删除
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            {hasFailed && (
              <div className="error-note">部分文档处理失败，可在「操作」列删除后重新上传。</div>
            )}
          </div>
        </>
      )}

      {createOpen && (
        <div className="modal-mask" onClick={() => setCreateOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal__title">新建知识库</div>
            <div className="form-row">
              <label className="form-label">名称</label>
              <input
                className="field"
                placeholder="如：法律领域"
                maxLength={100}
                value={createForm.name}
                onChange={(e) => setCreateForm((f) => ({ ...f, name: e.target.value }))}
              />
            </div>
            <div className="form-row">
              <label className="form-label">描述</label>
              <textarea
                className="field"
                rows={3}
                placeholder="简要描述该知识库的领域与用途"
                value={createForm.description}
                onChange={(e) => setCreateForm((f) => ({ ...f, description: e.target.value }))}
              />
            </div>
            <div className="form-row">
              <label className="form-label">分块策略</label>
              <select
                className="field"
                value={createForm.chunk_strategy}
                onChange={(e) => setCreateForm((f) => ({ ...f, chunk_strategy: e.target.value }))}
              >
                <option value="recursive">recursive（递归分块，推荐）</option>
                <option value="fixed">fixed（固定长度分块）</option>
              </select>
            </div>
            <div className="modal__actions">
              <button className="btn" onClick={() => setCreateOpen(false)}>
                取消
              </button>
              <button className="btn btn--primary" disabled={creating} onClick={submitCreate}>
                {creating ? '创建中…' : '创建'}
              </button>
            </div>
          </div>
        </div>
      )}

      {editOpen && (
        <div className="modal-mask" onClick={() => setEditOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal__title">修改知识库</div>
            <div className="form-row">
              <label className="form-label">名称</label>
              <input
                className="field"
                placeholder="如：法律领域"
                maxLength={100}
                value={editForm.name}
                onChange={(e) => setEditForm((f) => ({ ...f, name: e.target.value }))}
              />
            </div>
            <div className="form-row">
              <label className="form-label">描述</label>
              <textarea
                className="field"
                rows={3}
                placeholder="简要描述该知识库的领域与用途"
                value={editForm.description}
                onChange={(e) => setEditForm((f) => ({ ...f, description: e.target.value }))}
              />
            </div>
            <div className="form-row">
              <label className="form-label">分块策略</label>
              <select
                className="field"
                value={editForm.chunk_strategy}
                onChange={(e) => setEditForm((f) => ({ ...f, chunk_strategy: e.target.value }))}
              >
                <option value="recursive">recursive（递归分块，推荐）</option>
                <option value="fixed">fixed（固定长度分块）</option>
              </select>
            </div>
            <div className="modal__actions">
              <button className="btn" onClick={() => setEditOpen(false)}>
                取消
              </button>
              <button className="btn btn--primary" disabled={updating} onClick={submitEdit}>
                {updating ? '保存中…' : '保存'}
              </button>
            </div>
          </div>
        </div>
      )}

      {previewOpen && previewDoc && (
        <div className="modal-mask" onClick={() => setPreviewOpen(false)}>
          <div className="modal modal--wide" onClick={(e) => e.stopPropagation()}>
            <div className="modal__title">{previewDoc.filename || '文档预览'}</div>

            <div className="preview-meta">
              <span className="tag">{previewDoc.file_type}</span>
              <span>{formatSize(previewDoc.file_size)}</span>
              <span>{formatTime(previewDoc.created_at)}</span>
            </div>

            <div className="preview-body">
              {previewKind(previewDoc.file_type) === 'pdf' && (
                <iframe src={previewUrl(previewDoc)} className="preview-frame" title="PDF 预览" />
              )}
              {isMarkdown && <div className="preview-text markdown-body" dangerouslySetInnerHTML={{ __html: previewHtml }} />}
              {previewKind(previewDoc.file_type) === 'text' && (
                <pre className="preview-text preview-pre">{previewLoading ? '加载中…' : previewText}</pre>
              )}
              {previewKind(previewDoc.file_type) === 'unsupported' && (
                <div className="preview-unsupported">
                  <div className="preview-unsupported__icon">📄</div>
                  <div>该格式（{previewDoc.file_type}）暂不支持在线预览，请下载后用本地软件打开。</div>
                </div>
              )}
            </div>

            <div className="modal__actions">
              <button className="btn" onClick={() => window.open(previewUrl(previewDoc), '_blank')}>
                新窗口打开
              </button>
              <button className="btn btn--primary" onClick={downloadDoc}>
                下载
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
