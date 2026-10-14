<template>
  <el-collapse class="retrieval-detail">
    <el-collapse-item name="detail">
      <template #title>
        <span class="rd-title">检索详情</span>
        <span class="rd-title-sub">Top-{{ docs.length }} 文档块 · 两路召回得分</span>
      </template>

      <div class="rd-docs">
        <div v-for="(doc, i) in docs" :key="doc.chunk_id || i" class="rd-doc">
          <div class="rd-doc__head">
            <span class="rd-rank">{{ doc.rank || i + 1 }}.</span>
            <span class="rd-source">{{ doc.source || '未知来源' }}</span>
            <span v-if="pageText(doc)" class="rd-page">第 {{ pageText(doc) }} 页</span>
          </div>
          <div class="rd-scores">
            <span v-if="num(doc.score) !== null">最终 {{ fmt(doc.score) }}</span>
            <span v-if="num(doc.fused_score) !== null">融合 {{ fmt(doc.fused_score, 5) }}</span>
            <span v-if="num(doc.rerank_score) !== null">精排 {{ fmt(doc.rerank_score) }}</span>
            <span v-if="matchedText(doc)">命中 {{ matchedText(doc) }}</span>
          </div>
          <div class="rd-text">{{ preview(doc.text) }}</div>
        </div>
      </div>

      <div v-if="timingRows.length" class="rd-block">
        <div class="rd-block__title">耗时分解</div>
        <div class="rd-lines">
          <div v-for="row in timingRows" :key="row.label" class="rd-line">
            {{ row.label }} {{ row.value }}
          </div>
        </div>
      </div>

      <div v-if="statsRows.length" class="rd-block">
        <div class="rd-block__title">两路召回统计</div>
        <div class="rd-lines">
          <div v-for="row in statsRows" :key="row.label" class="rd-line">
            {{ row.label }} {{ row.value }}
          </div>
        </div>
      </div>
    </el-collapse-item>
  </el-collapse>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  docs: { type: Array, default: () => [] },
  latency: { type: Object, default: () => ({}) },
  stats: { type: Object, default: () => null },
  strategy: { type: String, default: '' },
})

function num(v) {
  return typeof v === 'number' && !Number.isNaN(v) ? v : null
}

function fmt(v, digits = 4) {
  const n = num(v)
  return n === null ? '-' : n.toFixed(digits)
}

function pageText(doc) {
  const p = doc.page
  return p === undefined || p === null || p === '' ? '' : String(p)
}

function matchedText(doc) {
  const m = doc.matched_by
  if (Array.isArray(m) && m.length) return m.join('+')
  return ''
}

function preview(text) {
  const t = String(text || '')
  return t.length > 180 ? `${t.slice(0, 180)}…` : t
}

const TIMING_LABELS = {
  recall_ms: '召回',
  retrieve_ms: '检索',
  rerank_ms: '精排',
  generate_ms: '生成',
  total_ms: '总计',
}

const timingRows = computed(() => {
  const rows = []
  for (const [key, label] of Object.entries(TIMING_LABELS)) {
    const v = props.latency?.[key]
    if (typeof v === 'number' && v > 0) {
      rows.push({ label, value: `${(v / 1000).toFixed(2)}s` })
    }
  }
  return rows
})

const STATS_LABELS = {
  dense: '向量召回',
  sparse: 'BM25 召回',
  overlap: '两路重叠',
  dense_only: '仅向量',
  sparse_only: '仅 BM25',
}

const statsRows = computed(() => {
  const s = props.stats
  if (!s || typeof s !== 'object') return []
  const rows = []
  for (const [key, label] of Object.entries(STATS_LABELS)) {
    if (s[key] !== undefined && s[key] !== null && s[key] !== '') {
      rows.push({ label, value: String(s[key]) })
    }
  }
  return rows
})
</script>

<style scoped>
.retrieval-detail {
  margin-top: 8px;
  border: none;
}

.rd-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--app-text-secondary);
}

.rd-title-sub {
  margin-left: 10px;
  font-size: 12px;
  color: var(--app-text-muted);
  font-weight: 400;
}

.rd-docs {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.rd-doc {
  border: 1px solid var(--app-border);
  border-radius: 4px;
  padding: 10px 12px;
  background-color: #ffffff;
}

.rd-doc__head {
  font-size: 13px;
  font-weight: 600;
  color: var(--app-text);
}

.rd-rank {
  color: var(--app-accent);
  margin-right: 4px;
}

.rd-page {
  margin-left: 8px;
  font-size: 12px;
  color: var(--app-text-muted);
  font-weight: 400;
}

.rd-scores {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 5px;
  font-family: var(--app-mono);
  font-size: 12px;
  color: var(--app-text-secondary);
}

.rd-text {
  margin-top: 6px;
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--app-text-regular);
  white-space: pre-wrap;
  word-break: break-word;
}

.rd-block {
  margin-top: 14px;
}

.rd-block__title {
  font-size: 12px;
  font-weight: 600;
  color: var(--app-text-secondary);
  margin-bottom: 6px;
}

.rd-lines {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.rd-line {
  font-family: var(--app-mono);
  font-size: 12px;
  color: var(--app-text-secondary);
  line-height: 1.8;
}
</style>
