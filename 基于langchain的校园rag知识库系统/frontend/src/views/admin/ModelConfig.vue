<template>
  <div class="page">
    <div class="page-header">
      <div class="muted">在在线 API 与本地模型之间切换，保存后下一次提问立即生效，无需重启后端</div>
      <div>
        <el-button @click="load">刷新</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存并应用</el-button>
      </div>
    </div>

    <el-alert
      v-if="current"
      type="success"
      :closable="false"
      style="margin-bottom: 12px"
      :title="`当前生效：${current.provider === 'ollama' ? '本地模型' : '在线 API'} · ${current.active_model}`"
      :description="`接口地址 ${current.active_base_url}${current.updated_at ? `，更新于 ${current.updated_at}` : ''}`"
    />

    <div class="mode-grid">
      <div
        v-for="item in modes"
        :key="item.key"
        class="mode-card"
        :class="{ 'is-active': form && form.provider === item.key }"
        @click="switchMode(item.key)"
      >
        <div class="mode-glyph">{{ item.glyph }}</div>
        <div>
          <div class="mode-title">{{ item.title }}</div>
          <div class="mode-sub">{{ item.sub }}</div>
          <div class="muted">{{ item.desc }}</div>
        </div>
      </div>
    </div>

    <el-card v-if="form" shadow="never">
      <el-form label-position="left" label-width="120px">
        <template v-if="form.provider === 'api'">
          <el-form-item label="服务商预设">
            <el-radio-group v-model="preset" @change="applyPreset">
              <el-radio-button value="deepseek">DeepSeek</el-radio-button>
              <el-radio-button value="openai">OpenAI</el-radio-button>
              <el-radio-button value="custom">自定义</el-radio-button>
            </el-radio-group>
            <span class="muted form-hint">选择预设会填入默认接口地址与模型，自定义用于其他 OpenAI 兼容服务</span>
          </el-form-item>

          <el-form-item label="API Key">
            <el-tag :type="form.api_key_configured ? 'success' : 'danger'">
              {{ form.api_key_configured ? '已配置' : '未配置' }}
            </el-tag>
            <span class="muted form-hint">密钥只从项目根 .env 读取，不在页面保存</span>
          </el-form-item>

          <el-form-item label="接口地址">
            <el-input v-model="form.api_base_url" placeholder="https://api.deepseek.com/v1" />
          </el-form-item>

          <el-form-item label="模型名称">
            <el-input v-model="form.api_model" placeholder="deepseek-chat" />
          </el-form-item>
        </template>

        <template v-else>
          <el-form-item label="服务地址">
            <el-input v-model="form.ollama_base_url" placeholder="http://127.0.0.1:11434" />
            <span class="muted form-hint">需先在本机执行 ollama serve</span>
          </el-form-item>

          <el-form-item label="本地模型">
            <div class="model-row">
              <el-select
                v-model="form.ollama_model"
                filterable
                allow-create
                default-first-option
                placeholder="qwen3:8b"
                style="flex: 1"
              >
                <el-option v-for="item in models" :key="item" :label="item" :value="item" />
              </el-select>
              <el-button :loading="loadingModels" @click="loadModels">读取本地模型</el-button>
            </div>
            <span class="muted form-hint">{{ modelHint || '可手动输入模型名，或先读取本地已安装模型' }}</span>
          </el-form-item>
        </template>

        <el-form-item label="温度">
          <el-input-number v-model="form.temperature" :min="0" :max="2" :step="0.1" />
        </el-form-item>

        <el-form-item label="最大输出 token">
          <el-input-number v-model="form.max_tokens" :min="64" :max="8192" :step="128" />
        </el-form-item>
      </el-form>

      <div class="form-actions">
        <el-button type="primary" :loading="saving" @click="save">保存并应用</el-button>
        <el-button :loading="testing" @click="test">测试连接</el-button>
      </div>

      <el-alert
        v-if="testResult"
        :type="testResult.ok ? 'success' : 'error'"
        :closable="false"
        style="margin-top: 12px"
        :title="testResult.ok ? '连接成功' : '连接失败'"
        :description="testResult.reply ? `${testResult.message}，模型回复：${testResult.reply}` : testResult.message"
      />
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  getModelConfig,
  listOllamaModels,
  saveModelConfig,
  testModelConfig
} from '../../api/knowledge'

