import { Link } from 'react-router-dom';
import { TaskThroughputChart } from '@/components/dashboard/task-throughput-chart';
import { AccountRiskChart } from '@/components/dashboard/account-risk-chart';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Table,
  TableHeader,
  TableBody,
  TableHead,
  TableRow,
  TableCell,
} from '@/components/ui/table';
import { StatusBadge } from '@/components/common/status-badge';
import { LoadingBlock } from '@/components/common/loading-spinner';
import { ErrorState } from '@/components/common/error-state';
import { EmptyState } from '@/components/common/empty-state';
import { useNotes } from '@/hooks/use-notes';
import { formatRelative } from '@/lib/format';

export function Data() {
  const { data, isLoading, error, refetch } = useNotes({ limit: 50 });
  const items = data?.items ?? [];

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">数据</h1>
        <p className="text-sm text-muted-foreground">指标看板 / 笔记列表</p>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <TaskThroughputChart />
        <AccountRiskChart />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div>
          <AccountRiskChart />
        </div>
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle className="text-base">最近发布笔记</CardTitle>
          </CardHeader>
          <CardContent>
            {isLoading && <LoadingBlock />}
            {error && <ErrorState error={error} onRetry={() => refetch()} />}
            {!isLoading && items.length === 0 && (
              <EmptyState title="无笔记" description="点「新建笔记」或等 Agent 自动创建" />
            )}
            {items.length > 0 && (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>标题</TableHead>
                    <TableHead>状态</TableHead>
                    <TableHead>标签</TableHead>
                    <TableHead>时间</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((n) => (
                    <TableRow key={n.id}>
                      <TableCell className="max-w-[280px] truncate">
                        <Link to={`/notes/${n.id}`} className="hover:underline">
                          {n.title}
                        </Link>
                      </TableCell>
                      <TableCell>
                        <StatusBadge status={n.status} />
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {n.tags?.join(', ') || '—'}
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {formatRelative(n.published_at || n.scheduled_at || '')}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
