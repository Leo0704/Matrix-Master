import { useState } from 'react';
import { Bell, Inbox, Menu, Moon, Sun, Briefcase } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useUIStore, useActiveBusinessId, useSetActiveBusinessId } from '@/stores/ui-store';
import { Button } from '@/components/ui/button';
import { useQuery } from '@tanstack/react-query';
import { apiClient } from '@/lib/api-client';
import type { Health } from '@/types/api';
import { useAlerts } from '@/hooks/use-alerts';
import { useUnreadNotificationCount } from '@/hooks/use-notifications';
import { useBusinesses } from '@/hooks/use-businesses';
import { cn } from '@/lib/utils';

export function Topbar() {
  const { toggleSidebar, theme, toggleTheme } = useUIStore();
  const activeBusinessId = useActiveBusinessId();
  const setActiveBusinessId = useSetActiveBusinessId();

  const { data: health } = useQuery<Health>({
    queryKey: ['health'],
    queryFn: () => apiClient.get<Health>('/health'),
    refetchInterval: 10_000,
    retry: false,
  });

  // v0.7+ 业务模型重构：列出 active 业务
  const { data: businesses } = useBusinesses({ status: 'active' });
  const activeItems = (businesses?.items ?? []).filter((b) => b.status === 'active');
  const activeBusiness = activeItems.find((b) => b.id === activeBusinessId);

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
        {/* v0.7+ 业务切换器：下拉选 active business；切业务触发 matrix:business-changed 事件 */}
        <BusinessSelector
          businesses={activeItems}
          activeId={activeBusinessId}
          activeName={activeBusiness?.name ?? null}
          onChange={setActiveBusinessId}
        />
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

/**
 * BusinessSelector — 业务下拉（v0.7+ 业务模型重构）。
 *
 * 显示当前活跃业务名称；下拉切换触发：
 * 1) 写入 ui-store（localStorage 持久化）
 * 2) 派发 window 'matrix:business-changed' 事件 → chat 页响应 reload 历史
 */
export function BusinessSelector({
  businesses,
  activeId,
  activeName,
  onChange,
}: {
  businesses: { id: string; name: string; slug: string }[];
  activeId: string | null;
  activeName: string | null;
  onChange: (id: string | null) => void;
}) {
  const [open, setOpen] = useState(false);

  function handleSelect(id: string) {
    onChange(id);
    setOpen(false);
    // 通知其他组件（chat 页）业务变了
    if (typeof window !== 'undefined') {
      window.dispatchEvent(
        new CustomEvent('matrix:business-changed', { detail: { businessId: id } }),
      );
    }
  }

  return (
    <div className="relative ml-2">
      <Button
        variant="outline"
        size="sm"
        onClick={() => setOpen((o) => !o)}
        className="gap-2"
      >
        <Briefcase className="h-4 w-4" />
        <span className="truncate max-w-[160px]">
          {activeName ?? '选择业务'}
        </span>
      </Button>
      {open && (
        <>
          {/* 点外面关闭 */}
          <div
            className="fixed inset-0 z-10"
            onClick={() => setOpen(false)}
          />
          <div className="absolute left-0 top-full z-20 mt-1 w-56 rounded-md border bg-card shadow-lg">
            {businesses.length === 0 ? (
              <div className="p-3 text-sm text-muted-foreground">
                还没有业务 —{' '}
                <Link
                  to="/businesses"
                  className="text-primary underline"
                  onClick={() => setOpen(false)}
                >
                  去新建
                </Link>
              </div>
            ) : (
              <ul className="max-h-72 overflow-y-auto py-1">
                {businesses.map((b) => (
                  <li key={b.id}>
                    <button
                      className={cn(
                        'flex w-full items-center justify-between px-3 py-2 text-sm hover:bg-accent',
                        b.id === activeId && 'bg-accent/50 font-medium',
                      )}
                      onClick={() => handleSelect(b.id)}
                    >
                      <span className="truncate">{b.name}</span>
                      {b.id === activeId && (
                        <span className="ml-2 text-xs text-primary">当前</span>
                      )}
                    </button>
                  </li>
                ))}
                <li className="border-t">
                  <Link
                    to="/businesses"
                    className="block px-3 py-2 text-sm text-primary hover:bg-accent"
                    onClick={() => setOpen(false)}
                  >
                    + 管理业务（建/归档）
                  </Link>
                </li>
              </ul>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function healthStatusLabel(status: string): string {
  switch (status) {
    case 'ok':
      return '正常';
    case 'degraded':
      return '降级';
    case 'down':
      return '宕机';
    default:
      return '未知';
  }
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
        主控: <span className="font-medium">{healthStatusLabel(status)}</span>
      </span>
      {health && (
        <span className="whitespace-nowrap text-muted-foreground">v{health.version}</span>
      )}
    </div>
  );
}
