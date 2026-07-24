import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/lib/api-client';
import type { NotificationItem, NotificationListResponse } from '@/types/api';

interface UseNotificationsParams {
  unread?: boolean;
  code?: string;
  severity?: string;
  limit?: number;
  offset?: number;
  /** v0.7+ 业务过滤 */
  business_id?: string;
}

export function useNotifications(params?: UseNotificationsParams) {
  return useQuery<NotificationListResponse>({
    queryKey: ['notifications', params],
    queryFn: () => apiClient.get<NotificationListResponse>('/notifications', { params }),
    refetchInterval: 8_000, // 与 alerts 同步轮询
  });
}

/**
 * 未读数量：从最新 unread 列表里取 total；读已读时会自动刷新。
 */
export function useUnreadNotificationCount() {
  const { data } = useNotifications({ unread: true, limit: 1 });
  return data?.total ?? 0;
}

export function useMarkRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (ids?: string[]) =>
      apiClient.post<{ marked: number }>('/notifications/read', { ids: ids ?? null }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['notifications'] }),
  });
}

export function useDeleteNotification() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      apiClient.delete<{ deleted: number }>(`/notifications/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['notifications'] }),
  });
}

export function useClearReadNotifications() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiClient.post<{ deleted: number }>('/notifications/clear-read'),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['notifications'] }),
  });
}

/** 兼容旧名导出，避免别处已经引用 NotificationItem 类型——实际类型从 @/types/api 走 */
export type { NotificationItem };