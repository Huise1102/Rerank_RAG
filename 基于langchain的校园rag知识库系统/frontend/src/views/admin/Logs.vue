<template>
  <div class="page logs-page">
    <section class="panel">
      <div class="panel-head">
        <div class="panel-title">
          日志明细
          <span class="count">共 {{ currentTotal }} 条</span>
        </div>
        <el-button type="danger" plain size="small" @click="clearVisible = true">清理日志</el-button>
      </div>

      <el-tabs v-model="tab" class="log-tabs" @tab-change="load">
      <el-tab-pane label="检索日志" name="retrieval">
        <div class="toolbar">
          <el-input
            v-model="retrievalKeyword"
            placeholder="按问题关键词搜索"
            style="width: 260px"
            clearable
            @keyup.enter="searchRetrieval"
          />
          <el-button @click="searchRetrieval">查询</el-button>
          <el-button @click="loadRetrieval">刷新</el-button>
        </div>
        <el-table :data="retrievalItems" size="small" border max-height="520">
          <el-table-column prop="question" label="问题" show-overflow-tooltip />
          <el-table-column prop="strategy_name" label="策略" width="110" />
          <el-table-column prop="vector_hits" label="向量" width="70" />
          <el-table-column prop="bm25_hits" label="关键词" width="80" />
          <el-table-column prop="reranked_hits" label="精排" width="70" />
          <el-table-column prop="duration_ms" label="耗时(毫秒)" width="100" />
          <el-table-column label="时间" width="170">
            <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
          </el-table-column>
        </el-table>
        <el-pagination
          v-model:current-page="retrievalPage"
          :page-size="pageSize"
          :total="retrievalTotal"
          layout="total, prev, pager, next"
          style="margin-top: 12px"
          @current-change="loadRetrieval"
        />
      </el-tab-pane>

      <el-tab-pane label="审计日志" name="audit">
        <div class="toolbar">
          <el-input
            v-model="auditKeyword"
            placeholder="按操作人或内容搜索"
            style="width: 260px"
            clearable
            @keyup.enter="searchAudit"
          />
          <el-button @click="searchAudit">查询</el-button>
          <el-button @click="loadAudit">刷新</el-button>
        </div>
        <el-table :data="auditItems" size="small" border max-height="520">
          <el-table-column prop="username" label="操作人" width="120" />
          <el-table-column prop="action" label="动作" width="160" />
          <el-table-column prop="target_type" label="对象类型" width="130" />
          <el-table-column prop="target_id" label="对象 ID" width="90" />
          <el-table-column prop="detail" label="说明" show-overflow-tooltip />
          <el-table-column prop="ip" label="IP" width="130" />
          <el-table-column label="时间" width="170">
            <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
          </el-table-column>
        </el-table>
        <el-pagination
          v-model:current-page="auditPage"
          :page-size="pageSize"
          :total="auditTotal"
          layout="total, prev, pager, next"
          style="margin-top: 12px"
          @current-change="loadAudit"
        />
      </el-tab-pane>

      <el-tab-pane label="系统日志" name="system">
        <div class="toolbar">
          <el-select v-model="systemLevel" style="width: 140px" @change="searchSystem">
            <el-option label="全部级别" value="" />
            <el-option label="DEBUG" value="DEBUG" />
            <el-option label="INFO" value="INFO" />
            <el-option label="WARNING" value="WARNING" />
            <el-option label="ERROR" value="ERROR" />
          </el-select>
          <el-input
            v-model="systemKeyword"
            placeholder="按模块或内容搜索"
            style="width: 240px"
            clearable
            @keyup.enter="searchSystem"
          />
          <el-button @click="searchSystem">查询</el-button>
          <el-button @click="loadSystem">刷新</el-button>
        </div>
        <el-table :data="systemItems" size="small" border max-height="520">
          <el-table-column label="时间" width="170">
            <template #default="{ row }">{{ row.time }}</template>
          </el-table-column>
          <el-table-column label="级别" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="levelType(row.level)">{{ row.level }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="logger" label="模块" width="220" show-overflow-tooltip />
          <el-table-column prop="request_id" label="请求 ID" width="130" show-overflow-tooltip />
          <el-table-column prop="message" label="内容" show-overflow-tooltip />
        </el-table>
        <el-pagination
          v-model:current-page="systemPage"
          :page-size="pageSize"
          :total="systemTotal"
          layout="total, prev, pager, next"
          style="margin-top: 12px"
          @current-change="loadSystem"
        />
      </el-tab-pane>
    </el-tabs>
    </section>

    <el-dialog v-model="clearVisible" title="清理日志" width="420px">
      <el-alert
        title="清理后无法恢复，请谨慎操作"
        type="warning"
        show-icon
        :closable="false"
      />
      <el-checkbox-group v-model="clearScopes" class="clear-scopes">
        <el-checkbox label="system">系统日志文件</el-checkbox>
        <el-checkbox label="retrieval">检索日志</el-checkbox>
        <el-checkbox label="audit">审计日志</el-checkbox>
      </el-checkbox-group>
      <template #footer>
        <el-button @click="clearVisible = false">取消</el-button>
        <el-button type="danger" :loading="clearing" @click="confirmClear">确认清理</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { clearLogs, listAuditLogs, listRetrievalLogs, listSystemLogs } from '../../api/admin'

const tab = ref('retrieval')
const pageSize = 20
const retrievalItems = ref([])
const retrievalTotal = ref(0)
const retrievalPage = ref(1)
const retrievalKeyword = ref('')
const auditItems = ref([])
const auditTotal = ref(0)
const auditPage = ref(1)
const auditKeyword = ref('')
const systemItems = ref([])
const systemTotal = ref(0)
const systemPage = ref(1)
const systemLevel = ref('')
const systemKeyword = ref('')
const clearVisible = ref(false)
const clearScopes = ref(['system'])
const clearing = ref(false)

const currentTotal = computed(() => {
  if (tab.value === 'retrieval') return retrievalTotal.value
  if (tab.value === 'audit') return auditTotal.value
  return systemTotal.value
})

function formatTime(value) {
  return value ? String(value).replace('T', ' ').slice(0, 19) : ''
}

function levelType(level) {
  if (level === 'ERROR' || level === 'CRITICAL') return 'danger'
  if (level === 'WARNING') return 'warning'
  if (level === 'DEBUG') return 'info'
  return 'success'
}

async function loadRetrieval() {
  const data = await listRetrievalLogs({
    page: retrievalPage.value,
    page_size: pageSize,
    keyword: retrievalKeyword.value || undefined
  })
  retrievalItems.value = data.items
  retrievalTotal.value = data.total
}

async function loadAudit() {
  const data = await listAuditLogs({
    page: auditPage.value,
    page_size: pageSize,
    keyword: auditKeyword.value || undefined
  })
  auditItems.value = data.items
  auditTotal.value = data.total
}

async function loadSystem() {
  const data = await listSystemLogs({
    page: systemPage.value,
    page_size: pageSize,
    level: systemLevel.value || undefined,
    keyword: systemKeyword.value || undefined
  })
  systemItems.value = data.items
  systemTotal.value = data.total
}

function searchRetrieval() {
  retrievalPage.value = 1
  loadRetrieval()
}

function searchAudit() {
  auditPage.value = 1
  loadAudit()
}

function searchSystem() {
  systemPage.value = 1
  loadSystem()
}

async function load() {
  if (tab.value === 'retrieval') {
    await loadRetrieval()
  } else if (tab.value === 'audit') {
    await loadAudit()
  } else {
    await loadSystem()
  }
}

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} 字节`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

async function confirmClear() {
  if (!clearScopes.value.length) {
    ElMessage.warning('请选择要清理的日志')
    return
  }
  clearing.value = true
  try {
    const data = await clearLogs({
      retrieval: clearScopes.value.includes('retrieval'),
      audit: clearScopes.value.includes('audit'),
      system: clearScopes.value.includes('system')
    })
    const cleared = data.cleared || {}
    const parts = []
    if ('retrieval' in cleared) parts.push(`检索日志 ${cleared.retrieval} 条`)
    if ('audit' in cleared) parts.push(`审计日志 ${cleared.audit} 条`)
    if ('system_bytes' in cleared) parts.push(`系统日志 ${formatSize(cleared.system_bytes)}`)
    ElMessage.success(`已清理：${parts.join('，')}`)
    clearVisible.value = false
    await load()
  } finally {
    clearing.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.panel {
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 10px;
  padding: 12px 16px 16px;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
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

.log-tabs :deep(.el-tabs__header) {
  margin: 0 0 12px;
}

.log-tabs :deep(.el-tabs__content) {
  padding: 0;
}

.clear-scopes {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 16px;
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}
</style>
