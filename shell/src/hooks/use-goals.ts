import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/lib/api-client';
import type { Goal, GoalCreate, GoalRound, GoalStatus, GoalType } from '@/types/api';

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

export function useGoalRounds(id: string | undefined) {
  return useQuery<{ items: GoalRound[]; total: number }>({
    queryKey: ['goal-rounds', id],
    queryFn: () =>
      apiClient.get<{ items: GoalRound[]; total: number }>(`/goals/${id}/rounds`),
    enabled: !!id,
    refetchInterval: 10_000, // 10s 轮询，看 phase 推进
  });
}

export function useCreateGoal() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: GoalCreate) => apiClient.post<Goal>('/goals', body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['goals'] }),
  });
}

export interface GoalUpdateBody {
  type?: GoalType;
  target?: Record<string, unknown>;
  /** 不传 = 不动；传 ISO 字符串 = 设置；不需要"清空"功能 */
  deadline?: string;
  /** 停止目标：active → cancelled / failed */
  status?: GoalStatus;
  target_likes?: number;
  notes_per_round?: number;
  max_rounds?: number;
}

export function useUpdateGoal() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: GoalUpdateBody }) =>
      apiClient.patch<Goal>(`/goals/${id}`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['goals'] });
      qc.invalidateQueries({ queryKey: ['goal'] });
    },
  });
}

export function useDeleteGoal() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.delete<void>(`/goals/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['goals'] });
      qc.invalidateQueries({ queryKey: ['goal'] });
    },
  });
}
