import React from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import './assets/theme.css'
import './styles/app.css'

// 深色模式：按 localStorage 预置 <html class="dark">，MainLayout 负责切换并持久化
if (localStorage.getItem('app-theme') === 'dark') {
  document.documentElement.classList.add('dark')
}

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>
)