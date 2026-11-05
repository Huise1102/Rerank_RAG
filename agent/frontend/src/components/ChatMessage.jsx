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

// 把 [1] / [2,3] / [1-3] 形式的证据引用转成可点击的 .cite-ref。
// maxIndex 为本次累积证据条数：整组编号都超出范围时（通常是法条条号，如 [38]）按普通文字显示。
// validIndices 为「引用来源」里真实存在的编号；范围内但缺卡片时标成越界引用。
function decorateCitations(html, validIndices, maxIndex) {
  // 证据条数未知（流式中 / 本轮未检索）时不解释方括号数字
  if (!maxIndex) return html
  return html.replace(
    /\[(\d+(?:\s*-\s*\d+)?(?:\s*,\s*\d+(?:\s*-\s*\d+)?)*)\]/g,
    (whole, inner) => {
      const indices = expandIndices(inner.split(',').map((s) => s.trim()))
      if (!indices.length) return whole
      const unique = [...new Set(indices)]
      if (unique.every((n) => n > maxIndex)) return whole
      const label = inner.replace(/\s+/g, '')
      const invalid = validIndices && unique.every((n) => !validIndices.has(n))
      const titleText = unique.slice(0, 10).join(', ') + (unique.length > 10 ? ' …' : '')
      const cls = invalid ? 'cite-ref cite-ref--invalid' : 'cite-ref'
      const title = invalid
        ? `编号 ${titleText} 没有对应的来源卡片（模型越界引用）`
        : `点击查看引用来源 ${titleText}`
      return `<span class="${cls}" data-indices="${unique.join(',')}" title="${title}">[${label}]</span>`
    }
  )
}

export default function ChatMessage({ message, onCite, citations, evidenceCount }) {
  const content = message.content || ''
  const maxIndex = evidenceCount || 0
  const validIndices = useMemo(() => {
    if (!citations?.length) return null
    return new Set(citations.map((c, i) => c.index ?? i + 1))
  }, [citations])
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
    out = decorateCitations(out, validIndices, maxIndex)
    if (message.streaming) out += '<span class="stream-cursor">▋</span>'
    return out
  }, [content, message.streaming, validIndices, maxIndex])

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