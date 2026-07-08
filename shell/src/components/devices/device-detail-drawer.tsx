import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useDevice } from '@/hooks/use-devices';
import { LoadingSpinner } from '@/components/common/loading-spinner';
import { ErrorState } from '@/components/common/error-state';
import { StatusBadge } from '@/components/common/status-badge';
import { formatDate, formatRelative } from '@/lib/format';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

export function DeviceDetailDrawer({
  id,
  open,
  onOpenChange,
}: {
  id: string | null;
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const { data, isLoading, error, refetch } = useDevice(id ?? undefined);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>设备详情</DialogTitle>
          <DialogDescription>设备 ID: {id}</DialogDescription>
        </DialogHeader>
        <div className="mt-2 max-h-[70vh] overflow-y-auto">
          {isLoading && <LoadingSpinner />}
          {error && <ErrorState error={error} onRetry={() => refetch()} />}
          {data && (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="text-lg font-semibold">{data.nickname}</h3>
                <StatusBadge status={data.status} />
              </div>
              <p className="text-sm text-muted-foreground">
                {data.model} · Android {data.android_version}
              </p>
              <Card>
                <CardHeader>
                  <CardTitle className="text-sm">基本信息</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2 text-sm">
                  <Row k="ID" v={data.id} mono />
                  <Row k="Tailnet IP" v={data.tailnet_ip ?? '—'} mono />
                  <Row k="APK 版本" v={data.apk_version ?? '—'} />
                  <Row k="绑定账号" v={String(data.bound_accounts ?? 0)} />
                  <Row k="最后心跳" v={formatRelative(data.last_heartbeat)} />
                  <Row k="注册时间" v={formatDate(data.last_heartbeat)} />
                  {data.tags && data.tags.length > 0 && (
                    <Row k="标签" v={data.tags.join(', ')} />
                  )}
                </CardContent>
              </Card>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function Row({ k, v, mono = false }: { k: string; v: string; mono?: boolean }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-muted-foreground">{k}</span>
      <span className={mono ? 'font-mono text-xs' : ''}>{v}</span>
    </div>
  );
}