const modes = [
  {
    key: 'api',
    title: '在线 API',
    sub: 'DeepSeek / OpenAI 兼容',
    desc: '调用云端接口，无需本地算力',
    glyph: '云'
  },
  {
    key: 'ollama',
    title: '本地模型',
    sub: 'Ollama 本地推理',
    desc: '调用本机已安装模型，数据不出本机',
    glyph: '地'
  }
]

const PRESETS = {
  deepseek: { base_url: 'https://api.deepseek.com/v1', model: 'deepseek-chat' },
  openai: { base_url: 'https://api.openai.com/v1', model: 'gpt-4o-mini' }
}

const form = ref(null)
const current = ref(null)
const preset = ref('custom')
const models = ref([])
const modelHint = ref('')
const loadingModels = ref(false)
const saving = ref(false)
const testing = ref(false)
const testResult = ref(null)

function detectPreset(data) {
  if (data.api_base_url === PRESETS.deepseek.base_url) return 'deepseek'
  if (data.api_base_url === PRESETS.openai.base_url) return 'openai'
  return 'custom'
}

async function load() {
  const data = await getModelConfig()
  current.value = data
  form.value = { ...data }
  preset.value = detectPreset(data)
}

function switchMode(mode) {
  testResult.value = null
  form.value.provider = mode
}

function applyPreset(key) {
  testResult.value = null
  const target = PRESETS[key]
  if (!target) return
  form.value.api_base_url = target.base_url
  form.value.api_model = target.model
}

function buildPayload() {
  return {
    provider: form.value.provider,
    api_base_url: form.value.api_base_url,
    api_model: form.value.api_model,
    ollama_base_url: form.value.ollama_base_url,
    ollama_model: form.value.ollama_model,
    temperature: form.value.temperature,
    max_tokens: form.value.max_tokens
  }
}

async function loadModels() {
  loadingModels.value = true
  modelHint.value = ''
  try {
    const result = await listOllamaModels(form.value.ollama_base_url)
    models.value = result.models || []
    if (result.ok) {
      modelHint.value = `已发现 ${models.value.length} 个本地模型`
      if (!form.value.ollama_model && models.value.length) {
        form.value.ollama_model = models.value[0]
      }
    } else {
      modelHint.value = result.message || '无法连接本地 Ollama'
    }
  } finally {
    loadingModels.value = false
  }
}

async function save() {
  saving.value = true
  try {
    const data = await saveModelConfig(buildPayload())
    current.value = data
    form.value = { ...data }
    ElMessage.success(`模型配置已保存，当前使用${data.provider === 'ollama' ? '本地模型' : '在线 API'}`)
  } finally {
    saving.value = false
  }
}

async function test() {
  testing.value = true
  testResult.value = null
  try {
    testResult.value = await testModelConfig(buildPayload())
    if (testResult.value.ok) {
      ElMessage.success(testResult.value.message)
    } else {
      ElMessage.error(testResult.value.message)
    }
  } finally {
    testing.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.mode-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 12px;
}

.mode-card {
  display: flex;
  gap: 12px;
  padding: 12px 16px;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  background: #fff;
  cursor: pointer;
  transition: border-color 0.2s, box-shadow 0.2s;
}

.mode-card:hover {
  border-color: #409eff;
}

.mode-card.is-active {
  border-color: #409eff;
  box-shadow: 0 0 0 2px rgba(64, 158, 255, 0.15);
}

.mode-glyph {
  flex: none;
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: #ecf5ff;
  color: #409eff;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
}

.mode-title {
  font-weight: 600;
  margin-bottom: 2px;
}

.mode-sub {
  font-size: 13px;
  color: #606266;
  margin-bottom: 2px;
}

.model-row {
  display: flex;
  gap: 8px;
  width: 100%;
}

.form-hint {
  margin-left: 12px;
}

.form-actions {
  display: flex;
  gap: 12px;
  padding-left: 120px;
}

:deep(.el-form-item) {
  margin-bottom: 14px;
}
</style>
