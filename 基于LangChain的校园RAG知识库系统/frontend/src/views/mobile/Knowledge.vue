<template>
  <div class="m-kb-page">
    <div class="m-kb-head">
      <div>
        <div class="m-kb-title">知识库</div>
        <div class="m-kb-sub">查看已发布的知识库与制度文件</div>
      </div>
      <el-button link type="primary" @click="goChat">去问答</el-button>
    </div>

    <el-collapse v-model="introOpen" class="m-kb-intro">
      <el-collapse-item title="知识库介绍" name="intro">
        <p>
          知识库是问答系统回答问题的权威依据。系统会自动解析制度文件、切分条款并建立混合索引，
          提问时检索最相关的片段，再生成带原文出处的回答。
        </p>
        <div class="m-kb-intro-item">
          <strong>只收录生效内容</strong>
          <span>展示已发布且处于生效期的当前版本。</span>
        </div>
        <div class="m-kb-intro-item">
          <strong>按身份控制范围</strong>
          <span>全校公开知识库所有人可见，部门知识库仅本部门成员可见。</span>
        </div>
        <div class="m-kb-intro-item">
          <strong>原文随时核对</strong>
          <span>点击文档即可查看原文，回答中也会标注来源。</span>
        </div>
      </el-collapse-item>
    </el-collapse>

    <div v-if="kbs.length" class="m-kb-tabs">
      <button
        v-for="kb in kbs"
        :key="kb.id"
        type="button"
        class="m-kb-tab"
        :class="{ active: kb.id === activeKbId }"
        @click="selectKb(kb.id)"
      >
        <span>{{ kb.name }}</span>
        <small>{{ visibilityLabel(kb.visibility) }}</small>
      </button>
    </div>

    <div v-if="loading" class="m-kb-loading">
      <el-icon class="is-loading" :size="22"><Loading /></el-icon>
      <span>正在加载知识库…</span>
    </div>

    <template v-else-if="detail">
      <section class="m-kb-summary">
        <div class="m-kb-summary-head">
          <h2>{{ detail.name }}</h2>
          <el-tag
            size="small"
            :type="detail.visibility === 'public' ? 'success' : 'warning'"
            effect="light"
          >
            {{ visibilityLabel(detail.visibility) }}
          </el-tag>
        </div>
        <p class="m-kb-desc">{{ detail.description || '该知识库暂无详细描述。' }}</p>

        <div class="m-kb-stats">
          <div class="m-kb-stat">
            <strong>{{ detail.document_count ?? documents.length }}</strong>
            <span>文档</span>
          </div>
          <div class="m-kb-stat">
            <strong>{{ detail.chunk_count ?? 0 }}</strong>
            <span>知识片段</span>
          </div>
          <div class="m-kb-stat">
            <strong>{{ detail.review_cycle_days || '-' }}</strong>
            <span>复核天数</span>
          </div>
        </div>

        <div class="m-kb-meta">
          <div class="m-kb-meta-row">
            <span>归属部门</span>
            <strong>{{ detail.department_name || '全校' }}</strong>
          </div>
          <div class="m-kb-meta-row">
            <span>责任人</span>
            <strong>{{ detail.owner_name || '未设置' }}</strong>
          </div>
          <div class="m-kb-meta-row">
            <span>分块策略</span>
            <strong>{{ strategyLabel(detail.chunk_strategy) }}</strong>
          </div>
          <div class="m-kb-meta-row">
            <span>最近更新</span>
            <strong>{{ formatDateTime(detail.updated_at) }}</strong>
          </div>
        </div>
      </section>

      <div class="m-kb-section-head">
        <span>收录文档</span>
        <small>仅展示当前生效版本 · {{ documents.length }} 篇</small>
      </div>

      <button
        v-for="doc in documents"
        :key="doc.id"
        type="button"
        class="m-doc-card"
        @click="openPreview(doc)"
      >
        <div class="m-doc-card-head">
          <span class="m-doc-title">{{ doc.title }}</span>
          <el-tag size="small" effect="plain">{{ (doc.file_type || '').toUpperCase() }}</el-tag>
        </div>
        <div class="m-doc-meta">
          <span v-if="doc.doc_no">{{ doc.doc_no }}</span>
          <span v-if="doc.version_label">{{ doc.version_label }}</span>
          <span>生效 {{ formatDate(doc.effective_date) }}</span>
        </div>
        <div class="m-doc-foot">
          <span>{{ formatSize(doc.size_bytes) }}</span>
          <span class="m-doc-action">查看原文 ›</span>
        </div>
      </button>

      <div v-if="documents.length === 0" class="m-kb-empty">该知识库暂无可查看的文档</div>
      <p class="m-kb-note">
        文档内容由各责任部门维护。如发现内容有误或未覆盖，可在问答页提交疑难反馈。
      </p>
    </template>

    <div v-else class="m-kb-empty">
      {{ error || '暂无可查看的知识库，请联系管理员发布知识库或检查账号权限。' }}
    </div>

    <DocumentPreviewDialog
      v-model="previewVisible"
      :document="previewDocument"
      :mobile="true"
    />
  </div>
</template>

