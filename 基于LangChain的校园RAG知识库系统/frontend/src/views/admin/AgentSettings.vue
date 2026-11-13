<template>
  <div class="page">
    <div class="page-head">
      <h2>深度问答</h2>
      <el-button @click="load">刷新</el-button>
    </div>

    <el-alert
      title="深度问答默认关闭，按知识库与角色白名单开放，最多三轮决策，每次检索仍受权限与版本过滤约束"
      type="info"
      :closable="false"
      style="margin-bottom: 12px"
    />

    <el-card>
      <template #header>开放范围</template>
      <el-form label-width="150px" style="max-width: 720px">
        <el-form-item label="总开关">
          <el-switch v-model="form.enabled" />
        </el-form-item>
        <el-form-item label="开放角色">
          <el-checkbox-group v-model="form.roles">
            <el-checkbox value="admin">系统管理员</el-checkbox>
            <el-checkbox value="dept_admin">部门管理员</el-checkbox>
            <el-checkbox value="staff">教职工</el-checkbox>
            <el-checkbox value="student">学生</el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item label="开放知识库">
          <el-select v-model="form.kb_ids" multiple collapse-tags clearable placeholder="留空表示全部知识库" style="width: 100%">
            <el-option v-for="item in knowledgeBases" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="最大决策轮数">
          <el-input-number v-model="form.max_rounds" :min="1" :max="3" />
          <span class="muted" style="margin-left: 12px">最多三轮，达到上限后强制作答</span>
        </el-form-item>
        <el-form-item label="最大检索次数">
          <el-input-number v-model="form.max_retrievals" :min="1" :max="3" />
        </el-form-item>
        <el-form-item label="单轮超时（秒）">
          <el-input-number v-model="form.timeout_seconds" :min="10" :max="180" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="saving" @click="save">保存设置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card style="margin-top: 12px">
      <template #header>运行说明</template>
      <ul class="muted" style="line-height: 1.9; margin: 0; padding-left: 20px">
        <li>深度问答不进入热点缓存，回答与轨迹单独落库。</li>
        <li>模型不可用、超时或循环异常时自动回退快速问答，引用口径保持一致。</li>
        <li>用户端只在当前知识库开放且角色命中白名单时显示深度问答开关。</li>
      </ul>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getAgentSettings, updateAgentSettings } from '../../api/agent'

const knowledgeBases = ref([])
const saving = ref(false)
const form = reactive({
  enabled: false,
  roles: ['admin', 'dept_admin'],
  kb_ids: [],
  max_rounds: 3,
  max_retrievals: 3,
  timeout_seconds: 90
})

async function load() {
  try {
    const data = await getAgentSettings()
    knowledgeBases.value = data.knowledge_bases || []
    Object.assign(form, {
      enabled: Boolean(data.enabled),
      roles: data.roles || [],
      kb_ids: data.kb_ids || [],
      max_rounds: Number(data.max_rounds || 3),
      max_retrievals: Number(data.max_retrievals || 3),
      timeout_seconds: Number(data.timeout_seconds || 90)
    })
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '深度问答设置加载失败')
  }
}

async function save() {
  saving.value = true
  try {
    await updateAgentSettings({ ...form })
    ElMessage.success('深度问答设置已保存')
    await load()
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>
