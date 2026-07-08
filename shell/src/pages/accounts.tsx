import { useState } from 'react';
import { useAccounts } from '@/hooks/use-accounts';
import { AccountCard } from '@/components/accounts/account-card';
import { LoadingBlock } from '@/components/common/loading-spinner';
import { ErrorState } from '@/components/common/error-state';
import { EmptyState } from '@/components/common/empty-state';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';

export function Accounts() {
  const { data, isLoading, error, refetch } = useAccounts();
  const [filter, setFilter] = useState('');

  const items = data?.items ?? [];
  const filtered = items.filter(
    (a) => !filter || a.handle.toLowerCase().includes(filter.toLowerCase()),
  );

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">账号</h1>
        <p className="text-sm text-muted-foreground">共 {items.length} 个</p>
      </div>

      <Input
        placeholder="按 handle 搜索…"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        className="max-w-sm"
      />

      {isLoading && <LoadingBlock />}
      {error && <ErrorState error={error} onRetry={() => refetch()} />}
      {!isLoading && filtered.length === 0 && (
        <EmptyState title="无账号" description="还没有绑定任何小红书账号" />
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {isLoading
          ? Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-40 w-full" />)
          : filtered.map((a) => <AccountCard key={a.id} account={a} />)}
      </div>
    </div>
  );
}
