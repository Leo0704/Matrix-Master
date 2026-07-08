import { useState } from 'react';
import { useDevices } from '@/hooks/use-devices';
import { DeviceCard } from '@/components/devices/device-card';
import { AddDeviceDialog } from '@/components/devices/add-device-dialog';
import { DeviceDetailDrawer } from '@/components/devices/device-detail-drawer';
import { LoadingBlock } from '@/components/common/loading-spinner';
import { ErrorState } from '@/components/common/error-state';
import { EmptyState } from '@/components/common/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
import { Plus } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';

export function Devices() {
  const { data, isLoading, error, refetch } = useDevices();
  const [filter, setFilter] = useState('');
  const [detailId, setDetailId] = useState<string | null>(null);

  const items = data?.items ?? [];
  const filtered = items.filter(
    (d) =>
      !filter ||
      d.nickname.toLowerCase().includes(filter.toLowerCase()) ||
      d.model.toLowerCase().includes(filter.toLowerCase()),
  );

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">设备</h1>
          <p className="text-sm text-muted-foreground">共 {items.length} 台</p>
        </div>
        <AddDeviceDialog
          trigger={
            <Button>
              <Plus className="mr-1 h-4 w-4" /> 添加设备
            </Button>
          }
        />
      </div>

      <div className="flex items-center gap-2">
        <Input
          placeholder="按昵称 / 型号搜索…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="max-w-sm"
        />
      </div>

      {isLoading && <LoadingBlock />}
      {error && <ErrorState error={error} onRetry={() => refetch()} />}
      {!isLoading && filtered.length === 0 && (
        <EmptyState title="无匹配设备" description="尝试调整搜索条件或添加新设备" />
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {isLoading
          ? Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-44 w-full" />)
          : filtered.map((d) => (
              <div key={d.id} onClick={() => setDetailId(d.id)} className="cursor-pointer">
                <DeviceCard device={d} />
              </div>
            ))}
      </div>

      <DeviceDetailDrawer
        id={detailId}
        open={!!detailId}
        onOpenChange={(v) => !v && setDetailId(null)}
      />
    </div>
  );
}
