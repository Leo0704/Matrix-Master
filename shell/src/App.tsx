import { Routes, Route, Navigate } from 'react-router-dom';
import { AppLayout } from './components/layout/app-layout';
import { Devices } from './pages/devices';
import { DeviceDetail } from './pages/device-detail';
import { Notes } from './pages/notes';
import { NoteDetail } from './pages/note-detail';
import { Goals } from './pages/goals';
import { GoalDetail } from './pages/goal-detail';
import { AgentRuns } from './pages/agent-runs';
import { AgentRunDetail } from './pages/agent-run-detail';
import { Data } from './pages/data';
import { KB } from './pages/kb';
import { Chat } from './pages/chat';
import { Alerts } from './pages/alerts';
import { Notifications } from './pages/notifications'; // Phase 1
import { NotFound } from './pages/not-found';
import { useUIStore } from './stores/ui-store';
import { useEffect } from 'react';

export function App() {
  const theme = useUIStore((s) => s.theme);

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark');
  }, [theme]);

  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route path="/" element={<Navigate to="/chat" replace />} />
        <Route path="/chat" element={<Chat />} />
        <Route path="/devices" element={<Devices />} />
        <Route path="/devices/:id" element={<DeviceDetail />} />
        <Route path="/notes" element={<Notes />} />
        <Route path="/notes/:id" element={<NoteDetail />} />
        <Route path="/goals" element={<Goals />} />
        <Route path="/goals/:id" element={<GoalDetail />} />
        <Route path="/agent-runs" element={<AgentRuns />} />
        <Route path="/agent-runs/:id" element={<AgentRunDetail />} />
        <Route path="/data" element={<Data />} />
        <Route path="/kb" element={<KB />} />
        <Route path="/alerts" element={<Alerts />} />
        <Route path="/notifications" element={<Notifications />} /> {/* Phase 1 */}

        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
