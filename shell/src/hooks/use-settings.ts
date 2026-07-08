import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/lib/api-client';

export interface AppSetting {
  key: string;
  value: Record<string, unknown>;
  description?: string | null;
  updated_at?: string | null;
}

export function useSettings() {
  return useQuery<{ items: AppSetting[] }>({
    queryKey: ['settings'],
    queryFn: () => apiClient.get<{ items: AppSetting[] }>('/settings'),
  });
}

export function useSetting(key: string) {
  return useQuery<AppSetting>({
    queryKey: ['settings', key],
    queryFn: () => apiClient.get<AppSetting>(`/settings/${key}`),
  });
}

export function useUpsertSetting() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      key,
      value,
      description,
    }: {
      key: string;
      value: Record<string, unknown>;
      description?: string;
    }) => apiClient.put<AppSetting>(`/settings/${key}`, { value, description }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['settings'] }),
  });
}
