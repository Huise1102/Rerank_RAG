<template>
  <div class="answer-body" @click="handleClick" v-html="html"></div>
</template>

<script>
import { marked } from 'marked'
import DOMPurify from 'dompurify'

marked.setOptions({ gfm: true, breaks: true })

function expandIndices(group) {
  const result = []
  for (const part of group) {
    const range = part.match(/^(\d+)-(\d+)$/)
    if (range) {
      const start = Number(range[1])
      const end = Math.min(Number(range[2]), start + 50)
      for (let index = start; index <= end; index += 1) {
        result.push(index)
      }
    } else if (/^\d+$/.test(part)) {
      result.push(Number(part))
    }
  }
  return result
}

function decorateCitations(html, citations) {
  const maxIndex = citations.length
  if (!maxIndex) {
    return html
  }
  const valid = new Set(citations.map((item, position) => item.index ?? position + 1))
  return html.replace(
    /\[(\d+(?:\s*-\s*\d+)?(?:\s*,\s*\d+(?:\s*-\s*\d+)?)*)\]/g,
    (whole, inner) => {
      const indices = [...new Set(expandIndices(inner.split(',').map((item) => item.trim())))]
      if (!indices.length || indices.every((index) => index > maxIndex)) {
        return whole
      }
      const invalid = indices.every((index) => !valid.has(index))
      const className = invalid ? 'cite-ref cite-ref--invalid' : 'cite-ref'
      const title = invalid ? `编号 ${indices.join(', ')} 没有对应的来源卡片` : '点击定位引用来源'
      const label = inner.replace(/\s+/g, '')
      return `<span class="${className}" data-indices="${indices.join(',')}" title="${title}">[${label}]</span>`
    }
  )
}

export default {
  name: 'AnswerContent',
  props: {
    content: { type: String, default: '' },
    citations: { type: Array, default: () => [] }
  },
  emits: ['cite'],
  computed: {
    html() {
      const source = this.content || ''
      if (!source) {
        return ''
      }
      let rendered = ''
      try {
        rendered = marked.parse(source)
      } catch (error) {
        rendered = source.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      }
      rendered = decorateCitations(rendered, this.citations)
      return DOMPurify.sanitize(rendered, { ADD_ATTR: ['title', 'data-indices'] })
    }
  },
  methods: {
    handleClick(event) {
      const target = event.target.closest('.cite-ref')
      if (!target) {
        return
      }
      const raw = target.getAttribute('data-indices') || ''
      const indices = raw.split(',').filter(Boolean).map(Number)
      if (indices.length) {
        this.$emit('cite', indices)
      }
    }
  }
}
</script>
