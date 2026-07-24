import { useParams, Link, useNavigate } from 'react-router-dom';
import { ArrowLeft, StopCircle } from 'lucide-react';
import { useAgentRun, useCancelAgentRun } from '@/hooks/use-agent-runs';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { StatusBadge } from '@/components/common/status-badge';
import { ErrorState } from '@/components/common/error-state';
import { LoadingBlock } from '@/components/common/loading-spinner';
import { PageHeader } from '@/components/common/page-header';
import { Button } from '@/components/ui/button';
import { formatDate, formatRelative } from '@/lib/format';
import { formatState } from '@/types/api';
import { toast } from '@/components/ui/use-toast';

const STATE_ORDER = [
  'IDLE',
  'RESEARCH',
  'DRAFT',
  'REVIEW',
  'SCHEDULE',
  'DISPATCH',
  'PUBLISH',
  'COLLECT',
  'ANALYZE',
];

export function AgentRunDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { data, isLoading, error, refetch } = useAgentRun(id);
  const cancel = useCancelAgentRun();

  return (
    <div className="space-y-4">
      {/* 没有独立的运行列表页（/agent-runs 路由不存在），返回上一页 */}
      <Button variant="ghost" size="sm" className="-ml-2" onClick={() => navigate(-1)}>
        <ArrowLeft className="mr-1 h-4 w-4" />
        返回
      </Button>

      {isLoading && <LoadingBlock />}
      {error && <ErrorState error={error} onRetry={() => refetch()} />}
      {data && (
        <>
          <PageHeader
            title={data.id}
            titleClassName="font-mono"
            description={
              <>
                目标：<Link to={`/goals/${data.goal_id ?? ''}`} className="text-primary hover:underline">{data.goal_id ?? '—'}</Link>
              </>
            }
            actions={
              <div className="flex items-center gap-2">
                <StatusBadge status={data.status} />
                {data.status === 'running' && (
                  <Button
                    variant="destructive"
                    size="sm"
                    onClick={() => {
                      cancel.mutate(data.id, {
                        onSuccess: () => toast({ title: '已发送取消请求' }),
                      });
                    }}
                  >
                    <StopCircle className="mr-1 h-4 w-4" />
                    取消
                  </Button>
                )}
              </div>
            }
          />

          <Card>
            <CardHeader>
              <CardTitle className="text-base">状态机</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex flex-wrap gap-1 text-xs">
                {STATE_ORDER.map((s) => (
                  <span
                    key={s}
                    className={
                      data.current_state === s
                        ? 'rounded bg-primary px-2 py-1 text-primary-foreground'
                        : 'rounded bg-muted px-2 py-1 text-muted-foreground'
                    }
                  >
                    {formatState(s)}
                  </span>
                ))}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">时间线</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">开始</span>
                <span>{formatDate(data.started_at)}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">最近更新</span>
                <span>{formatRelative(data.updated_at)}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">结束</span>
                <span>{formatDate(data.ended_at)}</span>
              </div>
            </CardContent>
          </Card>

          {data.last_error_snapshot && (
            <Card className="border-destructive/40">
              <CardHeader>
                <CardTitle className="text-base text-destructive">错误信息</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">错误码</span>
                  <span className="font-mono">
                    {String(data.last_error_snapshot.code ?? '未知错误')}
                  </span>
                </div>
                <div>
                  <span className="text-muted-foreground">描述</span>
                  <p className="mt-1 whitespace-pre-wrap">
                    {String(data.last_error_snapshot.message ?? '无详细描述')}
                  </p>
                </div>
              </CardContent>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
