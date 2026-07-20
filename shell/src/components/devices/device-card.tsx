import { Link } from 'react-router-dom';
import { Wifi, Battery, Activity, Smartphone, Tag } from 'lucide-react';
import type { Device } from '@/types/api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { StatusBadge } from '@/components/common/status-badge';
import { formatRelative } from '@/lib/format';
import { Badge } from '@/components/ui/badge';
import { DeviceRetireButton } from './device-retire-button';

export function DeviceCard({ device }: { device: Device }) {
  return (
    <Card className="group relative transition-shadow hover:shadow-md">
      <CardHeader className="flex flex-row items-start justify-between space-y-0 pb-2">
        <div className="min-w-0 flex-1">
          <CardTitle className="text-base">
            <Link to={`/devices/${device.id}`} className="hover:underline">
              {device.nickname}
            </Link>
          </CardTitle>
          <p className="text-xs text-muted-foreground">{device.model ?? '—'} · Android {device.android_version ?? '—'}</p>
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
        {/* 退役按钮：设备永久下线，清账号绑定 + 撤销密钥 */}
        <div className="flex justify-end pt-1">
          <DeviceRetireButton deviceId={device.id} deviceNickname={device.nickname} />
        </div>
      </CardContent>
    </Card>
  );
}
