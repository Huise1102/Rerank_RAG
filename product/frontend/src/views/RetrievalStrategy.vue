<template>
  <div class="page">
    <div class="page-head">
      <div class="page-head__left">
        <div class="page-title">检索策略</div>
        <div class="page-subtitle">用四个构建块开关自由组合检索链路，保存后对该知识库立即生效</div>
      </div>
    </div>

    <div class="panel">
      <div class="kb-picker">
        <span class="picker-label">选择知识库</span>
        <el-select
          v-model="kbStore.currentKbId"
          placeholder="请选择知识库"
          style="width: 260px"
        >
          <el-option v-for="kb in kbStore.kbs" :key="kb.id" :label="kb.name" :value="kb.id" />
        </el-select>
        <span v-if="kbStore.currentKb" class="picker-combo">
          当前组合：<b>{{ comboLabel }}</b>
        </span>
      </div>

      <el-empty v-if="!kbStore.currentKb" description="暂无知识库，请先在「知识库」页创建" />
    </div>

    <template v-if="kbStore.currentKb">
      <div class="panel">
        <div class="section-label">构建块开关</div>
        <StrategyBuilder v-model="strategies" />
        <div class="save-bar">
          <el-button type="primary" :loading="saving" @click="save">保存策略</el-button>
          <el-button @click="resetForm">重置</el-button>
        </div>
      </div>

      <div class="panel two-col">
        <div>
          <div class="section-label">组合说明（只读）</div>
          <ul class="combo-list">
            <li v-for="item in combos" :key="item.name">
              <span class="combo-name">{{ item.name }}</span>
              <span class="combo-arrow">→</span>
              <span class="combo-result">{{ item.result }}</span>
            </li>
          </ul>
        </div>
        <div>
          <div class="section-label">链路参数（只读）</div>
          <ul class="param-list">
            <li v-for="p in params" :key="p.label">
              <span class="param-label">{{ p.label }}</span>
              <span class="param-value">{{ p.value }}</span>
            </li>
          </ul>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useKbStore, normalizeStrategies, DEFAULT_STRATEGIES } from '@/stores/kb'
import { updateStrategies } from '@/api/kb'
import StrategyBuilder from '@/components/StrategyBuilder.vue'

const kbStore = useKbStore()

const strategies = ref({ ...DEFAULT_STRATEGIES })
const saving = ref(false)

const combos = [
  { name: '仅 向量', result: '纯向量检索' },
  { name: '仅 BM25', result: '纯 BM25 检索' },
  { name: '向量 + BM25 + 融合', result: '混合检索（RRF）' },
  { name: '全部开启', result: '混合检索 + Rerank 精排' },
]

const params = [
  { label: '双路召回', value: '各 Top-50' },
  { label: 'RRF 融合', value: 'k = 60' },
  { label: '候选池', value: '20' },
  { label: '精排保留', value: 'Top-5' },
  { label: '上下文预算', value: '6000 字' },
]

const comboLabel = computed(() => {
  const s = strategies.value
  const bothRecall = s.vector && s.bm25
  if (!s.vector && !s.bm25) return '未启用召回'
  if (bothRecall) return s.rerank ? '混合 + Rerank' : '混合'
  const base = s.vector ? '纯向量' : '纯 BM25'
  return s.rerank ? `${base} + Rerank` : base
})

function loadFromCurrent() {
  const raw = kbStore.currentKb?.enabled_strategies
  strategies.value = raw ? { ...normalizeStrategies(raw) } : { ...DEFAULT_STRATEGIES }
}

watch(() => kbStore.currentKbId, loadFromCurrent)

onMounted(async () => {
  if (!kbStore.kbs.length) await kbStore.fetchKbs()
  loadFromCurrent()
})

function resetForm() {
  loadFromCurrent()
  ElMessage.info('已重置为当前生效的策略')
}

async function save() {
  if (!kbStore.currentKbId) return
  saving.value = true
  try {
    await updateStrategies(kbStore.currentKbId, { ...strategies.value })
    ElMessage.success('检索策略保存成功')
    await kbStore.fetchKbs()
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.kb-picker {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.picker-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--app-text-secondary);
}

.picker-combo {
  font-size: 13px;
  color: var(--app-text-secondary);
}

.save-bar {
  display: flex;
  gap: 10px;
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--app-border);
}

.two-col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 32px;
}

.combo-list,
.param-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.combo-list li,
.param-list li {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 0;
  border-bottom: 1px dashed var(--app-border);
  font-size: 13px;
}

.combo-list li:last-child,
.param-list li:last-child {
  border-bottom: none;
}

.combo-name {
  min-width: 160px;
  color: var(--app-text-regular);
}

.combo-arrow {
  color: var(--app-text-muted);
}

.combo-result {
  color: var(--app-accent-dark);
  font-weight: 600;
}

.param-label {
  min-width: 110px;
  color: var(--app-text-regular);
}

.param-value {
  font-family: var(--app-mono);
  color: var(--app-accent-dark);
  font-weight: 600;
}
</style>
