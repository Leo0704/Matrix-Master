import { Link } from 'react-router-dom';
import { AlertCircle, AlertTriangle, Info } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { useAlerts } from '@/hooks/use-alerts';
import { LoadingSpinner } from '@/components/common/loading-spinner';
import { formatRelative } from '@/lib/format';
import { cn } from '@/lib/utils';

export function AlertsFeed() {
  const { data, isLoading } = useAlerts();
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">告警</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading && <LoadingSpinner />}
        {data && (data.items?.length ?? 0) === 0 && (
          <p className="py-4 text-center text-sm text-muted-foreground">无告警</p>
        )}
        {data && (
          <ul className="space-y-2">
            {(data.items ?? []).slice(0, 5).map((a) => {
              const Icon =
                a.severity === 'critical' ? AlertCircle : a.severity === 'warning' ? AlertTriangle : Info;
              return (
                <li
                  key={a.id}
                  className={cn(
                    'flex items-start gap-2 rounded-md border p-2 text-sm',
                    a.severity === 'critical' && 'border-destructive/40 bg-destructive/5',
                    a.severity === 'warning' && 'border-warning/40 bg-warning/5',
                    a.resolved && 'opacity-50',
                  )}
                >
                  <Icon
                    className={cn(
                      'mt-0.5 h-4 w-4 shrink-0',
                      a.severity === 'critical' && 'text-destructive',
                      a.severity === 'warning' && 'text-warning',
                      a.severity === 'info' && 'text-muted-foreground',
                    )}
                  />
                  <div className="min-w-0 flex-1">
                    <p className="truncate">{a.message}</p>
                    <p className="text-xs text-muted-foreground">
                      {a.code} · {formatRelative(a.created_at)}
                    </p>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
        <div className="mt-3 text-right">
          <Link to="/alerts" className="text-xs text-primary hover:underline">
            查看全部 →
          </Link>
        </div>
      </CardContent>
    </Card>
  );
}
