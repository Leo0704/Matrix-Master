import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/lib/api-client';
import type { Device, DeviceRegisterRequest } from '@/types/api';

export function useDevices(params?: { status?: string; tag?: string }) {
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

/** 解绑设备：把绑到这台设备上的账号 device_id 清空（设备坏了换新机场景） */
export function useUnbindDevice() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      apiClient.post<{ device_id: string; unbound_account_handle: string | null }>(
        `/devices/${id}/unbind`,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['devices'] });
      qc.invalidateQueries({ queryKey: ['device'] });
      qc.invalidateQueries({ queryKey: ['accounts'] });
      qc.invalidateQueries({ queryKey: ['account-content-stats'] });
    },
  });
}
