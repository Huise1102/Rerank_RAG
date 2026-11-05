// 轻量 toast：替代 Element 的 ElMessage，无框架依赖。
let counter = 0

export function toast(type, text) {
  const el = document.createElement('div')
  el.className = `app-toast app-toast--${type}`
  el.textContent = text
  document.body.appendChild(el)
  counter += 1
  requestAnimationFrame(() => el.classList.add('is-visible'))
  window.setTimeout(() => {
    el.classList.remove('is-visible')
    window.setTimeout(() => el.remove(), 300)
  }, 2600)
}

export const message = {
  success: (t) => toast('success', t),
  error: (t) => toast('error', t),
  info: (t) => toast('info', t),
  warning: (t) => toast('warning', t),
}