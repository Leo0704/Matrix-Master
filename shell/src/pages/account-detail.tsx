import { useParams, Link } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import { useAccount } from '@/hooks/use-accounts';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { StatusBadge } from '@/components/common/status-badge';
import { RiskIndicator } from '@/components/accounts/risk-indicator';
import { ErrorState } from '@/components/common/error-state';
import { LoadingBlock } from '@/components/common/loading-spinner';
import { Button } from '@/components/ui/button';
import { formatDate, formatRelative } from '@/lib/format';
import { ConfirmDialog } from '@/components/common/confirm-dialog';
import { toast } from '@/components/ui/use-toast';

export function AccountDetail() {
  const { id } = useParams<{ id: string }>();
  const { data, isLoading, error, refetch } = useAccount(id);

  return (
    <div className="space-y-4">
      <Button variant="ghost" size="sm" asChild className="-ml-2">
        <Link to="/accounts">
          <ArrowLeft className="mr-1 h-4 w-4" />
          返回账号列表
        </Link>
      </Button>

      {isLoading && <LoadingBlock />}
      {error && <ErrorState error={error} onRetry={() => refetch()} />}
      {data && (
        <>
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-2xl font-bold tracking-tight">@{data.handle}</h1>
              <p className="text-sm text-muted-foreground">ID: {data.id}</p>
            </div>
            <StatusBadge status={data.status} />
          </div>

          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">基本信息</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">风险评分</span>
                  <RiskIndicator score={data.risk_score} />
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">最后活跃</span>
                  <span>{formatRelative(data.last_active)}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">人设</span>
                  <span className="font-mono text-xs">{data.persona_id?.slice(0, 8) ?? '—'}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">绑定设备</span>
                  <span className="font-mono text-xs">{data.device_id?.slice(0, 8) ?? '—'}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">更新时间</span>
                  <span>{formatDate(data.last_active)}</span>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-base">操作</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                <p className="text-sm text-muted-foreground">账号级操作</p>
                <div className="flex flex-wrap gap-2">
                  <Button variant="outline" size="sm">查看笔记</Button>
                  <ConfirmDialog
                    trigger={<Button variant="outline" size="sm">暂停</Button>}
                    title="暂停账号？"
                    description="暂停后该账号不再接受新任务，已有任务等待完成。"
                    confirmText="暂停"
                    onConfirm={() => {
                      toast({ title: '账号已暂停' });
                    }}
                  />
                  <ConfirmDialog
                    trigger={<Button variant="destructive" size="sm">永久禁用</Button>}
                    title="永久禁用账号？"
                    description="此操作不可逆。"
                    confirmText="禁用"
                    destructive
                    onConfirm={() => {
                      toast({ title: '账号已禁用', variant: 'destructive' });
                    }}
                  />
                </div>
              </CardContent>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
