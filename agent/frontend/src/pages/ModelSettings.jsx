import { useEffect, useState } from 'react'
import { getModelConfig, listOllamaModels, saveModelConfig, testModelConfig } from '../api/model'
import { message } from '../utils/toast'

const MODES = [
  {
    key: 'api',
    title: '在线 API',
    sub: 'DeepSeek / OpenAI 兼容',
    desc: '填写 API Key 与接口地址，无需本地算力',
    glyph: '云',
  },
  {
    key: 'ollama',
    title: '本地模型',
    sub: 'Ollama 本地推理',
    desc: '调用本机 Ollama 模型，数据不出本机',
    glyph: '地',
  },
]

const PRESETS = {
  deepseek: {
    title: 'DeepSeek',
    base_url: 'https://api.deepseek.com/v1',
    model: 'deepseek-chat',
    vision_model: 'deepseek-flash',
  },
  openai: {
    title: 'OpenAI',
    base_url: 'https://api.openai.com/v1',
    model: 'gpt-4o-mini',
    vision_model: 'gpt-4o-mini',
  },
  custom: {
    title: '自定义',
  },
}

function modeOf(provider) {
  return provider === 'ollama' ? 'ollama' : 'api'
}

function apiProviderOf(provider) {
  return provider === 'ollama' ? 'deepseek' : provider
}

