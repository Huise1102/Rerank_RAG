import { Navigate, Route, Routes } from 'react-router-dom'
import MainLayout from './components/MainLayout'
import AgentChat from './pages/AgentChat'
import KnowledgeBase from './pages/KnowledgeBase'
import RetrievalStrategy from './pages/RetrievalStrategy'
import Logs from './pages/Logs'

export default function AppRoutes() {
  return (
    <Routes>
      <Route element={<MainLayout />}>
        <Route path="/" element={<Navigate to="/agent" replace />} />
        <Route path="/agent" element={<AgentChat />} />
        <Route path="/knowledge" element={<KnowledgeBase />} />
        <Route path="/strategy" element={<RetrievalStrategy />} />
        <Route path="/logs" element={<Logs />} />
        <Route path="*" element={<Navigate to="/agent" replace />} />
      </Route>
    </Routes>
  )
}