import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/lib/api-client';
import type { Persona, PersonaCreate } from '@/types/api';

export function usePersonas() {
  return useQuery<{ items: Persona[] }>({
    queryKey: ['personas'],
    queryFn: () => apiClient.get<{ items: Persona[] }>('/personas'),
  });
}

export function usePersona(id: string | undefined) {
  return useQuery<Persona>({
    queryKey: ['persona', id],
    queryFn: () => apiClient.get<Persona>(`/personas/${id}`),
    enabled: !!id,
  });
}

export function useCreatePersona() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: PersonaCreate) => apiClient.post<Persona>('/personas', body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['personas'] }),
  });
}

export interface PersonaUpdate {
  name?: string;
  tone?: string;
  style_guide?: string;
  forbidden_words?: string[];
  sample_note_ids?: string[];
}

export function useUpdatePersona() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: PersonaUpdate }) =>
      apiClient.patch<Persona>(`/personas/${id}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['personas'] }),
  });
}

export function useDeletePersona() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.delete(`/personas/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['personas'] }),
  });
}
