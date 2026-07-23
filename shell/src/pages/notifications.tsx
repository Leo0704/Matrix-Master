import { useState, useMemo } from 'react';
import { Link } from 'react-router-dom';
import {
  AlertCircle,
  AlertTriangle,
  Info,
  CheckCircle2,
  CheckCheck,
  ChevronDown,
  ChevronUp,
  CalendarDays,
  Trash2,
} from 'lucide-react';
import {
  useNotifications,
  useMarkRead,
  useDeleteNotification,
  useClearReadNotifications,
} from '@/hooks/use-notifications';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { LoadingBlock } from '@/components/common/loading-spinner';
import { PageHeader } from '@/components/common/page-header';
import { ErrorState } from '@/components/common/error-state';
import { EmptyState } from '@/components/common/empty-state';
import { formatRelative } from '@/lib/format';
import { cn } from '@/lib/utils';
import { toast } from '@/components/ui/use-toast';
import type { NotificationSeverity, NotificationItem } from '@/types/api';

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

const PROGRESS_CODES = new Set([
  'goal.round.prepared',
  'goal.round.monitored',
  'goal.round.decided',
  'goal.round.decided.continue',
]);

const NOTIFICATION_CODE_LABEL: Record<string, string> = {
  'goal.round.prepared': '目标已准备',
  'goal.round.finished': '目标轮次结束',
  'goal.round.monitored': '目标监控中',
  'goal.round.decided': '目标决策',
  'goal.round.decided.continue': '目标继续',
  'note.published': '笔记已发布',
  'note.publish_failed': '笔记发布失败',
  'note.collect.done': '数据已采集',
  'note.collect.failed': '数据采集失败',
  'agent.alert': '运行异常',
  'daily.digest': '日报',
  'system.webhook.failed': '网络钩子发送失败',
};

interface GroupedNotification {
  key: string;
  isGroup: boolean;
  items: NotificationItem[];
  head: NotificationItem;
  latestAt: string;
}

