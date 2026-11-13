<template>
  <div class="page">
    <el-tabs v-model="tab" @tab-change="load">
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
        <el-table :data="retrievalItems" size="small" border>
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
        <el-table :data="auditItems" size="small" border>
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
          <el-select v-model="systemLevel" style="width: 140px" @change="loadSystem">
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
            @keyup.enter="loadSystem"
          />
          <el-select v-model="systemLines" style="width: 130px" @change="loadSystem">
            <el-option label="最近 100 条" :value="100" />
            <el-option label="最近 200 条" :value="200" />
            <el-option label="最近 500 条" :value="500" />
          </el-select>
          <el-button @click="loadSystem">刷新</el-button>
          <span class="muted">共 {{ systemTotal }} 条</span>
        </div>
        <el-table :data="systemItems" size="small" border max-height="560">
          <el-table-column label="时间" width="170">
            <template #default="{ row }">{{ row.time }}</template>
          </el-table-column>
          <el-table-column label="级别" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="levelType(row.level)">{{ row.level }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="logger" label="模块" width="220" show-overflow-tooltip />
          <el-table-column prop="message" label="内容" show-overflow-tooltip />
        </el-table>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { listAuditLogs, listRetrievalLogs, listSystemLogs } from '../../api/admin'

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
const systemLevel = ref('')
const systemKeyword = ref('')
const systemLines = ref(200)

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
    lines: systemLines.value,
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

async function load() {
  if (tab.value === 'retrieval') {
    await loadRetrieval()
  } else if (tab.value === 'audit') {
    await loadAudit()
  } else {
    await loadSystem()
  }
}

onMounted(load)
</script>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}
</style>
