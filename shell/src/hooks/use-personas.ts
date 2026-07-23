import { useQuery } from '@tanstack/react-query';
import { apiClient } from '@/lib/api-client';
import type { KbDocument } from '@/types/api';

export function usePersonas(params?: {
  /** v0.7+ 业务过滤 */
  business_id?: string;
}) {
  return useQuery<{ items: KbDocument[] }>({
    queryKey: ['personas', params],
    queryFn: () =>
      apiClient.get<{ items: KbDocument[] }>('/kb/documents', {
        params: { ...params, type: 'persona' },
      }),
  });
}
