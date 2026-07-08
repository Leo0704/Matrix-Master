import { useMutation } from '@tanstack/react-query';
import { apiClient } from '@/lib/api-client';
import type { ChatHistoryMessage, ChatRequest, ChatResponse } from '@/types/api';

/**
 * 与后端 /chat 路由对话。
 * - 接受完整 history（前端 localStorage 自管）
 * - 后端无状态，每次靠 history 重建上下文
 */
export function useChat() {
  return useMutation({
    mutationFn: (body: ChatRequest) =>
      apiClient.post<ChatResponse>('/chat', body),
  });
}

export type { ChatHistoryMessage };
