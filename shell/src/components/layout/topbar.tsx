import { Bell, Inbox, Menu, Moon, Sun } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useUIStore } from '@/stores/ui-store';
import { Button } from '@/components/ui/button';
import { useQuery } from '@tanstack/react-query';
import { apiClient } from '@/lib/api-client';
import type { Health } from '@/types/api';
import { useAlerts } from '@/hooks/use-alerts';
import { useUnreadNotificationCount } from '@/hooks/use-notifications';
import { cn } from '@/lib/utils';

export function Topbar() {
  const { toggleSidebar, theme, toggleTheme } = useUIStore();

  const { data: health } = useQuery<Health>({
    queryKey: ['health'],
    queryFn: () => apiClient.get<Health>('/health'),
    refetchInterval: 10_000,
    retry: false,
  });

  // 未读 alert 角标（resolved=false）；30s 轮询
  const { data: unresolvedAlerts } = useAlerts({ resolved: false });
  const unreadAlertCount = unresolvedAlerts?.total ?? 0;
  // Phase 1: 通知未读角标
  const unreadNotifCount = useUnreadNotificationCount();

  return (
    <header className="flex h-14 shrink-0 items-center justify-between gap-2 border-b bg-card px-4">
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <Button variant="ghost" size="icon" onClick={toggleSidebar} aria-label="切换侧边栏">
          <Menu className="h-5 w-5" />
        </Button>
        <h1 className="truncate text-lg font-semibold">监控控制台</h1>
      </div>

      <div className="flex min-w-0 shrink-0 items-center gap-1 sm:gap-2">
        <SystemHealth health={health} />
        <Button variant="ghost" size="icon" onClick={toggleTheme} aria-label="切换主题">
          {theme === 'light' ? <Moon className="h-5 w-5" /> : <Sun className="h-5 w-5" />}
        </Button>
        <Button variant="ghost" size="icon" asChild aria-label="告警">
          <Link to="/alerts" className="relative inline-flex">
            <Bell className="h-5 w-5" />
            {unreadAlertCount > 0 ? (
              <span
                className={cn(
                  'absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-medium text-destructive-foreground',
                )}
              >
                {unreadAlertCount > 99 ? '99+' : unreadAlertCount}
              </span>
            ) : (
              <span
                className={cn(
                  'absolute right-1 top-1 h-2 w-2 rounded-full bg-destructive',
                  health?.status === 'ok' && 'opacity-50',
                )}
              />
            )}
          </Link>
        </Button>
        {/* Phase 1: 通知中心 — 运营进度与结果 */}
        <Button variant="ghost" size="icon" asChild aria-label="消息">
          <Link to="/notifications" className="relative inline-flex">
            <Inbox className="h-5 w-5" />
            {unreadNotifCount > 0 && (
              <span
                className={cn(
                  'absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-success px-1 text-[10px] font-medium text-success-foreground',
                )}
              >
                {unreadNotifCount > 99 ? '99+' : unreadNotifCount}
              </span>
            )}
          </Link>
        </Button>
      </div>
    </header>
  );
}

function SystemHealth({ health }: { health?: Health }) {
  const status = health?.status ?? 'unknown';
  const color =
    status === 'ok'
      ? 'bg-success'
      : status === 'degraded'
        ? 'bg-warning'
        : status === 'down'
          ? 'bg-destructive'
          : 'bg-muted-foreground';
  return (
    <div className="hidden sm:flex items-center gap-2 rounded-md border bg-background px-2 py-1.5 text-xs">
      <span className={cn('h-2 w-2 rounded-full', color)} />
      <span className="whitespace-nowrap">
        主控: <span className="font-medium">{status}</span>
      </span>
      {health && (
        <span className="whitespace-nowrap text-muted-foreground">v{health.version}</span>
      )}
    </div>
  );
}
