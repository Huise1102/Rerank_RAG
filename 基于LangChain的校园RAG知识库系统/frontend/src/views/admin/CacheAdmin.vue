<template>
  <div class="page cache-page">
    <div class="page-head">
      <div class="toolbar">
        <el-select v-model="days" style="width: 120px" @change="load">
          <el-option label="近 7 天" :value="7" />
          <el-option label="近 14 天" :value="14" />
          <el-option label="近 30 天" :value="30" />
        </el-select>
        <el-button @click="load">刷新</el-button>
      </div>
    </div>

    <div class="metric-grid">
      <div v-for="item in metrics" :key="item.label" class="metric-card">
        <div class="metric-top">
          <span class="metric-icon" :style="{ color: item.color, backgroundColor: item.color + '14' }">
            <el-icon><component :is="item.icon" /></el-icon>
          </span>
          <span>{{ item.label }}</span>
        </div>
        <div class="metric-value">
          {{ item.value }}<span v-if="item.unit" class="metric-unit">{{ item.unit }}</span>
        </div>
        <div class="metric-sub">{{ item.sub }}</div>
      </div>
    </div>

    <el-alert
      v-if="stats && stats.redis_available === false"
      title="Redis 当前不可用，精确缓存自动降级为回源，统计可能不完整"
      type="warning"
      :closable="false"
    />

    <div class="cache-main">
      <section class="panel">
        <div class="panel-head">
          <div class="panel-title">缓存设置</div>
        </div>
        <el-form label-width="130px" label-position="left">
          <el-form-item label="精确缓存">
            <el-switch v-model="form.enabled" />
          </el-form-item>
          <el-form-item label="缓存有效期">
            <el-input-number v-model="form.ttl_days" :min="1" :max="30" />
            <span class="form-hint">天</span>
          </el-form-item>
          <el-form-item label="语义缓存">
            <div class="inline-field">
              <el-switch v-model="form.semantic_enabled" :disabled="!settings.semantic_supported" />
              <span class="form-hint">
                {{
                  settings.semantic_supported
                    ? '用本地嵌入模型匹配相似问题'
                    : 'Redis 不可用，语义缓存保持关闭'
                }}
              </span>
            </div>
          </el-form-item>
          <el-form-item label="相似度阈值">
            <el-input-number
              v-model="form.semantic_threshold"
              :min="0.75"
              :max="1"
              :step="0.01"
              :disabled="!form.semantic_enabled"
            />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="saving" @click="saveSettings">保存设置</el-button>
          </el-form-item>
        </el-form>
      </section>

      <section class="panel">
        <div class="panel-head">
          <div class="panel-title">知识库命中情况</div>
          <span class="count">近 {{ days }} 天</span>
        </div>
        <div v-if="kbRows.length" class="kb-list">
          <div v-for="item in kbRows" :key="item.kb_id" class="kb-row">
            <div class="kb-head">
              <span class="kb-name">{{ item.kb_name }}</span>
              <span class="kb-rate">{{ item.rate }}%</span>
            </div>
            <div class="kb-track">
              <div class="kb-fill" :style="{ width: item.rate + '%' }" />
            </div>
            <div class="kb-sub">提问 {{ item.questions }} 次 · 命中 {{ item.hits }} 次</div>
          </div>
        </div>
        <div v-else class="empty">暂无命中数据</div>
      </section>
    </div>

    <section class="panel">
      <div class="panel-head">
        <div class="panel-title">
          最近缓存样本
          <span class="count">共 {{ samples.length }} 条</span>
        </div>
        <div class="sample-actions">
          <el-select v-model="clearKbId" clearable placeholder="全部知识库" style="width: 220px">
            <el-option
              v-for="item in stats.by_kb || []"
              :key="item.kb_id"
              :label="`${item.kb_name}（${item.questions} 次提问）`"
              :value="item.kb_id"
            />
          </el-select>
          <el-button type="warning" plain :loading="clearing" @click="doClear">清理缓存</el-button>
        </div>
      </div>
      <el-table :data="samples" size="small" max-height="360">
        <el-table-column prop="question" label="归一化问题" min-width="240" show-overflow-tooltip />
        <el-table-column prop="kb_id" label="知识库" width="90" />
        <el-table-column prop="index_epoch" label="索引版本" width="90" />
        <el-table-column prop="created_at" label="写入时间" width="170" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.flagged" type="danger" size="small">疑似误答</el-tag>
            <el-tag v-else type="info" size="small">正常</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120">
          <template #default="{ row }">
            <el-button v-if="!row.flagged" link size="small" @click="flagSample(row)">标记误答</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="table-tip muted">文档发布、废止、可见性变化会自动使旧缓存失效</div>
    </section>
  </div>
</template>

<script setup>
import { computed, onActivated, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ChatDotRound,
  Clock,
  Lightning,
  Timer,
  TrendCharts
} from '@element-plus/icons-vue'
import {
  clearCache,
  flagCacheSample,
  getCacheSettings,
  getCacheStats,
  listCacheSamples,
  updateCacheSettings
} from '../../api/cache'

const days = ref(7)
const stats = ref({})
const settings = ref({})
const samples = ref([])
const clearKbId = ref(null)
const saving = ref(false)
const clearing = ref(false)
const form = reactive({
  enabled: true,
  ttl_days: 7,
  semantic_enabled: false,
  semantic_threshold: 0.85
})

