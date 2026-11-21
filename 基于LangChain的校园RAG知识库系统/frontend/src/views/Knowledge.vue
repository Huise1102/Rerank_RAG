<template>
  <div class="page kb-page">
    <div class="kb-page-head">
      <div>
        <div class="page-title">知识库</div>
        <p class="kb-page-sub">
          浏览学校已发布的知识库，了解收录范围与制度文件，并查看原文出处。
        </p>
      </div>
      <el-button type="primary" plain @click="goChat">
        <el-icon><ChatDotRound /></el-icon>
        <span>去问答</span>
      </el-button>
    </div>

    <section class="kb-intro-card">
      <div class="kb-intro-icon">
        <el-icon :size="24"><Collection /></el-icon>
      </div>
      <div class="kb-intro-main">
        <h2>知识库介绍</h2>
        <p>
          知识库是问答系统回答问题的权威依据。各部门上传制度文件后，系统会自动解析正文、按条款或长度切分，
          并建立向量索引与关键词索引；提问时先检索相关片段，再经过融合与精排选出最相关的内容，最终给出带原文出处的回答。
        </p>
        <div class="kb-intro-points">
          <div class="kb-intro-point">
            <strong>只收录生效内容</strong>
            <span>仅展示已发布且处于生效期的当前版本，历史版本不会混入回答。</span>
          </div>
          <div class="kb-intro-point">
            <strong>按身份控制范围</strong>
            <span>全校公开知识库对所有账号可见，部门内部知识库仅本部门成员可见。</span>
          </div>
          <div class="kb-intro-point">
            <strong>原文随时核对</strong>
            <span>每条回答都会标注来源、版本与页码，点击文档即可查看原文。</span>
          </div>
        </div>
      </div>
    </section>

    <el-alert
      v-if="error"
      :title="error"
      type="error"
      show-icon
      :closable="false"
      class="kb-alert"
    />

    <div v-loading="loading" class="kb-content">
      <aside class="kb-list-panel">
        <div class="kb-panel-head">
          <span>知识库目录</span>
          <el-tag size="small" effect="plain">{{ kbs.length }}</el-tag>
        </div>
        <div v-if="kbs.length === 0 && !loading" class="kb-list-empty">
          暂无可查看的知识库，请联系管理员发布知识库或检查账号权限。
        </div>
        <button
          v-for="kb in kbs"
          :key="kb.id"
          type="button"
          class="kb-entry"
          :class="{ active: kb.id === activeKbId }"
          @click="selectKb(kb.id)"
        >
          <div class="kb-entry-head">
            <span class="kb-entry-name">{{ kb.name }}</span>
            <el-tag
              size="small"
              :type="kb.visibility === 'public' ? 'success' : 'warning'"
              effect="plain"
            >
              {{ visibilityLabel(kb.visibility) }}
            </el-tag>
          </div>
          <p class="kb-entry-desc">{{ kb.description || '暂无详细介绍' }}</p>
          <div class="kb-entry-meta">
            <span>{{ strategyLabel(kb.chunk_strategy) }}</span>
            <span>复核周期 {{ kb.review_cycle_days || '-' }} 天</span>
          </div>
        </button>
      </aside>

      <section v-loading="detailLoading" class="kb-detail-panel">
        <template v-if="detail">
          <div class="kb-detail-head">
            <div class="kb-detail-title">
              <h2>{{ detail.name }}</h2>
              <el-tag
                :type="detail.visibility === 'public' ? 'success' : 'warning'"
                effect="light"
              >
                {{ visibilityLabel(detail.visibility) }}
              </el-tag>
            </div>
            <p class="kb-detail-desc">{{ detail.description || '该知识库暂无详细描述。' }}</p>
          </div>

          <div class="kb-stat-grid">
            <div class="kb-stat-card">
              <strong>{{ detail.document_count ?? documents.length }}</strong>
              <span>收录文档</span>
            </div>
            <div class="kb-stat-card">
              <strong>{{ detail.chunk_count ?? 0 }}</strong>
              <span>知识片段</span>
            </div>
            <div class="kb-stat-card">
              <strong>{{ detail.review_cycle_days || '-' }}</strong>
              <span>复核周期（天）</span>
            </div>
            <div class="kb-stat-card">
              <strong class="kb-stat-date">{{ formatDateTime(detail.updated_at) }}</strong>
              <span>最近更新</span>
            </div>
          </div>

          <div class="kb-meta-grid">
            <div class="kb-meta-item">
              <span>归属部门</span>
              <strong>{{ detail.department_name || '全校' }}</strong>
            </div>
            <div class="kb-meta-item">
              <span>责任人</span>
              <strong>{{ detail.owner_name || '未设置' }}</strong>
            </div>
            <div class="kb-meta-item">
              <span>分块策略</span>
              <strong>{{ strategyLabel(detail.chunk_strategy) }}</strong>
            </div>
            <div class="kb-meta-item">
              <span>可见范围</span>
              <strong>{{ visibilityLabel(detail.visibility) }}</strong>
            </div>
          </div>

          <div class="kb-doc-section">
            <div class="kb-doc-head">
              <h3>收录文档</h3>
              <span>仅展示当前生效版本，共 {{ documents.length }} 篇</span>
            </div>
            <el-table :data="documents" size="small" border class="kb-doc-table">
              <el-table-column prop="title" label="文档标题" min-width="220" show-overflow-tooltip>
                <template #default="{ row }">
                  <button type="button" class="kb-doc-link" @click="openPreview(row)">
                    {{ row.title }}
                  </button>
                </template>
              </el-table-column>
              <el-table-column prop="doc_no" label="文号" width="150" show-overflow-tooltip>
                <template #default="{ row }">{{ row.doc_no || '—' }}</template>
              </el-table-column>
              <el-table-column label="类型" width="80">
                <template #default="{ row }">
                  <el-tag size="small" effect="plain">{{ (row.file_type || '').toUpperCase() }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="版本" width="90">
                <template #default="{ row }">{{ row.version_label || '—' }}</template>
              </el-table-column>
              <el-table-column label="生效日期" width="110">
                <template #default="{ row }">{{ formatDate(row.effective_date) }}</template>
              </el-table-column>
              <el-table-column label="大小" width="90">
                <template #default="{ row }">{{ formatSize(row.size_bytes) }}</template>
              </el-table-column>
              <el-table-column label="操作" width="100" align="right">
                <template #default="{ row }">
                  <el-button link type="primary" size="small" @click="openPreview(row)">
                    查看原文
                  </el-button>
                </template>
              </el-table-column>
              <template #empty>
                <div class="kb-doc-empty">该知识库暂无可查看的文档</div>
              </template>
            </el-table>
            <p class="kb-doc-note">
              文档内容由各责任部门维护。如发现内容有误或未覆盖，可通过问答页的「疑难提交」反馈。
            </p>
          </div>
        </template>
        <el-empty
          v-else-if="!detailLoading"
          :description="kbs.length ? '请选择左侧知识库查看详细介绍' : '暂无可查看的知识库'"
          :image-size="96"
        />
      </section>
    </div>

    <DocumentPreviewDialog
      v-model="previewVisible"
      :document="previewDocument"
    />
  </div>
</template>

<script setup>
import { onActivated } from 'vue'
import { useRouter } from 'vue-router'
import { ChatDotRound, Collection } from '@element-plus/icons-vue'
import DocumentPreviewDialog from '../components/DocumentPreviewDialog.vue'
import {
  formatDate,
  formatDateTime,
  formatSize,
  strategyLabel,
  useKnowledgeBase,
  visibilityLabel
} from '../composables/useKnowledgeBase'

const router = useRouter()
const {
  kbs,
  activeKbId,
  detail,
  documents,
  loading,
  detailLoading,
  error,
  previewVisible,
  previewDocument,
  load,
  selectKb,
  openPreview
} = useKnowledgeBase()

function goChat() {
  router.push('/chat')
}

onActivated(load)
</script>

<style scoped>
.kb-page {
  height: 100%;
  overflow-y: auto;
}

.kb-page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}

