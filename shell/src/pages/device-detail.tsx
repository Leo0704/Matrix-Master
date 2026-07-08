import { useParams, Link } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import { useDevice } from '@/hooks/use-devices';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { StatusBadge } from '@/components/common/status-badge';
import { ErrorState } from '@/components/common/error-state';
import { LoadingBlock } from '@/components/common/loading-spinner';
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
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-2xl font-bold tracking-tight">{data.nickname}</h1>
              <p className="text-sm text-muted-foreground">
                {data.model} · Android {data.android_version}
              </p>
            </div>
            <StatusBadge status={data.status} />
          </div>

          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">基本信息</CardTitle>
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
            <Card>
              <CardHeader>
                <CardTitle className="text-base">操作</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                <p className="text-muted-foreground">设备级操作（重启 APK / 截图 / 远程登录）</p>
                <div className="flex flex-wrap gap-2">
                  <Button variant="outline" size="sm">重启 APK</Button>
                  <Button variant="outline" size="sm">截屏</Button>
                  <Button variant="outline" size="sm">查看日志</Button>
                </div>
              </CardContent>
            </Card>
          </div>
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
