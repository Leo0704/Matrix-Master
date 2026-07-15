import { useQuery } from '@tanstack/react-query';
import { Users } from 'lucide-react';
import { apiClient } from '@/lib/api-client';
import { Card, CardContent } from '@/components/ui/card';
import { LoadingSpinner } from '@/components/common/loading-spinner';
import { PageHeader } from '@/components/common/page-header';
import type { AccountContentStats } from '@/types/api';

/**
 * 数据页：每个账号的内容表现（运营看板）。
 * 与"内容"页（notes 列表）不重复——这里只展示每个账号的聚合指标。
 */
export function Data() {
  const { data, isLoading, error } = useQuery<{ items: AccountContentStats[] }>({
    queryKey: ['account-content-stats'],
    queryFn: () =>
      apiClient.get<{ items: AccountContentStats[] }>(
        '/analytics/account-content-stats',
      ),
    refetchInterval: 15_000,
  });

  const items = data?.items ?? [];
  const accountCount = items.filter((i) => i.account_id != null).length;
  const poolCount = items.length - accountCount;

  return (
    <div className="space-y-4">
      <PageHeader
        title="数据"
        description={`账号 ${accountCount} 个 · 草稿池 ${poolCount} 个（草稿阶段先落库没绑账号的那批）`}
      />

      {isLoading ? (
        <LoadingSpinner />
      ) : error ? (
        <p className="text-sm text-destructive">加载失败：{String(error)}</p>
      ) : items.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-12 text-center">
            <Users className="mb-3 h-10 w-10 text-muted-foreground/40" />
            <p className="text-sm text-muted-foreground">还没有账号数据</p>
            <p className="mt-1 text-xs text-muted-foreground/70">
              注册账号 + 发布笔记后，这里会显示每个账号的内容表现
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {items.map((item) => (
            <AccountCard key={item.account_id ?? 'unassigned'} item={item} />
          ))}
        </div>
      )}
    </div>
  );
}

function AccountCard({ item }: { item: AccountContentStats }) {
  const isUnassigned = item.account_id == null;
  return (
    <Card className={isUnassigned ? 'border-dashed bg-muted/30' : ''}>
      <CardContent className="p-4">
        <div className="mb-3">
          <p className="truncate font-medium" title={item.handle}>
            {item.handle}
          </p>
          <p className="text-xs text-muted-foreground">
            {isUnassigned
              ? '未绑账号草稿池'
              : item.device_nickname
                ? `📱 ${item.device_nickname} · ${item.status}`
                : item.status}
          </p>
        </div>

        <div className="mb-3 grid grid-cols-4 gap-1 text-center">
          <Stat label="总" value={item.total_notes} />
          <Stat label="已发" value={item.published} accent="text-success" />
          <Stat label="草稿" value={item.draft} />
          <Stat label="排期" value={item.scheduled} />
        </div>

        {item.published > 0 && (
          <div className="border-t pt-2 text-xs text-muted-foreground">
            <div className="flex justify-between">
              <span>平均曝光</span>
              <span className="font-mono">{Math.round(item.avg_views)}</span>
            </div>
            <div className="flex justify-between">
              <span>平均点赞</span>
              <span className="font-mono">{Math.round(item.avg_likes)}</span>
            </div>
            <div className="flex justify-between">
              <span>平均评论</span>
              <span className="font-mono">{Math.round(item.avg_comments)}</span>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function Stat({
  label,
  value,
  accent,
}: {
  label: string;
  value: number;
  accent?: string;
}) {
  return (
    <div className="flex flex-col">
      <span className={`text-lg font-bold ${accent ?? ''}`}>{value}</span>
      <span className="text-[10px] text-muted-foreground">{label}</span>
    </div>
  );
}