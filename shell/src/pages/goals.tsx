import { useState } from 'react';
import { Plus } from 'lucide-react';
import { useGoals } from '@/hooks/use-goals';
import { GoalForm } from '@/components/goals/goal-form';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { StatusBadge } from '@/components/common/status-badge';
import { LoadingBlock } from '@/components/common/loading-spinner';
import { ErrorState } from '@/components/common/error-state';
import { EmptyState } from '@/components/common/empty-state';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { formatDate, formatRelative } from '@/lib/format';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';

export function Goals() {
  const { data, isLoading, error, refetch } = useGoals();
  const [open, setOpen] = useState(false);

  const items = data?.items ?? [];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">目标</h1>
          <p className="text-sm text-muted-foreground">共 {items.length} 个</p>
        </div>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild>
            <Button>
              <Plus className="mr-1 h-4 w-4" /> 新建目标
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>新建目标</DialogTitle>
              <DialogDescription>
                用自然语言描述目标；AI 会自动检索知识库并启动新 run。
              </DialogDescription>
            </DialogHeader>
            <GoalForm onCreated={() => setOpen(false)} />
          </DialogContent>
        </Dialog>
      </div>

      {isLoading && <LoadingBlock />}
      {error && <ErrorState error={error} onRetry={() => refetch()} />}
      {!isLoading && items.length === 0 && (
        <EmptyState title="无目标" description="创建第一个目标让 AI 开始工作" />
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {isLoading
          ? Array.from({ length: 2 }).map((_, i) => <Skeleton key={i} className="h-32 w-full" />)
          : items.map((g) => (
              <Card key={g.id}>
                <CardHeader>
                  <CardTitle className="flex items-center justify-between text-base">
                    <span>{g.type}</span>
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
                    <span>id: {g.id.slice(0, 8)}…</span>
                  </div>
                  {g.status === 'active' && (
                    <p className="text-xs text-muted-foreground">最近更新 {formatRelative(g.deadline)}</p>
                  )}
                </CardContent>
              </Card>
            ))}
      </div>
    </div>
  );
}
