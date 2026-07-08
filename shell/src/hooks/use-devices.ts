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
