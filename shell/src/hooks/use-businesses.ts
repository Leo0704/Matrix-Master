/**
 * use-businesses — v0.7+ 业务模型重构
 *
 * 业务是项目根，所有资源挂在业务名下。
 * 切换业务时整个 app 数据应重新加载（详见 use-active-business-id）。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/lib/api-client';
import type {
  Business,
  BusinessCreate,
  BusinessListResponse,
  BusinessStatus,
  BusinessUpdate,
} from '@/types/api';

/** 列出所有业务（含 archived）。前端按 status 过滤。 */
export function useBusinesses(params?: { status?: BusinessStatus }) {
  return useQuery<BusinessListResponse>({
    queryKey: ['businesses', params],
    queryFn: () =>
      apiClient.get<BusinessListResponse>('/businesses', { params }),
  });
}

/** 建业务。slug 全局 UNIQUE，重复返 409。 */
export function useCreateBusiness() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: BusinessCreate) =>
      apiClient.post<Business>('/businesses', body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['businesses'] }),
  });
}

/** 改业务属性（局部更新；status 不暴露）。 */
export function useUpdateBusiness() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: BusinessUpdate }) =>
      apiClient.patch<Business>(`/businesses/${id}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['businesses'] }),
  });
}

/** 软归档（幂等）。archived 业务下不能再创建资源。 */
export function useArchiveBusiness() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      apiClient.post<Business>(`/businesses/${id}/archive`, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['businesses'] }),
  });
}

/** 恢复（幂等）。 */
export function useUnarchiveBusiness() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      apiClient.post<Business>(`/businesses/${id}/unarchive`, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['businesses'] }),
  });
}