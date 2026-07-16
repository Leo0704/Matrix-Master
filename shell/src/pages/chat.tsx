import { useEffect, useReducer, useRef } from 'react';
import { Loader2, RotateCcw, Send } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { PageHeader } from '@/components/common/page-header';
import { ChatBlockRenderer } from '@/components/chat/chat-block-renderer';
import { QuickPromptButtons } from '@/components/chat/quick-prompt-buttons';
import { useChat, useConfirmChat } from '@/hooks/use-chat';
import type { ChatHistoryMessage, ChatResponse } from '@/types/api';

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  action?: ChatResponse['action'];
  /** token 一旦被消费（confirm/cancel），标记为 true，UI 不再渲染 block */
  consumed?: boolean;
}

const STORAGE_KEY = 'matrix.chat.messages.v1';

function loadMessages(): ChatMessage[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (m): m is ChatMessage =>
        m &&
        typeof m === 'object' &&
        typeof m.id === 'string' &&
        (m.role === 'user' || m.role === 'assistant') &&
        typeof m.content === 'string',
    );
  } catch {
    return [];
  }
}

function saveMessages(msgs: ChatMessage[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(msgs));
  } catch {
    // localStorage 满 / 不可用 — 静默失败
  }
}

// ---------------------------------------------------------------------------
// useReducer 状态机
// ---------------------------------------------------------------------------

type State = {
  messages: ChatMessage[];
  text: string;
};

type Action =
  | { type: 'set_text'; text: string }
  | { type: 'append'; message: ChatMessage }
  | { type: 'mark_consumed'; id: string }
  | { type: 'reset' };

function reducer(s: State, a: Action): State {
  switch (a.type) {
    case 'set_text':
      return { ...s, text: a.text };
    case 'append':
      return { ...s, messages: [...s.messages, a.message] };
    case 'mark_consumed':
      return {
        ...s,
        messages: s.messages.map((m) =>
          m.id === a.id ? { ...m, consumed: true } : m,
        ),
      };
    case 'reset':
      return { messages: [], text: '' };
    default:
      return s;
  }
}

export function Chat() {
  const [state, dispatch] = useReducer(reducer, undefined, () => ({
    messages: loadMessages(),
    text: '',
  }));
  const chat = useChat();
  const confirmChat = useConfirmChat();
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // 持久化 + 自动滚到底
  useEffect(() => {
    saveMessages(state.messages);
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [state.messages]);

  function reset() {
    dispatch({ type: 'reset' });
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      // ignore
    }
  }

  function pushAssistantFromResponse(
    resp: ChatResponse,
    prefixId?: string,
  ): string {
    const id = prefixId ?? `a-${Date.now()}`;
    dispatch({
      type: 'append',
      message: {
        id,
        role: 'assistant',
        content: resp.reply,
        action: resp.action,
      },
    });
    return id;
  }

  async function send(overrideText?: string) {
    const trimmed = (overrideText ?? state.text).trim();
    if (!trimmed || chat.isPending) return;
    const userMsg: ChatMessage = {
      id: `u-${Date.now()}`,
      role: 'user',
      content: trimmed,
    };
    dispatch({ type: 'append', message: userMsg });
    dispatch({ type: 'set_text', text: '' });
    try {
      const history: ChatHistoryMessage[] = state.messages
        .filter((m) => m.role === 'user' || m.role === 'assistant')
        .map((m) => ({ role: m.role, content: m.content }));
      const resp = await chat.mutateAsync({ message: trimmed, history });
      pushAssistantFromResponse(resp);
    } catch (e) {
      dispatch({
        type: 'append',
        message: {
          id: `a-${Date.now()}`,
          role: 'assistant',
          content: `错误：${(e as Error).message}`,
        },
      });
    }
  }

  async function handleConfirm(token: string, sourceMessageId: string) {
    try {
      const resp = await confirmChat.mutateAsync(token);
      // 标记原 preview 消息已消费
      dispatch({ type: 'mark_consumed', id: sourceMessageId });
      // 追加执行结果
      pushAssistantFromResponse(resp, `a-confirm-${Date.now()}`);
    } catch (e) {
      pushAssistantFromResponse(
        {
          reply: `确认执行失败：${(e as Error).message}`,
          action: { type: 'llm_error', payload: {} },
          error_hint: '请重试或检查 token 是否过期（10 分钟）',
        },
        `a-confirm-err-${Date.now()}`,
      );
    }
  }

  async function handleCancel(token: string, sourceMessageId: string) {
    try {
      await chat.mutateAsync({ message: `/cancel ${token}`, history: [] });
    } catch {
      // ignore
    }
    dispatch({ type: 'mark_consumed', id: sourceMessageId });
  }

  return (
    <div className="flex h-full flex-col space-y-3">
      <PageHeader
        title="对话"
        description="运营小助手：问数据 / 诊断 / 调参 / 批量 / 审 KB。建目标请去 /goals 手动表单。"
        actions={
          <Button
            variant="ghost"
            size="sm"
            onClick={reset}
            disabled={state.messages.length === 0}
          >
            <RotateCcw className="mr-1 h-4 w-4" /> 重置
          </Button>
        }
      />

      <div
        ref={scrollRef}
        className="flex-1 space-y-2 overflow-y-auto rounded-md border bg-card p-3 text-sm"
      >
        {state.messages.length === 0 && (
          <p className="text-muted-foreground">
            还没开始对话。试试下方快捷按钮，或直接打字问运营问题。
          </p>
        )}
        {state.messages.map((m) => (
          <div key={m.id} className={m.role === 'user' ? 'text-right' : 'text-left'}>
            <div className="space-y-2">
              <span
                className={
                  m.role === 'user'
                    ? 'inline-block max-w-[80%] rounded-md bg-primary px-3 py-2 text-primary-foreground'
                    : 'inline-block max-w-[80%] rounded-md bg-muted px-3 py-2'
                }
              >
                {m.content}
              </span>
              {m.role === 'assistant' && m.action && !m.consumed && (
                <div className="ml-0 max-w-[80%]">
                  <ChatBlockRenderer
                    action={m.action}
                    onConfirm={(token) => handleConfirm(token, m.id)}
                    onCancel={(token) => handleCancel(token, m.id)}
                  />
                </div>
              )}
              {m.role === 'assistant' && m.action && m.consumed && (
                <div className="ml-0 max-w-[80%] text-xs italic text-muted-foreground">
                  （已处理）
                </div>
              )}
            </div>
          </div>
        ))}
        {(chat.isPending || confirmChat.isPending) && (
          <div className="text-left">
            <span className="inline-block rounded-md bg-muted px-3 py-2 text-muted-foreground">
              <Loader2 className="mr-1 inline h-3 w-3 animate-spin" /> 思考中…
            </span>
          </div>
        )}
      </div>

      <QuickPromptButtons onPick={(p) => send(p)} disabled={chat.isPending} />

      <div className="flex items-end gap-2">
        <Textarea
          rows={2}
          value={state.text}
          onChange={(e) => dispatch({ type: 'set_text', text: e.target.value })}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              send();
            }
          }}
          placeholder="输入指令…（⌘/Ctrl+Enter 发送）"
        />
        <Button
          onClick={() => send()}
          disabled={!state.text.trim() || chat.isPending}
          size="icon"
        >
          {chat.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Send className="h-4 w-4" />
          )}
        </Button>
      </div>
    </div>
  );
}