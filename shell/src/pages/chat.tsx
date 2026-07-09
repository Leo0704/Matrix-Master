import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { CheckCircle2, RotateCcw, Send, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Textarea } from '@/components/ui/textarea';
import { useChat } from '@/hooks/use-chat';
import type { ChatHistoryMessage, ThemeTarget } from '@/types/api';

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
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
        m && typeof m === 'object' && typeof m.id === 'string' &&
        (m.role === 'user' || m.role === 'assistant') && typeof m.content === 'string'
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

export function Chat() {
  const [messages, setMessages] = useState<ChatMessage[]>(() => loadMessages());
  const [text, setText] = useState('');
  const [confirmed, setConfirmed] = useState<ThemeTarget | null>(null);
  const [goalId, setGoalId] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const chat = useChat();
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // 持久化 + 自动滚到底
  useEffect(() => {
    saveMessages(messages);
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  function reset() {
    setMessages([]);
    setConfirmed(null);
    setGoalId(null);
    setRunId(null);
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      // ignore
    }
  }

  async function send() {
    const trimmed = text.trim();
    if (!trimmed || chat.isPending) return;
    const userMsg: ChatMessage = {
      id: `u-${Date.now()}`,
      role: 'user',
      content: trimmed,
    };
    setMessages((m) => [...m, userMsg]);
    setText('');
    try {
      const history: ChatHistoryMessage[] = messages
        .filter((m) => m.role === 'user' || m.role === 'assistant')
        .map((m) => ({ role: m.role, content: m.content }));
      const resp = await chat.mutateAsync({ message: trimmed, history });
      setMessages((m) => [
        ...m,
        { id: `a-${Date.now()}`, role: 'assistant', content: resp.reply },
      ]);
      if (resp.theme_confirmed && resp.theme_payload) {
        setConfirmed(resp.theme_payload as ThemeTarget);
        const action = resp.action;
        if (action && typeof action === 'object') {
          const p = (action as { payload?: Record<string, unknown> }).payload;
          if (p && typeof p === 'object') {
            if (typeof p.goal_id === 'string') setGoalId(p.goal_id);
            if (typeof p.run_id === 'string') setRunId(p.run_id);
          }
        }
      }
    } catch (e) {
      setMessages((m) => [
        ...m,
        { id: `a-${Date.now()}`, role: 'assistant', content: `错误：${(e as Error).message}` },
      ]);
    }
  }

  return (
    <div className="flex h-[calc(100vh-4rem)] flex-col space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">对话</h1>
          <p className="text-sm text-muted-foreground">
            告诉主控你想做什么样的矩阵；多聊几轮，主题确定后会自动建目标 + 启动 Agent。
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={reset} disabled={messages.length === 0}>
          <RotateCcw className="mr-1 h-4 w-4" /> 重置
        </Button>
      </div>

      {confirmed && (
        <Card className="border-emerald-500/40 bg-emerald-50/30">
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-base text-emerald-700">
              <CheckCircle2 className="h-5 w-5" /> 主题已确定
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            {confirmed.theme && (
              <div><span className="text-muted-foreground">主题：</span>{confirmed.theme}</div>
            )}
            {confirmed.audience && (
              <div><span className="text-muted-foreground">人群：</span>{confirmed.audience}</div>
            )}
            {confirmed.product_category && (
              <div><span className="text-muted-foreground">类目：</span>{confirmed.product_category}</div>
            )}
            {confirmed.goal_type && (
              <div><span className="text-muted-foreground">动作：</span>{confirmed.goal_type}</div>
            )}
            <div className="flex items-center gap-2 pt-2">
              {goalId && (
                <Button size="sm" asChild>
                  <Link to={`/goals/${goalId}`}>查看目标</Link>
                </Button>
              )}
              {runId && (
                <Button size="sm" variant="outline" asChild>
                  <Link to={`/agent-runs/${runId}`}>查看 Agent run</Link>
                </Button>
              )}
              <Button size="sm" variant="ghost" onClick={reset}>
                <Trash2 className="mr-1 h-3 w-3" /> 清空开新对话
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      <div
        ref={scrollRef}
        className="flex-1 space-y-2 overflow-y-auto rounded-md border bg-card p-3 text-sm"
      >
        {messages.length === 0 && (
          <p className="text-muted-foreground">
            还没开始对话。试试：「我是卖鞋子的，主打平价百搭，面向大学生」。
          </p>
        )}
        {messages.map((m) => (
          <div key={m.id} className={m.role === 'user' ? 'text-right' : 'text-left'}>
            <span
              className={
                m.role === 'user'
                  ? 'inline-block max-w-[80%] rounded-md bg-primary px-3 py-2 text-primary-foreground'
                  : 'inline-block max-w-[80%] rounded-md bg-muted px-3 py-2'
              }
            >
              {m.content}
            </span>
          </div>
        ))}
        {chat.isPending && (
          <div className="text-left">
            <span className="inline-block rounded-md bg-muted px-3 py-2 text-muted-foreground">
              思考中…
            </span>
          </div>
        )}
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
          placeholder="输入指令…（⌘/Ctrl+Enter 发送）"
        />
        <Button onClick={send} disabled={!text.trim() || chat.isPending} size="icon">
          <Send className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}
