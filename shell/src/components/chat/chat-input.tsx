import { useState } from 'react';
import { Send } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { useChat } from '@/hooks/use-chat';
import type { ChatHistoryMessage } from '@/types/api';

export interface ChatMessage extends ChatHistoryMessage {
  /** 消息 ID（用于 React key，不传后端） */
  id: string;
}

/**
 * ChatInput — 兼容两种用法：
 * 1. 不传 props（dashboard 这种内嵌场景）：内部 useState 自管 messages
 * 2. 传 messages + onAppend（/chat 页要持久化到 localStorage）：受控模式
 */
export function ChatInput({
  messages: controlledMessages,
  onAppend: controlledAppend,
}: {
  messages?: ChatMessage[];
  onAppend?: (m: ChatMessage) => void;
} = {}) {
  const [internalMessages, setInternalMessages] = useState<ChatMessage[]>([]);
  const [text, setText] = useState('');
  const chat = useChat();

  // 受控 vs 非受控
  const isControlled = controlledMessages != null && controlledAppend != null;
  const messages = isControlled ? controlledMessages! : internalMessages;
  const append = (m: ChatMessage) => {
    if (isControlled) {
      controlledAppend!(m);
    } else {
      setInternalMessages((prev) => [...prev, m]);
    }
  };

  async function send() {
    const trimmed = text.trim();
    if (!trimmed || chat.isPending) return;
    append({ id: `u-${Date.now()}`, role: 'user', content: trimmed });
    setText('');
    try {
      // 把当前累积的 messages 作为 history 传出去
      const history: ChatHistoryMessage[] = messages
        .filter((m) => m.role === 'user' || m.role === 'assistant')
        .map((m) => ({ role: m.role, content: m.content }));
      const resp = await chat.mutateAsync({ message: trimmed, history });
      append({ id: `a-${Date.now()}`, role: 'assistant', content: resp.reply });
    } catch (e) {
      append({
        id: `a-${Date.now()}`,
        role: 'assistant',
        content: `错误：${(e as Error).message}`,
      });
    }
  }

  return (
    <div className="space-y-3">
      <div className="space-y-2 max-h-64 overflow-y-auto rounded-md border bg-muted/30 p-3 text-sm">
        {messages.length === 0 && (
          <p className="text-muted-foreground">
            试试：「我是卖鞋子的，主打平价百搭」「我的目标人群是大学生」
          </p>
        )}
        {messages.map((m) => (
          <div
            key={m.id}
            className={m.role === 'user' ? 'text-right' : 'text-left'}
          >
            <span
              className={
                m.role === 'user'
                  ? 'inline-block rounded-md bg-primary px-3 py-1 text-primary-foreground'
                  : 'inline-block rounded-md bg-muted px-3 py-1'
              }
            >
              {m.content}
            </span>
          </div>
        ))}
      </div>
      <div className="flex items-end gap-2">
        <Textarea
          rows={2}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              send();
            }
          }}
          placeholder="自然语言指令…（⌘/Ctrl+Enter 发送）"
        />
        <Button onClick={send} disabled={!text.trim() || chat.isPending} size="icon">
          <Send className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}