<script setup>
import { onActivated, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Loading } from '@element-plus/icons-vue'
import DocumentPreviewDialog from '../../components/DocumentPreviewDialog.vue'
import {
  formatDate,
  formatDateTime,
  formatSize,
  strategyLabel,
  useKnowledgeBase,
  visibilityLabel
} from '../../composables/useKnowledgeBase'

const router = useRouter()
const introOpen = ref(['intro'])
const {
  kbs,
  activeKbId,
  detail,
  documents,
  loading,
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
.m-kb-page {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  padding: 12px 12px calc(18px + env(safe-area-inset-bottom));
  -webkit-overflow-scrolling: touch;
}

.m-kb-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.m-kb-title {
  color: #1f2d3d;
  font-size: 20px;
  font-weight: 600;
}

.m-kb-sub {
  margin-top: 4px;
  color: #909399;
  font-size: 12px;
}

.m-kb-intro {
  margin-bottom: 12px;
  border: 1px solid #dbe7f6;
  border-radius: 10px;
  background: #f3f8ff;
  overflow: hidden;
}

.m-kb-intro :deep(.el-collapse-item__header) {
  padding: 0 12px;
  border: none;
  background: transparent;
  color: #1f2d3d;
  font-size: 14px;
  font-weight: 600;
}

.m-kb-intro :deep(.el-collapse-item__wrap) {
  border: none;
  background: transparent;
}

.m-kb-intro :deep(.el-collapse-item__content) {
  padding: 0 12px 12px;
}

.m-kb-intro p {
  margin: 0 0 10px;
  color: #5c6b7f;
  font-size: 13px;
  line-height: 1.75;
}

.m-kb-intro-item {
  margin-top: 8px;
  padding: 8px 10px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.75);
}

.m-kb-intro-item strong {
  display: block;
  color: #1f2d3d;
  font-size: 12px;
}

.m-kb-intro-item span {
  color: #7a8797;
  font-size: 12px;
  line-height: 1.6;
}

.m-kb-tabs {
  display: flex;
  gap: 8px;
  overflow-x: auto;
  padding-bottom: 8px;
  margin-bottom: 4px;
  -webkit-overflow-scrolling: touch;
}

.m-kb-tab {
  flex: none;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 9px 12px;
  border: 1px solid #e4e7ed;
  border-radius: 10px;
  background: #fff;
  color: #303133;
  text-align: left;
  cursor: pointer;
}

.m-kb-tab span {
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
}

.m-kb-tab small {
  color: #a0aaba;
  font-size: 11px;
}

.m-kb-tab.active {
  border-color: #1f6fd0;
  background: #ecf5ff;
}

.m-kb-tab.active small {
  color: #1f6fd0;
}

.m-kb-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 60px 12px;
  color: #909399;
  font-size: 13px;
}

.m-kb-summary {
  padding: 14px;
  border-radius: 12px;
  background: #fff;
  box-shadow: 0 2px 10px rgba(31, 45, 61, 0.05);
}

.m-kb-summary-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}

.m-kb-summary-head h2 {
  margin: 0;
  color: #1f2d3d;
  font-size: 17px;
  line-height: 1.4;
}

.m-kb-desc {
  margin: 8px 0 12px;
  color: #5c6b7f;
  font-size: 13px;
  line-height: 1.75;
  white-space: pre-wrap;
}

.m-kb-stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
  margin-bottom: 12px;
}

.m-kb-stat {
  padding: 10px 8px;
  border-radius: 9px;
  background: #f7f9fc;
  text-align: center;
}

.m-kb-stat strong {
  display: block;
  color: #1f6fd0;
  font-size: 19px;
  line-height: 1.2;
}

.m-kb-stat span {
  color: #909399;
  font-size: 11px;
}

.m-kb-meta {
  padding-top: 10px;
  border-top: 1px solid #ebeef5;
}

.m-kb-meta-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  padding: 4px 0;
  font-size: 12px;
}

.m-kb-meta-row span {
  flex: none;
  color: #a0aaba;
}

.m-kb-meta-row strong {
  overflow: hidden;
  color: #303133;
  font-weight: 500;
  text-align: right;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.m-kb-section-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  margin: 16px 2px 8px;
}

.m-kb-section-head span {
  color: #1f2d3d;
  font-size: 14px;
  font-weight: 600;
}

.m-kb-section-head small {
  color: #a0aaba;
  font-size: 11px;
}

.m-doc-card {
  display: block;
  width: 100%;
  margin-bottom: 8px;
  padding: 12px;
  border: 1px solid #ebeef5;
  border-radius: 10px;
  background: #fff;
  text-align: left;
  cursor: pointer;
}

.m-doc-card:active {
  background: #f5f9ff;
}

.m-doc-card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}

.m-doc-title {
  color: #1f2d3d;
  font-size: 14px;
  font-weight: 600;
  line-height: 1.5;
}

.m-doc-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
  margin-top: 6px;
  color: #7a8797;
  font-size: 11px;
}

.m-doc-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
  color: #a0aaba;
  font-size: 11px;
}

.m-doc-action {
  color: #1f6fd0;
  font-weight: 600;
}

.m-kb-empty {
  padding: 48px 16px;
  color: #909399;
  font-size: 13px;
  line-height: 1.8;
  text-align: center;
}

.m-kb-note {
  margin: 12px 2px 0;
  color: #a0aaba;
  font-size: 11px;
  line-height: 1.7;
}
</style>
