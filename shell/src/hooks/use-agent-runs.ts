import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/lib/api-client';
import type { AgentRun } from '@/types/api';

export function useAgentRuns(params?: {
  status?: string;
  limit?: number;
  /** v0.7+ 业务过滤 */
  business_id?: string;
}) {
  return useQuery<{ items: AgentRun[] }>({
    queryKey: ['agent-runs', params],
    queryFn: () => apiClient.get<{ items: AgentRun[] }>('/agent/runs', { params }),
    refetchInterval: 5_000,
  });
}

export function useAgentRun(id: string | undefined) {
  return useQuery<AgentRun>({
    queryKey: ['agent-run', id],
    queryFn: () => apiClient.get<AgentRun>(`/agent/runs/${id}`),
    enabled: !!id,
    refetchInterval: 3_000,
  });
}

export function useCancelAgentRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.post<{ ok: boolean }>(`/agent/runs/${id}/cancel`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['agent-runs'] }),
  });
}