function useGroupedNotifications(items: NotificationItem[] | undefined): GroupedNotification[] {
  return useMemo(() => {
    if (!items) return [];
    const digestItems: NotificationItem[] = [];
    const groups = new Map<string, NotificationItem[]>();
    const standalone: GroupedNotification[] = [];

    for (const item of items) {
      if (item.code === 'daily.digest') {
        digestItems.push(item);
        continue;
      }
      const goalId = item.goal_id;
      if (goalId && PROGRESS_CODES.has(item.code)) {
        const key = `goal-progress:${goalId}`;
        const arr = groups.get(key) ?? [];
        arr.push(item);
        groups.set(key, arr);
      } else {
        standalone.push({
          key: item.id,
          isGroup: false,
          items: [item],
          head: item,
          latestAt: item.created_at,
        });
      }
    }

    const grouped: GroupedNotification[] = Array.from(groups.values())
      .filter((arr) => arr.length > 0)
      .map((arr) => {
        arr.sort(
          (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
        );
        const head = arr[0]!;
        return {
          key: `group:${head.goal_id}`,
          isGroup: true,
          items: arr,
          head,
          latestAt: head.created_at,
        };
      });

    const digestGroups: GroupedNotification[] = digestItems.map((item) => ({
      key: `digest:${item.id}`,
      isGroup: false,
      items: [item],
      head: item,
      latestAt: item.created_at,
    }));

    return [...digestGroups, ...grouped, ...standalone].sort(
      (a, b) => new Date(b.latestAt).getTime() - new Date(a.latestAt).getTime()
    );
  }, [items]);
}

function RelatedLinks({ item }: { item: NotificationItem }) {
  const links: { to: string; label: string }[] = [];
  if (item.goal_id) {
    links.push({
      to: `/goals/${item.goal_id}`,
      label: item.goal_name ? `目标「${item.goal_name}」` : '查看目标',
    });
  }
  if (item.note_id) {
    links.push({
      to: `/notes/${item.note_id}`,
      label: item.note_title ? `笔记「${item.note_title}」` : '查看笔记',
    });
  }
  if (item.device_id) {
    links.push({
      to: `/devices/${item.device_id}`,
      label: item.device_name ? `设备「${item.device_name}」` : '查看设备',
    });
  }
  if (links.length === 0) return null;

  return (
    <div className="mt-2 flex flex-wrap gap-2">
      {links.map((l) => (
        <Button key={l.to} variant="link" size="sm" className="h-auto p-0" asChild>
          <Link to={l.to}>{l.label}</Link>
        </Button>
      ))}
    </div>
  );
}

function NotificationCard({
  group,
  onMarkRead,
  onDelete,
  isPending,
}: {
  group: GroupedNotification;
  onMarkRead: (ids?: string[]) => void;
  onDelete: (id: string) => void;
  isPending: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const head = group.head;
  const isDigest = head.code === 'daily.digest';
  const Icon = isDigest ? CalendarDays : SEVERITY_ICON[head.severity];
  const style = isDigest
    ? { ring: 'border-primary/40', icon: 'text-primary' }
    : SEVERITY_STYLE[head.severity];
  const unreadCount = group.items.filter((i) => !i.read_at).length;

  return (
    <Card className={cn(style.ring, unreadCount > 0 && 'bg-accent/30', isDigest && 'bg-primary/5')}>
      <CardContent className="flex items-start gap-3 p-4">
        <Icon className={cn('mt-0.5 h-5 w-5 shrink-0', style.icon)} />
        <div className="flex-1">
          <p className="text-sm font-medium">
            {head.title}
            {group.isGroup && unreadCount > 0 && (
              <span className="ml-2 rounded-full bg-primary px-2 py-0.5 text-xs text-primary-foreground">
                {unreadCount} 条未读
              </span>
            )}
            {isDigest && unreadCount > 0 && (
              <span className="ml-2 rounded-full bg-primary px-2 py-0.5 text-xs text-primary-foreground">
                日报
              </span>
            )}
          </p>
          <p className="mt-1 whitespace-pre-line text-sm text-muted-foreground">{head.body}</p>
          <RelatedLinks item={head} />
          <p className="mt-2 text-xs text-muted-foreground">
            {group.isGroup ? '目标进度 · ' : isDigest ? '人工智能日报 · ' : `${NOTIFICATION_CODE_LABEL[head.code] ?? '系统消息'} · `}
            {formatRelative(head.created_at)}
          </p>
          {group.isGroup && (
            <>
              <Button
                variant="ghost"
                size="sm"
                className="mt-2 h-auto p-0"
                onClick={() => setExpanded((e) => !e)}
              >
                {expanded ? (
                  <>
                    <ChevronUp className="mr-1 h-4 w-4" /> 收起历史
                  </>
                ) : (
                  <>
                    <ChevronDown className="mr-1 h-4 w-4" /> 展开 {group.items.length - 1} 条历史
                  </>
                )}
              </Button>
              {expanded && (
                <ul className="mt-2 space-y-2 border-l pl-3 text-sm text-muted-foreground">
                  {group.items.map((i) => (
                    <li key={i.id} className="flex flex-col">
                      <span className={cn(!i.read_at && 'font-medium text-foreground')}>
                        {i.title}
                      </span>
                      <span className="text-xs">{formatRelative(i.created_at)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </div>
        <div className="flex flex-col gap-1">
          {unreadCount > 0 && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onMarkRead(group.items.filter((i) => !i.read_at).map((i) => i.id))}
              disabled={isPending}
            >
              标为已读
            </Button>
          )}
          <Button
            variant="ghost"
            size="sm"
            className="text-destructive hover:text-destructive"
            onClick={() => onDelete(head.id)}
            disabled={isPending}
            title="删除"
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

export function Notifications() {
  const [tab, setTab] = useState<'all' | 'unread'>('all');
  const unread = tab === 'unread';
  const { data, isLoading, error, refetch } = useNotifications({ unread, limit: 100 });
  const markMut = useMarkRead();
  const deleteMut = useDeleteNotification();
  const clearMut = useClearReadNotifications();
  const grouped = useGroupedNotifications(data?.items);

  async function handleMarkRead(ids?: string[]) {
    try {
      const result = await markMut.mutateAsync(ids);
      toast({ title: ids ? `已标记 ${result.marked} 条` : `全部 ${result.marked} 条已读` });
    } catch (e) {
      toast({ title: '操作失败', description: (e as Error).message, variant: 'destructive' });
    }
  }

  async function handleDelete(id: string) {
    try {
      const result = await deleteMut.mutateAsync(id);
      toast({ title: `已删除 ${result.deleted} 条` });
    } catch (e) {
      toast({ title: '删除失败', description: (e as Error).message, variant: 'destructive' });
    }
  }

  async function handleClearRead() {
    try {
      const result = await clearMut.mutateAsync();
      toast({ title: `已清空 ${result.deleted} 条已读消息` });
    } catch (e) {
      toast({ title: '清空失败', description: (e as Error).message, variant: 'destructive' });
    }
  }

  const anyIsPending = markMut.isPending || deleteMut.isPending || clearMut.isPending;

  return (
    <div className="space-y-4">
      <PageHeader
        title="消息"
        description="运营进度与结果"
        actions={
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
              disabled={anyIsPending}
            >
              <CheckCheck className="mr-1 h-4 w-4" /> 全部标为已读
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="text-destructive hover:text-destructive"
              onClick={handleClearRead}
              disabled={anyIsPending}
            >
              <Trash2 className="mr-1 h-4 w-4" /> 清空已读
            </Button>
          </div>
        }
      />

      {isLoading && <LoadingBlock />}
      {error && <ErrorState error={error} onRetry={() => refetch()} />}
      {!isLoading && (data?.items.length ?? 0) === 0 && (
        <EmptyState title={unread ? '没有未读消息' : '暂无消息'} description="" />
      )}

      <div className="space-y-2">
        {grouped.map((g) => (
          <NotificationCard
            key={g.key}
            group={g}
            onMarkRead={handleMarkRead}
            onDelete={handleDelete}
            isPending={anyIsPending}
          />
        ))}
      </div>
    </div>
  );
}
