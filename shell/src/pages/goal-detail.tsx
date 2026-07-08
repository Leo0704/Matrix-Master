import { useParams, Link } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import { useGoal } from '@/hooks/use-goals';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { StatusBadge } from '@/components/common/status-badge';
import { ErrorState } from '@/components/common/error-state';
import { LoadingBlock } from '@/components/common/loading-spinner';
import { Button } from '@/components/ui/button';
import { formatDate } from '@/lib/format';

export function GoalDetail() {
  const { id } = useParams<{ id: string }>();
  const { data, isLoading, error, refetch } = useGoal(id);

  return (
    <div className="space-y-4">
      <Button variant="ghost" size="sm" asChild className="-ml-2">
        <Link to="/goals">
          <ArrowLeft className="mr-1 h-4 w-4" />
          返回目标列表
        </Link>
      </Button>

      {isLoading && <LoadingBlock />}
      {error && <ErrorState error={error} onRetry={() => refetch()} />}
      {data && (
        <>
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-2xl font-bold tracking-tight">{data.type}</h1>
              <p className="text-sm text-muted-foreground">ID: {data.id}</p>
            </div>
            <StatusBadge status={data.status} />
          </div>

          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">目标参数</CardTitle>
              </CardHeader>
              <CardContent>
                {data.target && typeof data.target === 'object' && ('theme' in data.target || 'audience' in data.target) ? (
                  <div className="space-y-2 text-sm">
                    {(data.target as { theme?: string }).theme && (
                      <div className="flex items-start gap-2">
                        <span className="w-16 shrink-0 text-muted-foreground">主题</span>
                        <span>{(data.target as { theme?: string }).theme}</span>
                      </div>
                    )}
                    {(data.target as { audience?: string }).audience && (
                      <div className="flex items-start gap-2">
                        <span className="w-16 shrink-0 text-muted-foreground">人群</span>
                        <span>{(data.target as { audience?: string }).audience}</span>
                      </div>
                    )}
                    {(data.target as { product_category?: string }).product_category && (
                      <div className="flex items-start gap-2">
                        <span className="w-16 shrink-0 text-muted-foreground">类目</span>
                        <span>{(data.target as { product_category?: string }).product_category}</span>
                      </div>
                    )}
                    {(data.target as { goal_type?: string }).goal_type && (
                      <div className="flex items-start gap-2">
                        <span className="w-16 shrink-0 text-muted-foreground">类型</span>
                        <span>{(data.target as { goal_type?: string }).goal_type}</span>
                      </div>
                    )}
                  </div>
                ) : (
                  <pre className="overflow-x-auto rounded bg-muted p-3 text-xs">
                    {JSON.stringify(data.target, null, 2)}
                  </pre>
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-base">时间</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">截止</span>
                  <span>{formatDate(data.deadline)}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">状态</span>
                  <StatusBadge status={data.status} />
                </div>
              </CardContent>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
