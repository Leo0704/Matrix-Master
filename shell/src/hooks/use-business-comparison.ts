/**
 * use-business-comparison — 多业务对比（v0.7+ dashboard 第 4 期）
 */
import { useQuery } from '@tanstack/react-query';
import { apiClient } from '@/lib/api-client';
import type { BusinessComparisonResponse, BusinessStatus } from '@/types/api';

export function useBusinessComparison(params?: { status?: BusinessStatus }) {
  return useQuery<BusinessComparisonResponse>({
    queryKey: ['business-comparison', params],
    queryFn: () =>
      apiClient.get<BusinessComparisonResponse>('/analytics/business-comparison', { params }),
  });
}