import { AlertCircle, AlertTriangle, Info, CheckCircle2, ScanLine } from 'lucide-react';
import { useAlerts, useScanAlerts, useResolveAlert } from '@/hooks/use-alerts';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { LoadingBlock } from '@/components/common/loading-spinner';
import { ErrorState } from '@/components/common/error-state';
import { EmptyState } from '@/components/common/empty-state';
import { formatRelative } from '@/lib/format';
import { cn } from '@/lib/utils';
import { toast } from '@/components/ui/use-toast';

export function Alerts() {
  const { data, isLoading, error, refetch } = useAlerts();
  const scanMut = useScanAlerts();
  const resolveMut = useResolveAlert();

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

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">告警</h1>
          <p className="text-sm text-muted-foreground">处理 / 标记 / 扫描</p>
        </div>
        <Button onClick={handleScan} disabled={scanMut.isPending}>
          <ScanLine className="mr-1 h-4 w-4" /> 立即扫描
        </Button>
      </div>

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
                    {a.code} · {formatRelative(a.created_at)}
                    {a.subject_id ? ` · ${a.subject_id.slice(0, 8)}` : ''}
                    {a.resolved && ' · 已处理'}
                  </p>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => handleResolve(a.id)}
                  disabled={a.resolved || resolveMut.isPending}
                >
                  <CheckCircle2 className="mr-1 h-4 w-4" />
                  {a.resolved ? '已处理' : '标记已处理'}
                </Button>
              </CardContent>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
