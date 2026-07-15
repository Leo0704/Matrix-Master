import { useState } from 'react';
import {
  AlertCircle,
  AlertTriangle,
  Info,
  CheckCircle2,
  CheckCheck,
} from 'lucide-react';
import { useNotifications, useMarkRead } from '@/hooks/use-notifications';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { LoadingBlock } from '@/components/common/loading-spinner';
import { ErrorState } from '@/components/common/error-state';
import { EmptyState } from '@/components/common/empty-state';
import { formatRelative } from '@/lib/format';
import { cn } from '@/lib/utils';
import { toast } from '@/components/ui/use-toast';
import type { NotificationSeverity } from '@/types/api';

const SEVERITY_ICON: Record<NotificationSeverity, typeof Info> = {
  error: AlertCircle,
  warning: AlertTriangle,
  info: Info,
  success: CheckCircle2,
};

const SEVERITY_STYLE: Record<NotificationSeverity, { ring: string; icon: string }> = {
  error: { ring: 'border-destructive/40', icon: 'text-destructive' },
  warning: { ring: 'border-warning/40', icon: 'text-warning' },
  info: { ring: 'border-border', icon: 'text-muted-foreground' },
  success: { ring: 'border-success/40', icon: 'text-success' },
};

export function Notifications() {
  const [tab, setTab] = useState<'all' | 'unread'>('all');
  const unread = tab === 'unread';
  const { data, isLoading, error, refetch } = useNotifications({ unread, limit: 100 });
  const markMut = useMarkRead();

  async function handleMarkRead(ids?: string[]) {
    try {
      const result = await markMut.mutateAsync(ids);
      toast({ title: ids ? `已标记 ${result.marked} 条` : `全部 ${result.marked} 条已读` });
    } catch (e) {
      toast({ title: '操作失败', description: (e as Error).message, variant: 'destructive' });
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">消息</h1>
          <p className="text-sm text-muted-foreground">运营进度与结果</p>
        </div>
        <div className="flex gap-2">
          <Button
            variant={tab === 'all' ? 'default' : 'outline'}
            size="sm"
            onClick={() => setTab('all')}
          >
            全部
          </Button>
          <Button
            variant={tab === 'unread' ? 'default' : 'outline'}
            size="sm"
            onClick={() => setTab('unread')}
          >
            未读
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => handleMarkRead()}
            disabled={markMut.isPending}
          >
            <CheckCheck className="mr-1 h-4 w-4" /> 全部标为已读
          </Button>
        </div>
      </div>

      {isLoading && <LoadingBlock />}
      {error && <ErrorState error={error} onRetry={() => refetch()} />}
      {!isLoading && (data?.items.length ?? 0) === 0 && (
        <EmptyState title={unread ? '没有未读消息' : '暂无消息'} description="" />
      )}

      <div className="space-y-2">
        {data?.items.map((n) => {
          const Icon = SEVERITY_ICON[n.severity];
          const style = SEVERITY_STYLE[n.severity];
          return (
            <Card
              key={n.id}
              className={cn(style.ring, !n.read_at && 'bg-accent/30')}
            >
              <CardContent className="flex items-start gap-3 p-4">
                <Icon className={cn('mt-0.5 h-5 w-5 shrink-0', style.icon)} />
                <div className="flex-1">
                  <p className="text-sm font-medium">{n.title}</p>
                  <p className="mt-1 text-sm text-muted-foreground">{n.body}</p>
                  <p className="mt-2 text-xs text-muted-foreground">
                    {n.code} · {formatRelative(n.created_at)}
                  </p>
                </div>
                {!n.read_at && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => handleMarkRead([n.id])}
                    disabled={markMut.isPending}
                  >
                    标为已读
                  </Button>
                )}
              </CardContent>
            </Card>
          );
        })}
      </div>
    </div>
  );
}