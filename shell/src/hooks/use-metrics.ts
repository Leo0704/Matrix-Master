import { useQuery } from '@tanstack/react-query';
import { apiClient } from '@/lib/api-client';
import type { MetricsSummary } from '@/types/api';

export function useMetricsSummary() {
  return useQuery<MetricsSummary>({
    queryKey: ['metrics-summary'],
    queryFn: () => apiClient.get<MetricsSummary>('/metrics/summary'),
    refetchInterval: 15_000,
  });
}

export function useTaskThroughput(days: number = 14) {
  return useQuery<{ items: Array<{ date: string; success: number; failed: number }>; days: number }>({
    queryKey: ['task-throughput', days],
    queryFn: () => apiClient.get('/analytics/task-throughput', { params: { days } }),
  });
}
