import { useState } from 'react';
import { useDevices } from '@/hooks/use-devices';
import { DeviceCard } from '@/components/devices/device-card';
import { AddDeviceDialog } from '@/components/devices/add-device-dialog';
import { DeviceDetailDrawer } from '@/components/devices/device-detail-drawer';
import { PageHeader } from '@/components/common/page-header';
import { ErrorState } from '@/components/common/error-state';
import { EmptyState } from '@/components/common/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
import { Plus, Eye, EyeOff } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';

export function Devices() {
  const [showRetired, setShowRetired] = useState(false);
  const [filter, setFilter] = useState('');
  const [detailId, setDetailId] = useState<string | null>(null);
  const { data, isLoading, error, refetch } = useDevices(
    showRetired ? { include_disabled: true } : undefined,
  );

  const items = data?.items ?? [];
  const filtered = items.filter(
    (d) =>
      !filter ||
      d.nickname.toLowerCase().includes(filter.toLowerCase()) ||
      (d.model ?? '').toLowerCase().includes(filter.toLowerCase()),
  );

  return (
    <div className="space-y-4">
      <PageHeader
        title="设备"
        description={`共 ${items.length} 台${showRetired ? '（含已退役）' : ''}`}
        actions={
          <AddDeviceDialog
            trigger={
              <Button>
                <Plus className="mr-1 h-4 w-4" /> 添加设备
              </Button>
            }
          />
        }
      />

      <div className="flex items-center gap-2">
        <Input
          placeholder="按昵称 / 型号搜索…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="max-w-sm"
        />
        <Button
          variant="outline"
          size="sm"
          onClick={() => setShowRetired((v) => !v)}
          title={showRetired ? '隐藏已退役设备' : '显示已退役设备'}
        >
          {showRetired ? (
            <>
              <EyeOff className="mr-1 h-4 w-4" /> 隐藏已退役
            </>
          ) : (
            <>
              <Eye className="mr-1 h-4 w-4" /> 显示已退役
            </>
          )}
        </Button>
      </div>

      {error && <ErrorState error={error} onRetry={() => refetch()} />}
      {!isLoading && filtered.length === 0 && (
        <EmptyState
          title={
            filter
              ? '无匹配设备'
              : showRetired
                ? '无已退役设备'
                : '暂无设备'
          }
          description={
            filter
              ? '尝试调整搜索条件'
              : showRetired
                ? '当前业务下没有已退役设备'
                : '点击右上角「添加设备」注册第一台'
          }
        />
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
