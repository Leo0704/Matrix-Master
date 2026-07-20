import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/lib/api-client';
import type { Device, DeviceRegisterRequest } from '@/types/api';

export function useDevices(params?: {
  status?: string;
  tag?: string;
  /** 默认 false；传 true 时包含已退役（status=disabled）设备 */
  include_disabled?: boolean;
  /** v0.7+ 业务过滤 */
  business_id?: string;
}) {
  return useQuery<{ items: Device[] }>({
    queryKey: ['devices', params],
    queryFn: () =>
      apiClient.get<{ items: Device[] }>('/devices', { params }),
  });
}

export function useDevice(id: string | undefined) {
  return useQuery<Device>({
    queryKey: ['device', id],
    queryFn: () => apiClient.get<Device>(`/devices/${id}`),
    enabled: !!id,
  });
}

export function useRegisterDevice() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: DeviceRegisterRequest) =>
      apiClient.post<Device>('/devices', body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['devices'] }),
  });
}

export interface DeviceUpdateBody {
  nickname?: string;
  tags?: string[];
}

export function useUpdateDevice() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: DeviceUpdateBody }) =>
      apiClient.patch<Device>(`/devices/${id}`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['devices'] });
      qc.invalidateQueries({ queryKey: ['device'] });
    },
  });
}

/** 退役设备：设备永久下线，清账号绑定 + 撤销 HMAC 密钥 */
export function useRetireDevice() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      apiClient.post<{ device_id: string; unbound_account_handle: string | null }>(
        `/devices/${id}/retire`,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['devices'] });
      qc.invalidateQueries({ queryKey: ['device'] });
      qc.invalidateQueries({ queryKey: ['accounts'] });
      qc.invalidateQueries({ queryKey: ['account-content-stats'] });
    },
  });
}
