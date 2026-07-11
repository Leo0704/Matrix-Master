import { Bell, Menu, Moon, Sun } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useUIStore } from '@/stores/ui-store';
import { Button } from '@/components/ui/button';
import { useQuery } from '@tanstack/react-query';
import { apiClient } from '@/lib/api-client';
import type { Health } from '@/types/api';
import { useAlerts } from '@/hooks/use-alerts';
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
  const unreadCount = unresolvedAlerts?.total ?? 0;

  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b bg-card px-4">
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="icon" onClick={toggleSidebar} aria-label="切换侧边栏">
          <Menu className="h-5 w-5" />
        </Button>
        <h1 className="text-lg font-semibold">监控控制台</h1>
      </div>

      <div className="flex items-center gap-2">
        <SystemHealth health={health} />
        <Button variant="ghost" size="icon" onClick={toggleTheme} aria-label="切换主题">
          {theme === 'light' ? <Moon className="h-5 w-5" /> : <Sun className="h-5 w-5" />}
        </Button>
        <Button variant="ghost" size="icon" asChild aria-label="告警">
          <Link to="/alerts" className="relative inline-flex">
            <Bell className="h-5 w-5" />
            {unreadCount > 0 ? (
              <span
                className={cn(
                  'absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-medium text-destructive-foreground',
                )}
              >
                {unreadCount > 99 ? '99+' : unreadCount}
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
    <div className="flex items-center gap-2 rounded-md border bg-background px-3 py-1.5 text-xs">
      <span className={cn('h-2 w-2 rounded-full', color)} />
      <span>
        主控: <span className="font-medium">{status}</span>
      </span>
      {health && (
        <span className="text-muted-foreground">v{health.version}</span>
      )}
    </div>
  );
}