.kb-page-sub {
  margin: 6px 0 0;
  color: #909399;
  font-size: 13px;
  line-height: 1.6;
}

.kb-intro-card {
  display: flex;
  gap: 16px;
  margin-bottom: 16px;
  padding: 20px 22px;
  border: 1px solid #dbe7f6;
  border-radius: 12px;
  background: linear-gradient(135deg, #eef5ff 0%, #f8fbff 100%);
}

.kb-intro-icon {
  flex: none;
  width: 46px;
  height: 46px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 12px;
  color: #1f6fd0;
  background: #fff;
  box-shadow: 0 2px 10px rgba(31, 111, 208, 0.12);
}

.kb-intro-main {
  min-width: 0;
}

.kb-intro-main h2 {
  margin: 0 0 6px;
  font-size: 17px;
  color: #1f2d3d;
}

.kb-intro-main > p {
  margin: 0;
  color: #5c6b7f;
  font-size: 13px;
  line-height: 1.8;
}

.kb-intro-points {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-top: 14px;
}

.kb-intro-point {
  padding: 10px 12px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.72);
}

.kb-intro-point strong {
  display: block;
  margin-bottom: 4px;
  color: #1f2d3d;
  font-size: 13px;
}

.kb-intro-point span {
  color: #7a8797;
  font-size: 12px;
  line-height: 1.7;
}

