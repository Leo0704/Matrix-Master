import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/lib/api-client';
import type { Goal, GoalCreate } from '@/types/api';

export function useGoals() {
  return useQuery<{ items: Goal[] }>({
    queryKey: ['goals'],
    queryFn: () => apiClient.get<{ items: Goal[] }>('/goals'),
  });
}

export function useGoal(id: string | undefined) {
  return useQuery<Goal>({
    queryKey: ['goal', id],
    queryFn: () => apiClient.get<Goal>(`/goals/${id}`),
    enabled: !!id,
  });
}

export function useCreateGoal() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: GoalCreate) => apiClient.post<Goal>('/goals', body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['goals'] }),
  });
}
