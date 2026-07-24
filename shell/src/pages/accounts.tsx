import { useState } from 'react';
import { useAccounts } from '@/hooks/use-accounts';
import { AddAccountDialog } from '@/components/accounts/add-account-dialog';
import { PageHeader } from '@/components/common/page-header';
import { ErrorState } from '@/components/common/error-state';
import { EmptyState } from '@/components/common/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { StatusBadge } from '@/components/common/status-badge';
import { formatRelative } from '@/lib/format';
import { Plus, Smartphone, User } from 'lucide-react';
import { Button } from '@/components/ui/button';

export function Accounts() {
  const { data, isLoading, error, refetch } = useAccounts();
  const [filter, setFilter] = useState('');

  const items = data?.items ?? [];
  const filtered = items.filter(
    (a) =>
      !filter ||
      a.handle.toLowerCase().includes(filter.toLowerCase()),
  );

  return (
    <div className="space-y-4">
      <PageHeader
        title="账号"
        description={`共 ${items.length} 个`}
        actions={
          <AddAccountDialog
            trigger={
              <Button>
                <Plus className="mr-1 h-4 w-4" /> 添加账号
              </Button>
            }
          />
        }
      />

      <div className="flex items-center gap-2">
        <input
          type="text"
          placeholder="按小红书号搜索…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="flex h-10 max-w-sm rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
        />
      </div>

      {error && <ErrorState error={error} onRetry={() => refetch()} />}
      {!isLoading && filtered.length === 0 && (
        <EmptyState
          title="暂无账号"
          description="添加账号后，系统才能通过设备发布笔记"
        />
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {isLoading
          ? Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-32 w-full" />
            ))
          : filtered.map((a) => (
              <Card key={a.id} className="transition-shadow hover:shadow-md">
                <CardHeader className="flex flex-row items-start justify-between space-y-0 pb-2">
                  <div className="min-w-0 flex-1">
                    <CardTitle className="text-base">@{a.handle}</CardTitle>
                    <p className="text-xs text-muted-foreground">
                      风险分 {(a.risk_score * 100).toFixed(0)}%
                    </p>
                  </div>
                  <StatusBadge status={a.status} />
                </CardHeader>
                <CardContent className="space-y-2 text-sm">
                  <div className="flex items-center gap-2 text-muted-foreground">
                    <Smartphone className="h-3.5 w-3.5" />
                    <span className="text-xs">
                      设备：{a.device_id ? '已绑定' : '未绑定'}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 text-muted-foreground">
                    <User className="h-3.5 w-3.5" />
                    <span className="text-xs">
                      人设：{a.persona_id ? '已配置' : '未配置'}
                    </span>
                  </div>
                  <div className="border-t pt-2 text-xs text-muted-foreground">
                    最后活跃：{formatRelative(a.last_active)}
                  </div>
                </CardContent>
              </Card>
            ))}
      </div>
    </div>
  );
}
