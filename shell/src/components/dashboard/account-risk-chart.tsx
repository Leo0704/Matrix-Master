import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from 'recharts';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { useQuery } from '@tanstack/react-query';
import { apiClient } from '@/lib/api-client';
import { LoadingSpinner } from '@/components/common/loading-spinner';

const COLORS = ['#22c55e', '#84cc16', '#eab308', '#f97316', '#ef4444'];

export function AccountRiskChart() {
  const { data, isLoading } = useQuery<{
    items: Array<{ range: string; count: number }>;
    total: number;
  }>({
    queryKey: ['analytics-account-risk'],
    queryFn: () => apiClient.get('/analytics/account-risk'),
  });

  const items = data?.items ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">账号风险分布（共 {data?.total ?? 0} 个）</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="h-64">
          {isLoading || !data ? (
            <LoadingSpinner />
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={items}>
                <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                <XAxis dataKey="range" tick={{ fontSize: 12 }} />
                <YAxis tick={{ fontSize: 12 }} allowDecimals={false} />
                <Tooltip />
                <Bar dataKey="count" radius={[4, 4, 0, 0]}>
                  {items.map((_, i) => (
                    <Cell key={i} fill={COLORS[i] ?? '#94a3b8'} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
