import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/lib/api-client';
import type { Account, AccountCreate } from '@/types/api';

export function useAccounts(params?: { device_id?: string; persona_id?: string; status?: string }) {
  return useQuery<{ items: Account[] }>({
    queryKey: ['accounts', params],
    queryFn: () => apiClient.get<{ items: Account[] }>('/accounts', { params }),
  });
}

export function useAccount(id: string | undefined) {
  return useQuery<Account>({
    queryKey: ['account', id],
    queryFn: () => apiClient.get<Account>(`/accounts/${id}`),
    enabled: !!id,
  });
}

export function useCreateAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AccountCreate) => apiClient.post<Account>('/accounts', body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['accounts'] }),
  });
}
