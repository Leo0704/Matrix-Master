import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Plus } from 'lucide-react';
import { useGoals } from '@/hooks/use-goals';
import { GoalForm } from '@/components/goals/goal-form';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { StatusBadge } from '@/components/common/status-badge';
import { PageHeader } from '@/components/common/page-header';
import { ErrorState } from '@/components/common/error-state';
import { EmptyState } from '@/components/common/empty-state';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { formatDate, formatRelative, GOAL_TYPE_LABEL } from '@/lib/format';
import { useActiveBusinessId, useUIStore } from '@/stores/ui-store';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';

export function Goals() {
  const activeBusinessId = useActiveBusinessId();
  const { data, isLoading, error, refetch } = useGoals(
    activeBusinessId ? { business_id: activeBusinessId } : undefined,
  );
  const [open, setOpen] = useState(false);

  const items = data?.items ?? [];
  const hasActiveGoal = items.some(
    (g) => g.status === 'active' && g.phase !== 'DONE',
  );

  // v0.7+ 业务过滤提示
  const filterTip = activeBusinessId ? (
    <span className="text-xs text-muted-foreground">
      当前仅显示所选业务的目标，
      <button
        className="text-primary underline"
        onClick={() => useUIStore.getState().setActiveBusinessId(null)}
      >
        查看全部
      </button>
    </span>
  ) : null;

  return (
    <div className="space-y-4">
      <PageHeader
        title="目标"
        description={
          <span className="flex items-center gap-2">
            <span>共 {items.length} 个</span>
            {filterTip}
          </span>
        }
        actions={
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
              <Button
                disabled={hasActiveGoal}
                title={
                  hasActiveGoal
                    ? '当前业务已有进行中的目标，需先完成或取消'
                    : undefined
                }
              >
                <Plus className="mr-1 h-4 w-4" /> 新建目标
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>新建目标</DialogTitle>
                <DialogDescription>
                  用自然语言描述目标；人工智能会自动检索知识库并启动新运行。
                </DialogDescription>
              </DialogHeader>
              <GoalForm
                onCreated={() => setOpen(false)}
                hasActiveGoal={hasActiveGoal}
              />
            </DialogContent>
          </Dialog>
        }
      />

      {error && <ErrorState error={error} onRetry={() => refetch()} />}
      {!isLoading && items.length === 0 && (
        <EmptyState title="无目标" description="创建第一个目标让人工智能开始工作" />
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {isLoading
          ? Array.from({ length: 2 }).map((_, i) => <Skeleton key={i} className="h-32 w-full" />)
          : items.map((g) => (
              <Link key={g.id} to={`/goals/${g.id}`} className="block">
              <Card className="transition-colors hover:border-primary/50 hover:bg-muted/40">
                <CardHeader>
                  <CardTitle className="flex items-center justify-between text-base">
                    <span>{GOAL_TYPE_LABEL[g.type] ?? '未知'}</span>
                    <StatusBadge status={g.status} />
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-2 text-sm">
                  {g.target && typeof g.target === 'object' && ('theme' in g.target || 'audience' in g.target) ? (
                    <div className="space-y-1 rounded bg-muted p-2 text-xs">
                      {(g.target as { theme?: string }).theme && (
                        <div><span className="text-muted-foreground">主题：</span>{(g.target as { theme?: string }).theme}</div>
                      )}
                      {(g.target as { audience?: string }).audience && (
                        <div><span className="text-muted-foreground">人群：</span>{(g.target as { audience?: string }).audience}</div>
                      )}
                      {(g.target as { product_category?: string }).product_category && (
                        <div><span className="text-muted-foreground">类目：</span>{(g.target as { product_category?: string }).product_category}</div>
                      )}
                    </div>
                  ) : (
                    <pre className="overflow-x-auto rounded bg-muted p-2 text-xs">
                      {JSON.stringify(g.target, null, 2)}
                    </pre>
                  )}
                  <div className="flex items-center justify-between text-xs text-muted-foreground">
                    <span>截止：{formatDate(g.deadline)}</span>
                    <span>编号：{g.id.slice(0, 8)}…</span>
                  </div>
                  {g.status === 'active' && (
                    <p className="text-xs text-muted-foreground">最近更新 {formatRelative(g.deadline)}</p>
                  )}
                </CardContent>
              </Card>
              </Link>
            ))}
      </div>
    </div>
  );
}
