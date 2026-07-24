import { useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useDevice, useIssuePairCode } from '@/hooks/use-devices';
import { useAccounts } from '@/hooks/use-accounts';
import { LoadingSpinner } from '@/components/common/loading-spinner';
import { ErrorState } from '@/components/common/error-state';
import { StatusBadge } from '@/components/common/status-badge';
import { formatDate, formatRelative, humanizeStatus } from '@/lib/format';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { toast } from '@/components/ui/use-toast';
import { Copy, KeyRound } from 'lucide-react';
import { DeviceRetireButton } from './device-retire-button';

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
  const accountsQ = useAccounts(id ? { device_id: id } : undefined);
  const account = accountsQ.data?.items?.[0];
  const issuePairMut = useIssuePairCode();
  const [pairCode, setPairCode] = useState<string | null>(null);

  async function handleIssuePair() {
    if (!id) return;
    try {
      const res = await issuePairMut.mutateAsync(id);
      if (!res.pair_code) throw new Error('主控没有返回配对码');
      setPairCode(res.pair_code);
      toast({ title: '已生成新配对码', description: '10 分钟内有效' });
    } catch (e) {
      toast({
        title: '获取配对码失败',
        description: (e as Error).message,
        variant: 'destructive',
      });
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>设备详情</DialogTitle>
          <DialogDescription>设备编号：{id}</DialogDescription>
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
                {data.model ?? '—'} · 安卓 {data.android_version ?? '—'}
              </p>
              <Card>
                <CardHeader>
                  <CardTitle className="text-sm">基本信息</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2 text-sm">
                  <Row k="编号" v={data.id} mono />
                  <Row k="内网 IP" v={data.tailnet_ip ?? '—'} mono />
                  <Row k="客户端版本" v={data.apk_version ?? '—'} />
                  <Row k="最后心跳" v={formatRelative(data.last_heartbeat)} />
                  <Row k="业务" v={data.business_name ?? '—'} />
                  <Row k="最后心跳时间" v={formatDate(data.last_heartbeat)} />
                  {data.tags && data.tags.length > 0 && (
                    <Row k="标签" v={data.tags.join(', ')} />
                  )}
                </CardContent>
              </Card>
              {account && (
                <Card>
                  <CardHeader>
                    <CardTitle className="text-sm">绑定账号</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-2 text-sm">
                    <Row k="账号名" v={`@${account.handle}`} />
                    <Row
                      k="状态"
                      v={humanizeStatus(account.status)}
                    />
                    <Row k="最后活跃" v={formatRelative(account.last_active)} />
                  </CardContent>
                </Card>
              )}
              <Card>
                <CardHeader>
                  <CardTitle className="text-sm">配对码</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2 text-sm">
                  <p className="text-muted-foreground">
                    配对码 10 分钟过期。过期或丢失时点下方按钮重新获取。
                  </p>
                  {pairCode && (
                    <div className="flex items-center justify-center gap-2 rounded-md border bg-muted px-4 py-2 font-mono text-2xl tracking-widest">
                      {pairCode}
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => {
                          navigator.clipboard.writeText(pairCode);
                          toast({ title: '已复制配对码' });
                        }}
                      >
                        <Copy className="h-4 w-4" />
                      </Button>
                    </div>
                  )}
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={handleIssuePair}
                    disabled={issuePairMut.isPending}
                  >
                    <KeyRound className="mr-1 h-4 w-4" />
                    {issuePairMut.isPending ? '生成中…' : '重新获取配对码'}
                  </Button>
                </CardContent>
              </Card>
              <Card>
                <CardHeader>
                  <CardTitle className="text-sm">危险操作</CardTitle>
                </CardHeader>
                <CardContent>
                  <DeviceRetireButton
                    deviceId={data.id}
                    deviceNickname={data.nickname}
                    variant="outline"
                    size="default"
                  />
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
