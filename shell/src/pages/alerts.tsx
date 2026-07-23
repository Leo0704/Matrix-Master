import {
  AlertCircle,
  AlertTriangle,
  Info,
  CheckCircle2,
  ScanLine,
  Trash2,
} from 'lucide-react';
import {
  useAlerts,
  useScanAlerts,
  useResolveAlert,
  useDeleteAlert,
  useClearResolvedAlerts,
} from '@/hooks/use-alerts';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { LoadingBlock } from '@/components/common/loading-spinner';
import { PageHeader } from '@/components/common/page-header';
import { ErrorState } from '@/components/common/error-state';
import { EmptyState } from '@/components/common/empty-state';
import { formatRelative } from '@/lib/format';
import { cn } from '@/lib/utils';
import { toast } from '@/components/ui/use-toast';

const ALERT_CODE_LABEL: Record<string, string> = {
  DEVICE_OFFLINE: '设备离线',
  RISK_BLOCKED: '账号风控',
  SELECTOR_NOT_FOUND: '界面元素丢失',
  TAILSCALE_DERP_LOST: '中继连接丢失',
  POSTGRES_DISK_FULL: '数据库磁盘满',
};

export function Alerts() {
  const { data, isLoading, error, refetch } = useAlerts();
  const scanMut = useScanAlerts();
  const resolveMut = useResolveAlert();
  const deleteMut = useDeleteAlert();
  const clearMut = useClearResolvedAlerts();

  async function handleScan() {
    try {
      const result = await scanMut.mutateAsync();
      toast({ title: '扫描完成', description: `新增 ${result.total} 条告警` });
    } catch (e) {
      toast({ title: '扫描失败', description: (e as Error).message, variant: 'destructive' });
    }
  }

  async function handleResolve(id: string) {
    try {
      await resolveMut.mutateAsync({ id, resolver: 'operator' });
      toast({ title: '已标记为处理' });
    } catch (e) {
      toast({ title: '操作失败', description: (e as Error).message, variant: 'destructive' });
    }
  }

  async function handleDelete(id: string) {
    try {
      const result = await deleteMut.mutateAsync(id);
      toast({ title: `已删除 ${result.deleted} 条告警` });
    } catch (e) {
      toast({ title: '删除失败', description: (e as Error).message, variant: 'destructive' });
    }
  }

  async function handleClearResolved() {
    try {
      const result = await clearMut.mutateAsync();
      toast({ title: `已清空 ${result.deleted} 条已处理告警` });
    } catch (e) {
      toast({ title: '清空失败', description: (e as Error).message, variant: 'destructive' });
    }
  }

  const anyIsPending =
    scanMut.isPending || resolveMut.isPending || deleteMut.isPending || clearMut.isPending;

  return (
    <div className="space-y-4">
      <PageHeader
        title="告警"
        description="处理 / 标记 / 扫描 / 清理"
        actions={
          <div className="flex gap-2">
            <Button onClick={handleScan} disabled={anyIsPending}>
              <ScanLine className="mr-1 h-4 w-4" /> 立即扫描
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="text-destructive hover:text-destructive"
              onClick={handleClearResolved}
              disabled={anyIsPending}
            >
              <Trash2 className="mr-1 h-4 w-4" /> 清空已处理
            </Button>
          </div>
        }
      />

      {isLoading && <LoadingBlock />}
      {error && <ErrorState error={error} onRetry={() => refetch()} />}
      {!isLoading && (data?.items.length ?? 0) === 0 && (
        <EmptyState title="无告警" description="主控运行正常" />
      )}

      <div className="space-y-2">
        {data?.items.map((a) => {
          const Icon =
            a.severity === 'critical' ? AlertCircle : a.severity === 'warning' ? AlertTriangle : Info;
          return (
            <Card
              key={a.id}
              className={cn(
                a.severity === 'critical' && 'border-destructive/40',
                a.severity === 'warning' && 'border-warning/40',
                a.resolved && 'opacity-60',
              )}
            >
              <CardContent className="flex items-start gap-3 p-4">
                <Icon
                  className={cn(
                    'mt-0.5 h-5 w-5 shrink-0',
                    a.severity === 'critical' && 'text-destructive',
                    a.severity === 'warning' && 'text-warning',
                    a.severity === 'info' && 'text-muted-foreground',
                  )}
                />
                <div className="flex-1">
                  <p className="text-sm font-medium">{a.message}</p>
                  <p className="text-xs text-muted-foreground">
                    {ALERT_CODE_LABEL[a.code] ?? a.code} · {formatRelative(a.created_at)}
                    {a.subject_id ? ` · ${a.subject_id.slice(0, 8)}` : ''}
                    {a.resolved && ' · 已处理'}
                  </p>
                </div>
                <div className="flex flex-col gap-1">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => handleResolve(a.id)}
                    disabled={a.resolved || anyIsPending}
                  >
                    <CheckCircle2 className="mr-1 h-4 w-4" />
                    {a.resolved ? '已处理' : '标记已处理'}
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-destructive hover:text-destructive"
                    onClick={() => handleDelete(a.id)}
                    disabled={anyIsPending}
                    title="删除"
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
