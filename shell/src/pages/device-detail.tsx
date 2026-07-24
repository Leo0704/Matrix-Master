import { useParams, Link } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import { useDevice } from '@/hooks/use-devices';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { StatusBadge } from '@/components/common/status-badge';
import { ErrorState } from '@/components/common/error-state';
import { LoadingBlock } from '@/components/common/loading-spinner';
import { PageHeader } from '@/components/common/page-header';
import { formatDate, formatRelative } from '@/lib/format';

export function DeviceDetail() {
  const { id } = useParams<{ id: string }>();
  const { data, isLoading, error, refetch } = useDevice(id);

  return (
    <div className="space-y-4">
      <Button variant="ghost" size="sm" asChild className="-ml-2">
        <Link to="/devices">
          <ArrowLeft className="mr-1 h-4 w-4" />
          返回设备列表
        </Link>
      </Button>

      {isLoading && <LoadingBlock />}
      {error && <ErrorState error={error} onRetry={() => refetch()} />}
      {data && (
        <>
          <PageHeader
            title={data.nickname}
            description={`${data.model ?? '—'} · 安卓 ${data.android_version ?? '—'}`}
            actions={<StatusBadge status={data.status} />}
          />

          <Card>
            <CardHeader>
              <CardTitle className="text-base">基本信息</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <Row k="编号" v={data.id} mono />
              <Row k="内网 IP" v={data.tailnet_ip ?? '—'} mono />
              <Row k="客户端版本" v={data.apk_version ?? '—'} />
              <Row k="绑定账号" v={String(data.bound_accounts ?? 0)} />
              <Row k="最后心跳" v={formatRelative(data.last_heartbeat)} />
              <Row k="最后心跳时间" v={formatDate(data.last_heartbeat)} />
              {data.tags && data.tags.length > 0 && (
                <Row k="标签" v={data.tags.join(', ')} />
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
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
