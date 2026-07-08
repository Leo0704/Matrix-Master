import { Link } from 'react-router-dom';
import type { Device } from '@/types/api';
import { StatusBadge } from '@/components/common/status-badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Smartphone } from 'lucide-react';
import { formatRelative } from '@/lib/format';

export function DeviceStatusGrid({ devices }: { devices: Device[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Smartphone className="h-4 w-4" />
          设备状态
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {devices.map((d) => (
            <Link
              key={d.id}
              to={`/devices/${d.id}`}
              className="flex items-center justify-between rounded-md border bg-card p-3 transition-colors hover:bg-accent"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-medium">{d.nickname}</p>
                <p className="text-xs text-muted-foreground">
                  {formatRelative(d.last_heartbeat)}
                </p>
              </div>
              <StatusBadge status={d.status} />
            </Link>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
