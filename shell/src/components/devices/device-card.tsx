import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Wifi, Battery, Activity, Smartphone, Tag, Unlink, Loader2 } from 'lucide-react';
import type { Device } from '@/types/api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { StatusBadge } from '@/components/common/status-badge';
import { formatRelative } from '@/lib/format';
import { Badge } from '@/components/ui/badge';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useToast } from '@/components/ui/use-toast';
import { useUnbindDevice } from '@/hooks/use-devices';

export function DeviceCard({ device }: { device: Device }) {
  const unbind = useUnbindDevice();
  const { toast } = useToast();
  const [confirmOpen, setConfirmOpen] = useState(false);

  async function handleUnbind() {
    try {
      const res = await unbind.mutateAsync(device.id);
      toast({
        title: '设备已解绑',
        description: res.unbound_account_handle
          ? `账号 ${res.unbound_account_handle} 已脱离该设备`
          : '设备未绑定账号',
      });
      setConfirmOpen(false);
    } catch (err) {
      toast({
        title: '解绑失败',
        description: String(err),
        variant: 'destructive',
      });
    }
  }

  return (
    <>
      <Card className="group relative transition-shadow hover:shadow-md">
        <CardHeader className="flex flex-row items-start justify-between space-y-0 pb-2">
          <div className="min-w-0 flex-1">
            <CardTitle className="text-base">
              <Link to={`/devices/${device.id}`} className="hover:underline">
                {device.nickname}
              </Link>
            </CardTitle>
            <p className="text-xs text-muted-foreground">{device.model} · Android {device.android_version}</p>
          </div>
          <StatusBadge status={device.status} />
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          <div className="flex items-center gap-2 text-muted-foreground">
            <Wifi className="h-3.5 w-3.5" />
            <span className="font-mono text-xs">{device.tailnet_ip ?? '—'}</span>
          </div>
          <div className="flex items-center gap-2 text-muted-foreground">
            <Activity className="h-3.5 w-3.5" />
            <span className="text-xs">心跳 {formatRelative(device.last_heartbeat)}</span>
          </div>
          {device.tags && device.tags.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {device.tags.map((t) => (
                <Badge key={t} variant="muted" className="text-xs">
                  <Tag className="mr-1 h-3 w-3" />
                  {t}
                </Badge>
              ))}
            </div>
          )}
          <div className="flex items-center justify-between border-t pt-2 text-xs text-muted-foreground">
            <span className="flex items-center gap-1">
              <Smartphone className="h-3 w-3" />
              {device.bound_account_handle ? (
                <span title={`账号数：${device.bound_accounts ?? 0}`}>
                  👤 {device.bound_account_handle}
                </span>
              ) : (
                <span>未绑账号</span>
              )}
            </span>
            <span className="flex items-center gap-1">
              <Battery className="h-3 w-3" />
              APK v{device.apk_version ?? '—'}
            </span>
          </div>
          {/* 解绑按钮：始终显示在卡片最底部一行（不与 StatusBadge 抢空间） */}
          <div className="flex justify-end pt-1">
            <Button
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-xs text-muted-foreground hover:text-destructive"
              onClick={() => setConfirmOpen(true)}
              disabled={!device.bound_account_handle}
              title={
                device.bound_account_handle
                  ? '解绑此设备上的账号（设备坏了换新机场景）'
                  : '设备未绑账号，无需解绑'
              }
            >
              <Unlink className="mr-1 h-3 w-3" />
              解绑
            </Button>
          </div>
        </CardContent>
      </Card>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>解绑设备「{device.nickname}」？</DialogTitle>
            <DialogDescription>
              将清除该设备绑定的账号（device_id 置空）。账号的笔记数据不丢失，
              之后再把账号绑到新设备即可。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)} disabled={unbind.isPending}>
              取消
            </Button>
            <Button variant="destructive" onClick={handleUnbind} disabled={unbind.isPending}>
              {unbind.isPending && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
              确认解绑
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}