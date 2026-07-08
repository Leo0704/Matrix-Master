import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { useTaskThroughput } from '@/hooks/use-metrics';
import { LoadingSpinner } from '@/components/common/loading-spinner';

export function TaskThroughputChart() {
  const { data, isLoading } = useTaskThroughput();
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">任务吞吐（近 14 天）</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="h-64">
          {isLoading || !data ? (
            <LoadingSpinner />
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={data.items ?? []}>
                <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip />
                <Legend />
                <Line type="monotone" dataKey="success" stroke="#22c55e" name="成功" />
                <Line type="monotone" dataKey="failed" stroke="#ef4444" name="失败" />
              </LineChart>
            </ResponsiveContainer>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
