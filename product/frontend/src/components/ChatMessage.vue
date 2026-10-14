<template>
  <div v-if="message.role === 'user'" class="msg msg--user">
    <div class="bubble bubble--user">
      <img
        v-if="message.image"
        class="bubble-image"
        :src="message.image"
        alt="用户上传的图片"
      />
      <div v-if="message.content">{{ message.content }}</div>
    </div>
  </div>

  <div v-else class="msg msg--assistant">
    <div class="msg-role">回答</div>
    <div
      ref="bodyEl"
      class="bubble bubble--assistant markdown-body"
      v-html="renderedHtml"
    ></div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { marked } from 'marked'

const props = defineProps({
  message: { type: Object, required: true },
})
const emit = defineEmits(['cite'])

const bodyEl = ref(null)

const renderedHtml = computed(() => {
  const content = props.message.content || ''
  if (!content) {
    return props.message.streaming ? '<span class="stream-cursor">▌</span>' : ''
  }
  let html
  try {
    html = marked.parse(content, { gfm: true, breaks: true })
  } catch (e) {
    html = escapeHtml(content)
  }
  html = decorateCitations(html)
  if (props.message.streaming) html += '<span class="stream-cursor">▌</span>'
  return html
})

// 把 [1][2,3][1-3][1、2] 这类引用编号转成可点击的 span
function decorateCitations(html) {
  const re = /\[(\d+(?:\s*[-–,，、]\s*\d+)*)\](?!\()/g
  return html.replace(re, (full, group) => {
    const indices = expandIndices(group)
    if (!indices.length) return full
    return indices
      .map((n) => `<span class="cite-ref" data-index="${n}">[${n}]</span>`)
      .join('<span class="cite-sep">,</span>')
  })
}

function expandIndices(group) {
  const result = []
  for (const part of group.split(/\s*[,，、]\s*/)) {
    const range = part.split(/\s*[-–]\s*/)
    if (range.length === 2) {
      const start = parseInt(range[0], 10)
      const end = parseInt(range[1], 10)
      if (!Number.isNaN(start) && !Number.isNaN(end)) {
        for (let i = start; i <= end && i <= start + 50; i += 1) result.push(i)
        continue
      }
    }
    const n = parseInt(part, 10)
    if (!Number.isNaN(n)) result.push(n)
  }
  return [...new Set(result)]
}

function escapeHtml(text) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\n/g, '<br />')
}

function onClick(event) {
  const target = event.target.closest('.cite-ref')
  if (target && target.dataset.index) {
    emit('cite', Number(target.dataset.index))
  }
}

onMounted(() => bodyEl.value?.addEventListener('click', onClick))
onBeforeUnmount(() => bodyEl.value?.removeEventListener('click', onClick))
</script>

<style scoped>
.msg {
  margin: 14px 0;
}

.msg--user {
  display: flex;
  justify-content: flex-end;
}

.bubble {
  border-radius: 6px;
  max-width: 82%;
  word-break: break-word;
}

.bubble--user {
  background-color: #f5f0e6;
  border: 1px solid var(--app-border);
  padding: 10px 16px;
  font-size: 15px;
  line-height: 1.7;
  color: var(--app-text);
  white-space: pre-wrap;
}

.bubble-image {
  display: block;
  max-width: 100%;
  max-height: 260px;
  border-radius: 4px;
  border: 1px solid var(--app-border);
  margin-bottom: 8px;
}

.bubble-image:last-child {
  margin-bottom: 0;
}

.bubble--assistant {
  max-width: 100%;
  padding: 2px 2px 2px 16px;
  border-left: 3px solid var(--app-accent);
}

.msg-role {
  font-size: 12px;
  color: var(--app-text-muted);
  letter-spacing: 0.04em;
  margin-bottom: 4px;
}
</style>