.kb-alert {
  margin-bottom: 12px;
}

.kb-content {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  min-height: 360px;
}

.kb-list-panel {
  flex: none;
  width: 280px;
  padding: 14px;
  border-radius: 12px;
  background: #fff;
  box-shadow: 0 2px 12px rgba(31, 45, 61, 0.06);
}

.kb-panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
  padding: 0 4px;
  color: #1f2d3d;
  font-size: 14px;
  font-weight: 600;
}

.kb-list-empty {
  padding: 28px 12px;
  color: #909399;
  font-size: 13px;
  line-height: 1.8;
  text-align: center;
}

.kb-entry {
  display: block;
  width: 100%;
  margin-bottom: 8px;
  padding: 12px;
  border: 1px solid transparent;
  border-radius: 9px;
  background: #f7f9fc;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.18s, background-color 0.18s;
}

.kb-entry:last-child {
  margin-bottom: 0;
}

.kb-entry:hover {
  background: #eef4fd;
}

.kb-entry.active {
  border-color: #1f6fd0;
  background: #ecf5ff;
}

.kb-entry-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.kb-entry-name {
  overflow: hidden;
  color: #1f2d3d;
  font-size: 14px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.kb-entry-desc {
  display: -webkit-box;
  overflow: hidden;
  margin: 6px 0;
  color: #7a8797;
  font-size: 12px;
  line-height: 1.6;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.kb-entry-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
  color: #a0aaba;
  font-size: 11px;
}

.kb-detail-panel {
  flex: 1;
  min-width: 0;
  padding: 20px 22px;
  border-radius: 12px;
  background: #fff;
  box-shadow: 0 2px 12px rgba(31, 45, 61, 0.06);
}

.kb-detail-head {
  padding-bottom: 16px;
  border-bottom: 1px solid #ebeef5;
}

.kb-detail-title {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.kb-detail-title h2 {
  margin: 0;
  color: #1f2d3d;
  font-size: 20px;
}

.kb-detail-desc {
  margin: 8px 0 0;
  color: #5c6b7f;
  font-size: 14px;
  line-height: 1.8;
  white-space: pre-wrap;
}

.kb-stat-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin: 16px 0;
}

.kb-stat-card {
  padding: 14px 16px;
  border-radius: 10px;
  background: #f7f9fc;
}

.kb-stat-card strong {
  display: block;
  color: #1f6fd0;
  font-size: 24px;
  line-height: 1.2;
}

.kb-stat-card .kb-stat-date {
  font-size: 15px;
}

.kb-stat-card span {
  display: block;
  margin-top: 4px;
  color: #909399;
  font-size: 12px;
}

.kb-meta-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 20px;
}

.kb-meta-item {
  padding: 10px 12px;
  border: 1px solid #ebeef5;
  border-radius: 8px;
}

.kb-meta-item span {
  display: block;
  color: #a0aaba;
  font-size: 12px;
}

.kb-meta-item strong {
  display: block;
  margin-top: 4px;
  overflow: hidden;
  color: #303133;
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.kb-doc-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.kb-doc-head h3 {
  margin: 0;
  color: #1f2d3d;
  font-size: 16px;
}

.kb-doc-head span {
  color: #a0aaba;
  font-size: 12px;
}

.kb-doc-link {
  padding: 0;
  border: none;
  background: transparent;
  color: #1f6fd0;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.kb-doc-link:hover {
  text-decoration: underline;
}

.kb-doc-empty {
  padding: 28px 0;
  color: #909399;
  font-size: 13px;
}

.kb-doc-note {
  margin: 12px 0 0;
  color: #a0aaba;
  font-size: 12px;
  line-height: 1.7;
}

@media (max-width: 900px) {
  .kb-content {
    flex-direction: column;
  }

  .kb-list-panel {
    width: 100%;
  }

  .kb-intro-points,
  .kb-stat-grid,
  .kb-meta-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 560px) {
  .kb-page-head {
    flex-direction: column;
  }

  .kb-intro-card {
    padding: 16px;
  }

  .kb-intro-points,
  .kb-stat-grid,
  .kb-meta-grid {
    grid-template-columns: 1fr;
  }
}
</style>
