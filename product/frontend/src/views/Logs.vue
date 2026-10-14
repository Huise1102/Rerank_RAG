<template>
  <div class="page">
    <div class="page-head">
      <div class="page-head__left">
        <div class="page-title">日志</div>
        <div class="page-subtitle">检索日志来自 retrieval_logs 表，系统日志来自 logs/app.log</div>
      </div>
    </div>

    <div class="panel">
      <el-tabs v-model="activeTab" @tab-change="onTabChange">
        <!-- 检索日志 -->
        <el-tab-pane label="检索日志" name="retrieval">
          <div class="filter-bar">
            <span class="filter-label">知识库</span>
            <el-select
              v-model="kbFilter"
              placeholder="全部"
              clearable
              style="width: 220px"
              @change="reloadRetrieval"
            >
              <el-option v-for="kb in kbStore.kbs" :key="kb.id" :label="kb.name" :value="kb.id" />
            </el-select>
            <el-button @click="reloadRetrieval">
              <el-icon><Refresh /></el-icon>
              <span>刷新</span>
            </el-button>
          </div>

          <el-table :data="logs" v-loading="logsLoading" stripe style="width: 100%">
            <el-table-column label="知识库" width="140">
              <template #default="{ row }">{{ kbNameOf(row) }}</template>
            </el-table-column>
            <el-table-column prop="query" label="查询" min-width="220" show-overflow-tooltip />
            <el-table-column label="生效策略" width="140">
              <template #default="{ row }">
                <el-tag size="small" effect="plain">{{ row.strategy || '—' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="top_k" label="top_k" width="80" />
            <el-table-column label="耗时" width="100">
              <template #default="{ row }">{{ latencyOf(row) }}</template>
            </el-table-column>
            <el-table-column prop="result_count" label="结果数" width="90" />
            <el-table-column label="时间" width="180">
              <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
            </el-table-column>
            <template #empty>
              <div class="table-empty">暂无检索日志</div>
            </template>
          </el-table>

          <div class="pager">
            <el-pagination
              v-model:current-page="page"
              v-model:page-size="pageSize"
              :total="total"
              :page-sizes="[10, 20, 50]"
              layout="total, sizes, prev, pager, next"
              @current-change="loadRetrieval"
              @size-change="reloadRetrieval"
            />
          </div>
        </el-tab-pane>

        <!-- 系统日志 -->
        <el-tab-pane label="系统日志" name="system">
          <div class="filter-bar">
            <span class="filter-label">条数</span>
            <el-select v-model="lines" style="width: 120px" @change="loadSystem">
              <el-option v-for="n in [100, 200, 500, 1000]" :key="n" :label="`${n} 行`" :value="n" />
            </el-select>
            <span class="filter-label">级别</span>
            <el-select v-model="level" placeholder="全部" clearable style="width: 130px" @change="loadSystem">
              <el-option v-for="lv in levels" :key="lv" :label="lv" :value="lv" />
            </el-select>
            <span class="filter-label">关键字</span>
            <el-input
              v-model="keyword"
              placeholder="按关键字过滤"
              clearable
              style="width: 220px"
              @keyup.enter="loadSystem"
            />
            <el-button @click="loadSystem">
              <el-icon><Refresh /></el-icon>
              <span>刷新</span>
            </el-button>
          </div>

          <pre v-loading="systemLoading" class="log-view scroll-area">{{ systemLogText || '（暂无日志）' }}</pre>
        </el-tab-pane>
      </el-tabs>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useKbStore } from '@/stores/kb'
import { getRetrievalLogs, getSystemLogs } from '@/api/log'

const kbStore = useKbStore()

const activeTab = ref('retrieval')

// 检索日志
const kbFilter = ref(null)
const logs = ref([])
const logsLoading = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)

// 系统日志
const systemLogText = ref('')
const systemLoading = ref(false)
const lines = ref(200)
const level = ref('')
const keyword = ref('')
const levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR']

onMounted(async () => {
  if (!kbStore.kbs.length) await kbStore.fetchKbs()
  await loadRetrieval()
})

function onTabChange(name) {
  if (name === 'system' && !systemLogText.value) {
    loadSystem()
  }
}

function reloadRetrieval() {
  page.value = 1
  loadRetrieval()
}

async function loadRetrieval() {
  logsLoading.value = true
  try {
    const params = { page: page.value, page_size: pageSize.value }
    if (kbFilter.value) params.kb_id = kbFilter.value
    const data = await getRetrievalLogs(params)
    const list = Array.isArray(data) ? data : (data?.items || data?.results || data?.data || data?.list || [])
    logs.value = list
    total.value = data?.total ?? data?.count ?? list.length
  } catch (e) {
    logs.value = []
    total.value = 0
  } finally {
    logsLoading.value = false
  }
}

async function loadSystem() {
  systemLoading.value = true
  try {
    const params = { lines: lines.value }
    if (level.value) params.level = level.value
    if (keyword.value.trim()) params.keyword = keyword.value.trim()
    const data = await getSystemLogs(params)
    systemLogText.value = normalizeSystemLogs(data)
  } catch (e) {
    systemLogText.value = ''
  } finally {
    systemLoading.value = false
  }
}

function normalizeSystemLogs(data) {
  if (typeof data === 'string') return data
  if (Array.isArray(data)) return data.join('\n')
  if (data && typeof data === 'object') {
    const arr = data.lines || data.logs || data.content || data.data
    if (Array.isArray(arr)) return arr.join('\n')
    if (typeof arr === 'string') return arr
  }
  return ''
}

function kbNameOf(row) {
  if (row.kb_name) return row.kb_name
  if (row.kb_id !== undefined && row.kb_id !== null) return kbStore.kbName(row.kb_id)
  return '—'
}

function latencyOf(row) {
  const v = row.latency ?? row.latency_ms
  if (typeof v !== 'number') return '—'
  // latency 为秒则直接显示，为毫秒则换算
  return v > 100 ? `${(v / 1000).toFixed(2)}s` : `${v.toFixed(3)}s`
}

function formatTime(value) {
  if (!value) return '—'
  return String(value).replace('T', ' ').replace(/\.\d+$/, '').slice(0, 19)
}
</script>

<style scoped>
.filter-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}

.filter-label {
  font-size: 13px;
  color: var(--app-text-secondary);
  font-weight: 600;
}

.table-empty {
  padding: 28px 0;
  color: var(--app-text-muted);
  font-size: 13px;
}

.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 14px;
}

.log-view {
  margin: 0;
  max-height: 520px;
  overflow: auto;
  background-color: var(--app-surface-sunken);
  border: 1px solid var(--app-border);
  border-radius: 4px;
  padding: 14px 16px;
  font-family: var(--app-mono);
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--app-text-regular);
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