const hitRate = computed(() => `${(((stats.value.hit_rate || 0) * 100).toFixed(1))}%`)

const metrics = computed(() => [
  {
    label: '提问量',
    value: stats.value.questions || 0,
    sub: `近 ${days.value} 天累计`,
    icon: ChatDotRound,
    color: '#0f9d8f'
  },
  {
    label: '缓存命中',
    value: stats.value.hits || 0,
    sub: '精确缓存命中次数',
    icon: Lightning,
    color: '#6366f1'
  },
  {
    label: '命中率',
    value: hitRate.value,
    sub: `命中 ${stats.value.hits || 0} / 提问 ${stats.value.questions || 0}`,
    icon: TrendCharts,
    color: '#3b82f6'
  },
  {
    label: '命中平均耗时',
    value: stats.value.avg_hit_ms || 0,
    unit: '毫秒',
    sub: '缓存直接返回',
    icon: Timer,
    color: '#06b6d4'
  },
  {
    label: '未命中平均耗时',
    value: stats.value.avg_miss_ms || 0,
    unit: '毫秒',
    sub: '完整检索链路',
    icon: Clock,
    color: '#f59e0b'
  }
])

const kbRows = computed(() =>
  (stats.value.by_kb || [])
    .map((item) => {
      const questions = Number(item.questions || 0)
      const hits = Number(item.hits || 0)
      return {
        ...item,
        questions,
        hits,
        rate: questions ? Math.round((hits / questions) * 1000) / 10 : 0
      }
    })
    .filter((item) => item.questions > 0)
    .sort((a, b) => b.questions - a.questions)
)

async function load() {
  try {
    const [statsData, settingsData, sampleData] = await Promise.all([
      getCacheStats(days.value),
      getCacheSettings(),
      listCacheSamples(20)
    ])
    stats.value = statsData
    settings.value = settingsData
    samples.value = sampleData.samples || []
    Object.assign(form, {
      enabled: Boolean(settingsData.enabled),
      ttl_days: Number(settingsData.ttl_days || 7),
      semantic_enabled: Boolean(settingsData.semantic_enabled),
      semantic_threshold: Number(settingsData.semantic_threshold || 0.85)
    })
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '缓存数据加载失败')
  }
}

async function saveSettings() {
  saving.value = true
  try {
    settings.value = await updateCacheSettings({ ...form })
    ElMessage.success('缓存设置已保存')
    await load()
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

async function doClear() {
  try {
    await ElMessageBox.confirm(
      clearKbId.value ? '确认清理该知识库的全部缓存？' : '确认清理全部热点缓存？',
      '清理缓存',
      { type: 'warning' }
    )
  } catch {
    return
  }
  clearing.value = true
  try {
    const result = await clearCache({ kb_id: clearKbId.value })
    ElMessage.success(`已清理 ${result.removed} 条缓存`)
    await load()
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '清理失败')
  } finally {
    clearing.value = false
  }
}

async function flagSample(row) {
  try {
    await flagCacheSample(row.key)
    ElMessage.success('已标记为疑似误答')
    await load()
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '标记失败')
  }
}

onActivated(load)
</script>

<style scoped>
.cache-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 10px;
}

.metric-card {
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 10px;
  padding: 12px 14px;
  min-width: 0;
}

.metric-top {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #909399;
  font-size: 13px;
}

.metric-icon {
  width: 26px;
  height: 26px;
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 15px;
}

.metric-value {
  margin-top: 8px;
  font-size: 26px;
  font-weight: 700;
  color: #1f2937;
  line-height: 1.15;
  white-space: nowrap;
}

.metric-unit {
  margin-left: 4px;
  font-size: 13px;
  font-weight: 500;
  color: #909399;
}

.metric-sub {
  margin-top: 4px;
  font-size: 12px;
  color: #9ca3af;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cache-main {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 12px;
}

.panel {
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 10px;
  padding: 14px 16px;
  min-width: 0;
}

.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

.panel-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}

.panel-title::before {
  content: '';
  width: 3px;
  height: 14px;
  border-radius: 2px;
  background: #0f9d8f;
}

.count {
  font-size: 12px;
  font-weight: 400;
  color: #9ca3af;
}

.form-hint {
  margin-left: 8px;
  font-size: 12px;
  color: #9ca3af;
  line-height: 1.5;
}

.inline-field {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
}

.kb-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-height: 300px;
  overflow-y: auto;
  padding-right: 4px;
}

.kb-row {
  min-width: 0;
}

.kb-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 13px;
  color: #4b5563;
}

.kb-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.kb-rate {
  font-weight: 600;
  color: #0f9d8f;
}

.kb-track {
  margin: 6px 0 4px;
  height: 8px;
  border-radius: 4px;
  background: #f1f5f9;
  overflow: hidden;
}

.kb-fill {
  height: 100%;
  border-radius: 4px;
  background: linear-gradient(90deg, #0f9d8f, #38bdf8);
}

.kb-sub {
  font-size: 12px;
  color: #9ca3af;
}

.sample-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.table-tip {
  margin-top: 10px;
  font-size: 12px;
}

.empty {
  padding: 40px 0;
  text-align: center;
  color: #9ca3af;
  font-size: 13px;
}

@media (max-width: 1280px) {
  .metric-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 1024px) {
  .cache-main {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 768px) {
  .metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
