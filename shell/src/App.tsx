import { Routes, Route, Navigate } from 'react-router-dom';
import { AppLayout } from './components/layout/app-layout';
import { Devices } from './pages/devices';
import { DeviceDetail } from './pages/device-detail';
import { Notes } from './pages/notes';
import { NoteDetail } from './pages/note-detail';
import { Goals } from './pages/goals';
import { GoalDetail } from './pages/goal-detail';
import { AgentRunDetail } from './pages/agent-run-detail';
import { Accounts } from './pages/accounts';
import { Data } from './pages/data';
import { KB } from './pages/kb';
import { Chat } from './pages/chat';
import { Alerts } from './pages/alerts';
import { Notifications } from './pages/notifications'; // Phase 1
import { Businesses } from './pages/businesses'; // v0.7+ 业务管理页
import { BusinessComparison } from './pages/business-comparison'; // v0.7+ 多业务对比 dashboard
import { NotFound } from './pages/not-found';
import { Login } from './pages/login';
import { useUIStore } from './stores/ui-store';
import { useEffect } from 'react';

export function App() {
  const theme = useUIStore((s) => s.theme);

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark');
  }, [theme]);

  return (
    <Routes>
      {/* 登录页在 AppLayout 之外：不做前置路由守卫，由 api-client 的 401 拦截驱动跳转 */}
      <Route path="/login" element={<Login />} />
      <Route element={<AppLayout />}>
        <Route path="/" element={<Navigate to="/chat" replace />} />
        <Route path="/chat" element={<Chat />} />
        <Route path="/devices" element={<Devices />} />
        <Route path="/devices/:id" element={<DeviceDetail />} />
        <Route path="/notes" element={<Notes />} />
        <Route path="/notes/:id" element={<NoteDetail />} />
        <Route path="/goals" element={<Goals />} />
        <Route path="/goals/:id" element={<GoalDetail />} />
        <Route path="/agent-runs/:id" element={<AgentRunDetail />} />
        <Route path="/accounts" element={<Accounts />} />
        <Route path="/data" element={<Data />} />
        <Route path="/kb" element={<KB />} />
        <Route path="/alerts" element={<Alerts />} />
        <Route path="/notifications" element={<Notifications />} /> {/* Phase 1 */}
        <Route path="/businesses" element={<Businesses />} /> {/* v0.7+ 业务管理 */}
        <Route
          path="/analytics-comparison"
          element={<BusinessComparison />}
        /> {/* v0.7+ 多业务对比 dashboard */}

        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
