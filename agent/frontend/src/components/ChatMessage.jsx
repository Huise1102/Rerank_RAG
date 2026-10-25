import { useMemo } from 'react'
import { marked } from 'marked'

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

function expandIndices(group) {
  const out = []
  for (const part of group) {
    const m = part.match(/^(\d+)-(\d+)$/)
    if (m) {
      const a = parseInt(m[1], 10)
      const b = parseInt(m[2], 10)
      for (let i = a; i <= b && i <= a + 50; i += 1) out.push(i)
    } else if (/^\d+$/.test(part)) {
      out.push(parseInt(part, 10))
    }
  }
  return out
}

// 把 [1] / [2,3] / [1-3] 形式的引用编号转成可点击的 .cite-ref
function decorateCitations(html) {
  return html.replace(
    /\[(\d+(?:\s*-\s*\d+)?(?:\s*,\s*\d+(?:\s*-\s*\d+)?)*)\]/g,
    (whole, inner) => {
      const indices = expandIndices(inner.split(',').map((s) => s.trim()))
      if (!indices.length) return whole
      const label = inner.replace(/\s+/g, '')
      const titleText = [...new Set(indices)].slice(0, 10).join(', ') + (indices.length > 10 ? ' …' : '')
      return `<span class="cite-ref" data-indices="${[...new Set(indices)].join(',')}" title="引用来源 ${titleText}">[${label}]</span>`
    }
  )
}

export default function ChatMessage({ message, onCite }) {
  const content = message.content || ''
  const html = useMemo(() => {
    if (!content) {
      return message.streaming ? '<span class="stream-cursor">▋</span>' : ''
    }
    let out = ''
    try {
      out = marked.parse(content, { gfm: true, breaks: true })
    } catch (e) {
      out = escapeHtml(content)
    }
    out = decorateCitations(out)
    if (message.streaming) out += '<span class="stream-cursor">▋</span>'
    return out
  }, [content, message.streaming])

  function handleClick(e) {
    const el = e.target.closest('.cite-ref')
    if (!el) return
    const raw = el.getAttribute('data-indices') || ''
    const indices = raw.split(',').filter(Boolean).map(Number)
    onCite?.(indices)
  }

  if (message.role === 'user') {
    return (
      <div className="msg msg--user">
        <div className="bubble bubble--user">
          {message.image ? (
            <img className="bubble-image" src={message.image} alt="用户上传的图片" />
          ) : null}
          {content ? <div>{content}</div> : null}
        </div>
      </div>
    )
  }

  return (
    <div className="msg msg--assistant">
      <div className="msg-role">回答</div>
      <div
        className="bubble bubble--assistant markdown-body"
        dangerouslySetInnerHTML={{ __html: html }}
        onClick={handleClick}
      />
    </div>
  )
}