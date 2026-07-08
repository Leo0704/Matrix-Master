import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { useLlmCost } from '@/hooks/use-metrics';
import { LoadingSpinner } from '@/components/common/loading-spinner';

export function LlmCostChart() {
  const { data, isLoading } = useLlmCost();
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">LLM 成本（近 14 天）</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="h-64">
          {isLoading || !data ? (
            <LoadingSpinner />
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={data.items ?? []}>
                <defs>
                  <linearGradient id="llm-cost" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#3b82f6" stopOpacity={0.4} />
                    <stop offset="100%" stopColor="#3b82f6" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip formatter={(v: number) => `$${v.toFixed(2)}`} />
                <Area
                  type="monotone"
                  dataKey="cost"
                  stroke="#3b82f6"
                  fill="url(#llm-cost)"
                  name="成本 USD"
                />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
