<template>
  <div class="page">
    <div class="page-head">
      <h2>热点缓存</h2>
      <el-button @click="load">刷新</el-button>
    </div>

    <el-alert
      v-if="stats && stats.redis_available === false"
      title="Redis 当前不可用，精确缓存自动降级为回源，统计可能不完整"
      type="warning"
      :closable="false"
      style="margin-bottom: 12px"
    />

    <div class="card-grid">
      <el-card><div class="muted">近 {{ days }} 天提问</div><div class="stat-value">{{ stats.questions || 0 }}</div></el-card>
      <el-card><div class="muted">缓存命中</div><div class="stat-value">{{ stats.hits || 0 }}</div></el-card>
      <el-card><div class="muted">命中率</div><div class="stat-value">{{ hitRate }}</div></el-card>
      <el-card><div class="muted">命中平均耗时</div><div class="stat-value">{{ stats.avg_hit_ms || 0 }} 毫秒</div></el-card>
      <el-card><div class="muted">未命中平均耗时</div><div class="stat-value">{{ stats.avg_miss_ms || 0 }} 毫秒</div></el-card>
    </div>

    <el-card style="margin-top: 12px">
      <template #header>缓存设置</template>
      <el-form label-width="160px" style="max-width: 560px">
        <el-form-item label="精确缓存">
          <el-switch v-model="form.enabled" />
        </el-form-item>
        <el-form-item label="缓存有效期（天）">
          <el-input-number v-model="form.ttl_days" :min="1" :max="30" />
        </el-form-item>
        <el-form-item label="语义缓存">
          <el-switch v-model="form.semantic_enabled" :disabled="!settings.semantic_supported" />
          <span class="muted" style="margin-left: 12px">
            {{
              settings.semantic_supported
                ? 'Redis 可用，语义缓存用本地嵌入模型匹配相似问题'
                : 'Redis 不可用，语义缓存保持关闭'
            }}
          </span>
        </el-form-item>
        <el-form-item label="语义相似度阈值">
          <el-input-number
            v-model="form.semantic_threshold"
            :min="0.9"
            :max="1"
            :step="0.01"
            :disabled="!form.semantic_enabled"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="saving" @click="saveSettings">保存设置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card style="margin-top: 12px">
      <template #header>清理缓存</template>
      <div style="display: flex; gap: 12px; flex-wrap: wrap; align-items: center">
        <el-select v-model="clearKbId" clearable placeholder="全部知识库" style="width: 260px">
          <el-option
            v-for="item in stats.by_kb || []"
            :key="item.kb_id"
            :label="`${item.kb_name}（${item.questions} 次提问）`"
            :value="item.kb_id"
          />
        </el-select>
        <el-button type="warning" :loading="clearing" @click="doClear">清理</el-button>
        <span class="muted">文档发布、废止、可见性变化会自动使旧缓存失效</span>
      </div>
    </el-card>

    <el-card style="margin-top: 12px">
      <template #header>最近缓存样本</template>
      <el-table :data="samples" size="small">
        <el-table-column prop="question" label="归一化问题" min-width="240" />
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
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
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
  semantic_threshold: 0.95
})

const hitRate = computed(() => `${(((stats.value.hit_rate || 0) * 100).toFixed(1))}%`)

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
      semantic_threshold: Number(settingsData.semantic_threshold || 0.95)
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

onMounted(load)
</script>
