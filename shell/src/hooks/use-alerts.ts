import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/lib/api-client';
import type { AlertItem } from '@/types/api';

export function useAlerts(params?: {
  resolved?: boolean;
  severity?: string;
  code?: string;
  /** v0.7+ 业务过滤（018 migration 加列后生效） */
  business_id?: string;
}) {
  return useQuery<{ items: AlertItem[]; total: number }>({
    queryKey: ['alerts', params],
    queryFn: () => apiClient.get('/alerts', { params }),
    refetchInterval: 8_000,
  });
}

export function useScanAlerts() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiClient.post<{ items: AlertItem[]; total: number }>('/alerts/scan'),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['alerts'] }),
  });
}

export function useResolveAlert() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, resolver, comment }: { id: string; resolver: string; comment?: string }) =>
      apiClient.post<{ id: string; resolved: boolean }>(`/alerts/${id}/resolve`, {
        resolver,
        comment,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['alerts'] }),
  });
}

export function useDeleteAlert() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.delete<{ deleted: number }>(`/alerts/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['alerts'] }),
  });
}

export function useClearResolvedAlerts() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiClient.post<{ deleted: number }>('/alerts/clear-resolved'),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['alerts'] }),
  });
}
