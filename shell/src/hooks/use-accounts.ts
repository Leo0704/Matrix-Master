import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/lib/api-client';
import type { Account, AccountCreate } from '@/types/api';

export function useAccounts(params?: {
  device_id?: string;
  persona_id?: string;
  status?: string;
  /** v0.7+ 业务过滤 */
  business_id?: string;
}) {
  return useQuery<{ items: Account[] }>({
    queryKey: ['accounts', params],
    queryFn: () => apiClient.get<{ items: Account[] }>('/accounts', { params }),
  });
}

export function useCreateAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AccountCreate) => apiClient.post<Account>('/accounts', body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['accounts'] }),
  });
}

export interface AccountUpdateBody {
  handle?: string;
  persona_id?: string;
  device_id?: string;
}

export function useUpdateAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: AccountUpdateBody }) =>
      apiClient.patch<Account>(`/accounts/${id}`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['accounts'] });
      qc.invalidateQueries({ queryKey: ['account'] });
      qc.invalidateQueries({ queryKey: ['devices'] });
      qc.invalidateQueries({ queryKey: ['account-content-stats'] });
    },
  });
}
