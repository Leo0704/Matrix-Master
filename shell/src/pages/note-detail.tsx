import { useParams, Link } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import { useNote } from '@/hooks/use-notes';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { StatusBadge } from '@/components/common/status-badge';
import { ErrorState } from '@/components/common/error-state';
import { LoadingBlock } from '@/components/common/loading-spinner';
import { Button } from '@/components/ui/button';
import { formatDate } from '@/lib/format';

export function NoteDetail() {
  const { id } = useParams<{ id: string }>();
  const { data, isLoading, error, refetch } = useNote(id);

  return (
    <div className="space-y-4">
      <Button variant="ghost" size="sm" asChild className="-ml-2">
        <Link to="/notes">
          <ArrowLeft className="mr-1 h-4 w-4" />
          返回内容列表
        </Link>
      </Button>

      {isLoading && <LoadingBlock />}
      {error && <ErrorState error={error} onRetry={() => refetch()} />}
      {data && (
        <>
          <div className="flex items-start justify-between">
            <div>
              <h1 className="text-2xl font-bold tracking-tight">{data.title}</h1>
              <p className="text-sm text-muted-foreground">ID: {data.id}</p>
            </div>
            <StatusBadge status={data.status} />
          </div>

          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            <Card className="md:col-span-2">
              <CardHeader>
                <CardTitle className="text-base">内容</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="whitespace-pre-wrap text-sm">{data.content}</p>
                {data.tags && data.tags.length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-1">
                    {data.tags.map((t) => (
                      <span
                        key={t}
                        className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground"
                      >
                        #{t}
                      </span>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-base">元数据</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                <Row k="账号" v={data.account_id} mono />
                <Row k="排期" v={formatDate(data.scheduled_at)} />
                <Row k="发布" v={formatDate(data.published_at)} />
                <Row k="平台 ID" v={data.platform_note_id ?? '—'} mono />
                {data.platform_url && (
                  <a
                    href={data.platform_url}
                    target="_blank"
                    rel="noreferrer"
                    className="block text-primary hover:underline"
                  >
                    查看发布 →
                  </a>
                )}
              </CardContent>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}

function Row({ k, v, mono = false }: { k: string; v: string | null | undefined; mono?: boolean }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-muted-foreground">{k}</span>
      <span className={mono ? 'font-mono text-xs' : ''}>{v ?? '—'}</span>
    </div>
  );
}
