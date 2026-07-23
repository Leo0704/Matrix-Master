/**
 * /analytics-comparison — 多业务对比 dashboard（v0.7+ 业务模型重构）
 *
 * 表格 + 进度条对比 2-N 个业务的核心资源计数：
 * devices / accounts / personas / goals / notes / published_notes / kb_documents / agent_runs / successful_runs
 *
 * 后端：GET /api/v1/analytics/business-comparison
 */
import { useState } from 'react';
import { useBusinessComparison } from '@/hooks/use-business-comparison';
import { PageHeader } from '@/components/common/page-header';
import { Skeleton } from '@/components/ui/skeleton';
import { ErrorState } from '@/components/common/error-state';
import { EmptyState } from '@/components/common/empty-state';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import type { BusinessComparisonRow } from '@/types/api';

function statusLabel(status: 'active' | 'archived' | undefined): string {
  if (status === 'active') return '运营中';
  if (status === 'archived') return '已归档';
  return '全部';
}

const METRICS: Array<{
  key: keyof Pick<
    BusinessComparisonRow,
    | 'devices'
    | 'accounts'
    | 'personas'
    | 'goals'
    | 'notes'
    | 'published_notes'
    | 'kb_documents'
    | 'agent_runs'
    | 'successful_runs'
  >;
  label: string;
  fmt?: (n: number) => string;
}> = [
  { key: 'devices', label: '设备' },
  { key: 'accounts', label: '账号' },
  { key: 'personas', label: '人设' },
  { key: 'goals', label: '目标' },
  { key: 'notes', label: '笔记' },
  { key: 'published_notes', label: '已发布' },
  { key: 'kb_documents', label: '知识库文档' },
  { key: 'agent_runs', label: '智能体运行' },
  {
    key: 'successful_runs',
    label: '成功运行',
    fmt: (n) => `${n}`,
  },
];

export function BusinessComparison() {
  const [statusFilter, setStatusFilter] = useState<'active' | 'archived' | undefined>('active');
  const { data, isLoading, error, refetch } = useBusinessComparison(
    statusFilter ? { status: statusFilter } : undefined,
  );

  const items = data?.items ?? [];

  // 计算每列最大值（用于进度条）
  const maxByMetric: Record<string, number> = {};
  for (const m of METRICS) {
    maxByMetric[m.key] = Math.max(1, ...items.map((it) => it[m.key] as number));
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="多业务对比"
        description={`共 ${data?.total_businesses ?? 0} 个业务（${statusLabel(statusFilter)}）`}
        actions={
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant={statusFilter === undefined ? 'default' : 'outline'}
              onClick={() => setStatusFilter(undefined)}
            >
              全部
            </Button>
            <Button
              size="sm"
              variant={statusFilter === 'active' ? 'default' : 'outline'}
              onClick={() => setStatusFilter('active')}
            >
              仅运营中
            </Button>
            <Button
              size="sm"
              variant={statusFilter === 'archived' ? 'default' : 'outline'}
              onClick={() => setStatusFilter('archived')}
            >
              仅已归档
            </Button>
          </div>
        }
      />

      {error && <ErrorState error={error} onRetry={refetch} />}
      {isLoading ? (
        <Skeleton className="h-64" />
      ) : items.length === 0 ? (
        <EmptyState
          title="还没有业务"
          description="先去「业务管理」页建一个业务再来看对比。"
        />
      ) : (
        <>
          {/* 横向表格：行 = 业务，列 = 9 个指标 */}
          <div className="rounded-md border overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b bg-muted/40">
                <tr className="text-left">
                  <th className="sticky left-0 z-10 bg-muted/40 px-3 py-2 font-medium min-w-[160px]">
                    业务
                  </th>
                  {METRICS.map((m) => (
                    <th key={m.key} className="px-3 py-2 font-medium whitespace-nowrap">
                      {m.label}
                    </th>
                  ))}
                  <th className="px-3 py-2 font-medium whitespace-nowrap">笔记/账号</th>
                </tr>
              </thead>
              <tbody>
                {items.map((row) => (
                  <tr key={row.business_id} className="border-b last:border-b-0">
                    <td className="sticky left-0 z-10 bg-card px-3 py-2 font-medium">
                      <div className="flex items-center gap-2">
                        <span className="truncate max-w-[160px]">{row.business_name}</span>
                        {row.status === 'archived' && (
                          <span className="rounded bg-gray-100 px-1.5 py-0.5 text-xs text-gray-600">
                            已归档
                          </span>
                        )}
                      </div>
                      <div className="text-xs text-muted-foreground truncate max-w-[160px]">
                        标识：{row.business_slug}
                      </div>
                    </td>
                    {METRICS.map((m) => {
                      const value = row[m.key] as number;
                      const max = maxByMetric[m.key] ?? 1;
                      const pct = (value / max) * 100;
                      return (
                        <td key={m.key} className="px-3 py-2 align-middle">
                          <div className="flex items-center gap-2">
                            <span className="font-mono text-xs tabular-nums whitespace-nowrap min-w-[3ch] text-right">
                              {value}
                            </span>
                            <div className="h-2 flex-1 min-w-[40px] rounded bg-muted overflow-hidden">
                              <div
                                className={cn(
                                  'h-full rounded',
                                  pct > 80
                                    ? 'bg-primary'
                                    : pct > 40
                                      ? 'bg-primary/60'
                                      : 'bg-primary/30',
                                )}
                                style={{ width: `${Math.max(2, pct)}%` }}
                                title={`${row.business_name} · ${m.label}: ${value}`}
                              />
                            </div>
                          </div>
                        </td>
                      );
                    })}
                    <td className="px-3 py-2 align-middle font-mono text-xs tabular-nums">
                      {row.notes_per_account.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p className="text-xs text-muted-foreground">
            进度条按列归一化（每列最大值 = 100%）。点击行表头可看绝对值。
          </p>
        </>
      )}
    </div>
  );
}