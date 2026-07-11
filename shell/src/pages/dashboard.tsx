import { Smartphone, Users, FileText, Activity } from 'lucide-react';
import { useMetricsSummary } from '@/hooks/use-metrics';
import { useDevices } from '@/hooks/use-devices';
import { useNotes } from '@/hooks/use-notes';
import { useAgentRuns } from '@/hooks/use-agent-runs';
import { KpiCard } from '@/components/dashboard/kpi-card';
import { DeviceStatusGrid } from '@/components/dashboard/device-status-grid';
import { AccountRiskChart } from '@/components/dashboard/account-risk-chart';
import { TaskThroughputChart } from '@/components/dashboard/task-throughput-chart';
import { AlertsFeed } from '@/components/dashboard/alerts-feed';
import { ChatInput } from '@/components/chat/chat-input';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { formatState } from '@/types/api';

export function Dashboard() {
  const metrics = useMetricsSummary();
  const devices = useDevices();
  const notes = useNotes({ limit: 6 });
  const runs = useAgentRuns({ limit: 4 });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">总览</h1>
        <p className="text-sm text-muted-foreground">主控核心指标 + 异常告警</p>
      </div>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-5">
        <KpiCard
          label="设备"
          value={metrics.data?.devices?.total ?? '—'}
          hint={`${metrics.data?.devices?.active ?? 0} 在线`}
          icon={Smartphone}
        />
        <KpiCard
          label="账号"
          value={metrics.data?.accounts?.total ?? '—'}
          hint={`${metrics.data?.accounts?.high_risk ?? 0} 高风险`}
          icon={Users}
        />
        <KpiCard
          label="任务 24h"
          value={metrics.data?.tasks?.success_24h ?? '—'}
          hint={`${metrics.data?.tasks?.failed_24h ?? 0} 失败`}
          icon={Activity}
        />
        <KpiCard
          label="笔记数"
          value={notes.data?.total ?? notes.data?.items?.length ?? '—'}
          icon={FileText}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          {devices.data && <DeviceStatusGrid devices={devices.data.items} />}
          {devices.isLoading && <Skeleton className="h-64 w-full" />}
        </div>
        <AlertsFeed />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <TaskThroughputChart />
        <AccountRiskChart />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="lg:col-span-1">
          <AccountRiskChart />
        </div>
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle className="text-base">最近 AI 运行</CardTitle>
          </CardHeader>
          <CardContent>
            {runs.isLoading && <Skeleton className="h-32 w-full" />}
            {runs.data && runs.data.items?.length === 0 && (
              <p className="text-sm text-muted-foreground">无运行记录</p>
            )}
            <ul className="space-y-2">
              {runs.data?.items?.map((r) => (
                <li
                  key={r.id}
                  className="flex items-center justify-between rounded-md border p-2 text-sm"
                >
                  <span className="font-mono text-xs">{r.id.slice(0, 8)}…</span>
                  <span className="text-muted-foreground">state: {formatState(r.current_state)}</span>
                  <span>{r.status}</span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">自然语言指令</CardTitle>
        </CardHeader>
        <CardContent>
          <ChatInput />
        </CardContent>
      </Card>
    </div>
  );
}
