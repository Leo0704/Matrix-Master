import { useMutation } from '@tanstack/react-query';
import { apiClient } from '@/lib/api-client';
import { useActiveBusinessId } from '@/stores/ui-store';
import type { ChatHistoryMessage, ChatRequest, ChatResponse } from '@/types/api';

/**
 * 与后端 /chat 路由对话（运营小助手）。
 * - 接受完整 history（前端 localStorage 自管）
 * - 后端无状态，每次靠 history 重建上下文
 *
 * 5 类 intent：ask_data / diagnose / preview_change / browse_kb / chitchat。
 * 写操作必须走 preview_change 拿到 confirmation_token，再用 useConfirmChat。
 */
export function useChat() {
  return useMutation({
    mutationFn: (body: ChatRequest) =>
      apiClient.post<ChatResponse>('/chat', body),
  });
}

/**
 * 确认 / 取消 preview_change 的 confirmation token。
 * 后端走 /confirm <token> 路径短路，直接调 apply_change 工具（不再调 LLM）。
 */
export function useConfirmChat() {
  const activeBusinessId = useActiveBusinessId();
  return useMutation({
    mutationFn: (token: string) =>
      apiClient.post<ChatResponse>('/chat', {
        message: `/confirm ${token}`,
        history: [],
        business_id: activeBusinessId ?? '',  // v0.7+ 业务归属（后端必填，缺则 422）
      }),
  });
}

export type { ChatHistoryMessage };