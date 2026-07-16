import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/lib/api-client';
import type {
  KbDocument,
  KbDocumentCreate,
  KbDocumentUpdate,
  KbSearchRequest,
  KbSearchHit,
  KbType,
  ViralIngestRequest,
  ViralIngestResponse,
} from '@/types/api';

export interface KbListParams {
  type?: KbType;
  is_published?: boolean;
  limit?: number;
  offset?: number;
  /** v0.7+ 业务过滤 */
  business_id?: string;
}

export function useKbDocuments(params?: KbListParams) {
  return useQuery<{ items: KbDocument[]; total: number }>({
    queryKey: ['kb-documents', params],
    queryFn: () => apiClient.get('/kb/documents', { params }),
  });
}

export function useKbDocument(id: string | undefined) {
  return useQuery<KbDocument>({
    queryKey: ['kb-document', id],
    queryFn: () => apiClient.get<KbDocument>(`/kb/documents/${id}`),
    enabled: !!id,
  });
}

export function useCreateKbDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: KbDocumentCreate) =>
      apiClient.post<KbDocument>('/kb/documents', body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['kb-documents'] }),
  });
}

export function useIngestViral() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ViralIngestRequest) =>
      apiClient.post<ViralIngestResponse>('/kb/ingest-viral', body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['kb-documents'] }),
  });
}

export function useUploadKbDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ file, type, title, is_published }: {
      file: File; type: KbType; title?: string; is_published?: boolean;
    }) => {
      const form = new FormData();
      form.append('file', file);
      form.append('type', type);
      if (title) form.append('title', title);
      form.append('is_published', String(is_published ?? true));
      return apiClient.postForm<KbDocument>('/kb/documents/upload', form);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['kb-documents'] }),
  });
}

export function useUpdateKbDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: KbDocumentUpdate }) =>
      apiClient.patch<KbDocument>(`/kb/documents/${id}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['kb-documents'] }),
  });
}

export function useDeleteKbDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.delete(`/kb/documents/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['kb-documents'] }),
  });
}

export function usePublishKbDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, reviewer, comment }: { id: string; reviewer: string; comment?: string }) =>
      apiClient.post<{ doc_id: string; is_published: boolean }>(
        `/kb/documents/${id}/publish`,
        { reviewer, comment },
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['kb-documents'] }),
  });
}

export function useKbSearch() {
  return useMutation({
    mutationFn: (body: KbSearchRequest) =>
      apiClient.post<{ items: KbSearchHit[] }>('/kb/search', body),
  });
}