export default function ModelSettings() {
  const [cfg, setCfg] = useState(null)
  const [form, setForm] = useState(null)
  const [mode, setMode] = useState('api')
  const [models, setModels] = useState([])
  const [modelHint, setModelHint] = useState('')
  const [listing, setListing] = useState(false)
  const [testing, setTesting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testResult, setTestResult] = useState(null)

  useEffect(() => {
    load()
  }, [])

  async function load() {
    try {
      const data = await getModelConfig()
      apply(data)
    } catch (e) {
      /* 拦截器已提示 */
    }
  }

  function apply(data) {
    setCfg(data)
    setForm({ ...data, provider: apiProviderOf(data.provider) })
    setMode(modeOf(data.provider))
  }

  function update(patch) {
    setForm((f) => ({ ...f, ...patch }))
  }

  function switchMode(next) {
    setTestResult(null)
    setMode(next)
    if (next === 'api' && form.provider === 'ollama') update({ provider: 'deepseek' })
  }

  function pickPreset(key) {
    const preset = PRESETS[key]
    const patch = { provider: key }
    if (preset.base_url) {
      patch.base_url = preset.base_url
      patch.model = preset.model
      patch.vision_model = preset.vision_model
    }
    update(patch)
    setTestResult(null)
  }

  function buildPayload() {
    return {
      provider: mode === 'ollama' ? 'ollama' : apiProviderOf(form.provider),
      api_key: form.api_key || '',
      base_url: form.base_url,
      model: form.model,
      vision_model: form.vision_model || '',
      ollama_base_url: form.ollama_base_url,
      ollama_model: form.ollama_model,
      ollama_vision_model: form.ollama_vision_model || '',
      think: !!form.think,
      num_ctx: Number(form.num_ctx) || 8192,
    }
  }

  async function loadModels() {
    setListing(true)
    try {
      const res = await listOllamaModels(form.ollama_base_url)
      setModels(res.models || [])
      if (res.ok) {
        setModelHint(`已发现 ${(res.models || []).length} 个本地模型`)
        if (!form.ollama_model && res.models?.length) update({ ollama_model: res.models[0] })
      } else {
        setModelHint(res.message || '无法连接本地 Ollama')
      }
    } catch (e) {
      setModelHint(e.message || '无法连接本地 Ollama')
    } finally {
      setListing(false)
    }
  }

  async function handleTest() {
    setTesting(true)
    setTestResult(null)
    try {
      const res = await testModelConfig(buildPayload())
      setTestResult(res)
      if (res.ok) message.success(res.message)
      else message.error(res.message)
    } catch (e) {
      /* 拦截器已提示 */
    } finally {
      setTesting(false)
    }
  }

  async function handleSave() {
    if (mode === 'api' && !form.api_key && !cfg?.api_key_set) {
      message.warning('请先填写 API Key')
      return
    }
    setSaving(true)
    try {
      const data = await saveModelConfig(buildPayload())
      apply(data)
      message.success(`模型配置已保存，当前使用${modeOf(data.provider) === 'ollama' ? '本地模型' : '在线 API'}`)
    } catch (e) {
      /* 拦截器已提示 */
    } finally {
      setSaving(false)
    }
  }

  if (!form) {
    return (
      <div className="page">
        <div className="empty-state">加载模型配置…</div>
      </div>
    )
  }

  const currentMode = modeOf(cfg?.provider || form.provider)
  const currentName =
    currentMode === 'ollama' ? cfg?.ollama_model : cfg?.model
  const isOllama = mode === 'ollama'

  return (
    <div className="page">
      <div className="page-head">
        <div className="page-head__left">
          <div className="page-title">模型配置</div>
          <div className="page-subtitle">
            在在线 API 与本地 Ollama 之间切换，保存后下一次提问立即生效，无需重启服务
          </div>
        </div>
        <button className="btn btn--sm" onClick={load}>
          刷新
        </button>
      </div>

      <div className="panel model-current">
        <span className={`model-current__dot${currentMode === 'ollama' ? ' is-local' : ''}`} />
        <span className="model-current__text">
          当前生效：
          <b>{currentMode === 'ollama' ? '本地模型' : '在线 API'}</b>
          <span className="model-current__name">{currentName || '未配置模型'}</span>
        </span>
        {cfg?.updated_at && <span className="model-current__time">更新于 {cfg.updated_at}</span>}
      </div>

      <div className="panel">
        <div className="section-label">选择模型来源</div>
        <div className="model-mode-grid">
          {MODES.map((m) => (
            <button
              key={m.key}
              className={`model-mode${mode === m.key ? ' is-active' : ''}`}
              onClick={() => switchMode(m.key)}
            >
              <span className="model-mode__glyph">{m.glyph}</span>
              <span className="model-mode__body">
                <span className="model-mode__title">{m.title}</span>
                <span className="model-mode__sub">{m.sub}</span>
                <span className="model-mode__desc">{m.desc}</span>
              </span>
            </button>
          ))}
        </div>
      </div>

      {isOllama ? (
        <div className="panel">
          <div className="section-label">本地 Ollama 参数</div>

          <div className="form-row">
            <label className="form-label">Ollama 服务地址</label>
            <input
              className="field"
              value={form.ollama_base_url}
              placeholder="http://127.0.0.1:11434"
              onChange={(e) => update({ ollama_base_url: e.target.value })}
            />
            <div className="form-hint">需先在本机启动 ollama serve</div>
          </div>

          <div className="form-row">
            <label className="form-label">本地模型</label>
            <div className="model-inline">
              <input
                className="field"
                list="ollama-model-options"
                value={form.ollama_model}
                placeholder="qwen3:8b"
                onChange={(e) => update({ ollama_model: e.target.value })}
              />
              <datalist id="ollama-model-options">
                {models.map((m) => (
                  <option key={m} value={m} />
                ))}
              </datalist>
              <button className="btn btn--sm" disabled={listing} onClick={loadModels}>
                {listing ? '读取中…' : '读取本地模型'}
              </button>
            </div>
            {modelHint && <div className="form-hint">{modelHint}</div>}
          </div>

          <div className="form-row">
            <label className="form-label">多模态模型（可选）</label>
            <input
              className="field"
              value={form.ollama_vision_model}
              placeholder="留空则使用上面的本地模型"
              onChange={(e) => update({ ollama_vision_model: e.target.value })}
            />
            <div className="form-hint">带图片提问时使用；需本地已安装支持图片的模型</div>
          </div>

          <div className="form-row">
            <label className="form-label">上下文窗口 num_ctx</label>
            <input
              className="field"
              type="number"
              min={1024}
              step={512}
              value={form.num_ctx}
              onChange={(e) => update({ num_ctx: e.target.value })}
            />
          </div>

          <div className="model-switch-row">
            <div>
              <div className="form-label" style={{ marginBottom: 2 }}>
                输出思维链（think）
              </div>
              <div className="form-hint" style={{ marginTop: 0 }}>
                关闭可明显加快决策速度，qwen3 建议保持关闭
              </div>
            </div>
            <label className="switch">
              <input
                type="checkbox"
                checked={!!form.think}
                onChange={(e) => update({ think: e.target.checked })}
              />
              <span className="switch__track">
                <span className="switch__thumb" />
              </span>
            </label>
          </div>
        </div>
      ) : (
        <div className="panel">
          <div className="section-label">在线 API 参数</div>

          <div className="form-row">
            <label className="form-label">服务商预设</label>
            <div className="preset-tabs">
              {Object.entries(PRESETS).map(([key, p]) => (
                <button
                  key={key}
                  className={`preset-tab${form.provider === key ? ' is-active' : ''}`}
                  onClick={() => pickPreset(key)}
                >
                  {p.title}
                </button>
              ))}
            </div>
            <div className="form-hint">选择 DeepSeek / OpenAI 会填入默认地址与模型，也可用自定义兼容接口</div>
          </div>

          <div className="form-row">
            <label className="form-label">API Key</label>
            <input
              className="field"
              type="password"
              value={form.api_key}
              placeholder={cfg?.api_key_set ? '已保存，留空保持不变' : 'sk-...'}
              onChange={(e) => update({ api_key: e.target.value })}
            />
            <div className="form-hint">
              {cfg?.api_key_set ? '已配置密钥，输入新值可覆盖' : '密钥仅保存在本机后端，不会回传到浏览器'}
            </div>
          </div>

          <div className="form-row">
            <label className="form-label">接口地址 base_url</label>
            <input
              className="field"
              value={form.base_url}
              placeholder="https://api.deepseek.com/v1"
              onChange={(e) => update({ base_url: e.target.value })}
            />
          </div>

          <div className="form-row">
            <label className="form-label">模型名称</label>
            <input
              className="field"
              value={form.model}
              placeholder="deepseek-chat"
              onChange={(e) => update({ model: e.target.value })}
            />
          </div>

          <div className="form-row">
            <label className="form-label">多模态模型（可选）</label>
            <input
              className="field"
              value={form.vision_model}
              placeholder="留空则使用上面的模型"
              onChange={(e) => update({ vision_model: e.target.value })}
            />
            <div className="form-hint">带图片提问时使用；需为支持 image_url 内容块的模型</div>
          </div>
        </div>
      )}

      {testResult && (
        <div className={`panel model-test${testResult.ok ? ' is-ok' : ' is-bad'}`}>
          <span className="model-test__title">{testResult.ok ? '连接成功' : '连接失败'}</span>
          <span className="model-test__msg">{testResult.message}</span>
          {testResult.reply && <span className="model-test__reply">模型回复：{testResult.reply}</span>}
        </div>
      )}

      <div className="save-bar">
        <button className="btn btn--primary" disabled={saving} onClick={handleSave}>
          {saving ? '保存中…' : '保存并应用'}
        </button>
        <button className="btn" disabled={testing} onClick={handleTest}>
          {testing ? '测试中…' : '测试连接'}
        </button>
        <button className="btn btn--ghost" onClick={load}>
          恢复已保存配置
        </button>
      </div>
    </div>
  )
}
