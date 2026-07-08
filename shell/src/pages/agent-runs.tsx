import { Link } from 'react-router-dom';
import { useAgentRuns } from '@/hooks/use-agent-runs';
import { Card, CardHeader, CardTitle } from '@/components/ui/card';
import { StatusBadge } from '@/components/common/status-badge';
import { LoadingBlock } from '@/components/common/loading-spinner';
import { ErrorState } from '@/components/common/error-state';
import { EmptyState } from '@/components/common/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
import { formatRelative } from '@/lib/format';

export function AgentRuns() {
  const { data, isLoading, error, refetch } = useAgentRuns({ limit: 50 });

  const items = data?.items ?? [];

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Agent 运行</h1>
        <p className="text-sm text-muted-foreground">共 {items.length} 条</p>
      </div>

      {isLoading && <LoadingBlock />}
      {error && <ErrorState error={error} onRetry={() => refetch()} />}
      {!isLoading && items.length === 0 && (
        <EmptyState title="无运行记录" description="创建目标后 Agent 会启动 run" />
      )}

      <div className="space-y-2">
        {isLoading
          ? Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-16 w-full" />)
          : items.map((r) => (
              <Link key={r.id} to={`/agent-runs/${r.id}`}>
                <Card className="transition-colors hover:bg-accent/30">
                  <CardHeader className="flex flex-row items-center justify-between p-4">
                    <div>
                      <CardTitle className="text-sm font-mono">{r.id}</CardTitle>
                      <p className="text-xs text-muted-foreground">
                        state: <span className="font-mono">{r.current_state}</span>
                        {' · '}
                        {formatRelative(r.started_at)}
                      </p>
                    </div>
                    <StatusBadge status={r.status} />
                  </CardHeader>
                </Card>
              </Link>
            ))}
      </div>
    </div>
  );
}
