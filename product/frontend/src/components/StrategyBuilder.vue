<template>
  <div class="strategy-builder">
    <!-- 只读展示（问答页顶部 / 策略页摘要） -->
    <div v-if="readonly" class="sb-readonly">
      <span class="sb-tag" :class="{ 'is-off': !model.vector }">向量</span>
      <span class="sb-tag" :class="{ 'is-off': !model.bm25 }">BM25</span>
      <span class="sb-tag" :class="{ 'is-off': !model.fusion }">融合</span>
      <span class="sb-tag" :class="{ 'is-off': !model.rerank }">精排</span>
      <span class="sb-combo">→ {{ comboLabel }}</span>
    </div>

    <!-- 可编辑：4 个构建块开关 -->
    <div v-else class="sb-list">
      <div v-for="block in blocks" :key="block.key" class="sb-row">
        <div class="sb-row__info">
          <div class="sb-row__name">{{ block.name }}</div>
          <div class="sb-row__desc">{{ block.desc }}</div>
        </div>
        <el-switch
          :model-value="model[block.key]"
          :disabled="block.key === 'fusion' && !canFuse"
          @change="(v) => toggle(block.key, v)"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  modelValue: { type: Object, required: true },
  readonly: { type: Boolean, default: false },
})

const emit = defineEmits(['update:modelValue'])

const blocks = [
  {
    key: 'vector',
    name: '向量召回',
    desc: 'BGE 稠密向量，语义相似匹配',
  },
  {
    key: 'bm25',
    name: 'BM25 召回',
    desc: '关键词频率检索，精确匹配强',
  },
  {
    key: 'fusion',
    name: 'RRF 融合（k=60）',
    desc: '双路召回按名次融合，仅双路同时开启才有意义',
  },
  {
    key: 'rerank',
    name: '精排 Rerank',
    desc: 'bge-reranker-v2-m3 交叉编码精排',
  },
]

const model = computed(() => props.modelValue)

const canFuse = computed(() => model.value.vector && model.value.bm25)

const comboLabel = computed(() => describeStrategies(model.value))

function toggle(key, value) {
  const next = { ...model.value, [key]: value }
  // 融合仅在两路召回同时开启时有意义，关闭任一路时自动关闭融合
  if ((key === 'vector' || key === 'bm25') && !value) {
    next.fusion = false
  }
  emit('update:modelValue', next)
}

// 构建块组合 → 人类可读的策略名
function describeStrategies(s) {
  const vector = !!s.vector
  const bm25 = !!s.bm25
  const rerank = !!s.rerank
  const bothRecall = vector && bm25

  if (!vector && !bm25) return '未启用召回'
  if (bothRecall) return rerank ? '混合 + Rerank' : '混合'
  const base = vector ? '纯向量' : '纯 BM25'
  return rerank ? `${base} + Rerank` : base
}
</script>

<style scoped>
.strategy-builder {
  width: 100%;
}

.sb-readonly {
  display: inline-flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  font-size: 13px;
}

.sb-tag {
  padding: 1px 8px;
  border-radius: 3px;
  border: 1px solid var(--app-accent);
  background-color: var(--app-accent-soft);
  color: var(--app-accent-dark);
  font-size: 12px;
  font-weight: 600;
}

.sb-tag.is-off {
  border-color: var(--app-border-strong);
  background-color: var(--app-surface-soft);
  color: var(--app-text-muted);
  font-weight: 500;
  text-decoration: line-through;
}

.sb-combo {
  color: var(--app-text-secondary);
  font-weight: 600;
}

.sb-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.sb-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 12px 0;
  border-bottom: 1px solid var(--app-border);
}

.sb-row:last-child {
  border-bottom: none;
}

.sb-row__name {
  font-size: 14px;
  font-weight: 600;
  color: var(--app-text);
}

.sb-row__desc {
  margin-top: 3px;
  font-size: 12px;
  color: var(--app-text-muted);
}
</style>
