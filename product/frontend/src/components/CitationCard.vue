<template>
  <div :id="id" class="citation-card">
    <div class="cc-head">
      <span class="cc-index">[{{ index }}]</span>
      <span class="cc-file">{{ citation.source || '未知来源' }}</span>
      <span v-if="pageText" class="cc-meta">第 {{ pageText }} 页</span>
      <span v-if="scoreText" class="cc-meta">相关度 {{ scoreText }}</span>
      <span v-if="citation.count > 1" class="cc-meta">引用 {{ citation.count }} 次</span>
    </div>
    <div v-if="citation.text" class="cc-text">{{ citation.text }}</div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  citation: { type: Object, required: true },
  index: { type: Number, default: 1 },
  id: { type: String, default: '' },
})

const pageText = computed(() => {
  const p = props.citation.page
  return p === undefined || p === null || p === '' ? '' : String(p)
})

const scoreText = computed(() => {
  const s = props.citation.score
  return typeof s === 'number' ? s.toFixed(4) : ''
})
</script>

<style scoped>
.citation-card {
  border: 1px solid var(--app-border);
  border-left: 3px solid var(--app-accent);
  border-radius: 4px;
  padding: 10px 14px;
  margin-bottom: 10px;
  background-color: var(--app-surface-soft);
}

.cc-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 13px;
}

.cc-index {
  color: var(--app-accent);
  font-weight: 700;
}

.cc-file {
  color: var(--app-text);
  font-weight: 600;
}

.cc-meta {
  color: var(--app-text-muted);
  font-size: 12px;
}

.cc-text {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed var(--app-border);
  font-family: var(--app-mono);
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--app-text-regular);
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
