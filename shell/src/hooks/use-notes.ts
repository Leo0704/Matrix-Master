import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/lib/api-client';
import type { Note, NoteStatus } from '@/types/api';

export function useNotes(params?: { account_id?: string; status?: string; limit?: number; offset?: number }) {
  return useQuery<{ items: Note[]; total?: number }>({
    queryKey: ['notes', params],
    queryFn: () => apiClient.get<{ items: Note[]; total?: number }>('/notes', { params }),
  });
}

export function useNote(id: string | undefined) {
  return useQuery<Note>({
    queryKey: ['note', id],
    queryFn: () => apiClient.get<Note>(`/notes/${id}`),
    enabled: !!id,
  });
}

export interface NoteCreateBody {
  account_id: string;
  title: string;
  content: string;
  images?: string[];
  tags?: string[];
  status?: NoteStatus;
  scheduled_at?: string;
}

export interface NoteUpdateBody {
  title?: string;
  content?: string;
  images?: string[];
  tags?: string[];
  status?: NoteStatus;
  scheduled_at?: string;
}

export function useCreateNote() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: NoteCreateBody) => apiClient.post<Note>('/notes', body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['notes'] }),
  });
}

export function useUpdateNote() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: NoteUpdateBody }) =>
      apiClient.patch<Note>(`/notes/${id}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['notes'] }),
  });
}

export function useDeleteNote() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.delete(`/notes/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['notes'] }),
  });
}
