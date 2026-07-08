import { useState } from 'react';
import { z } from 'zod';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { Label } from '@/components/ui/label';
import { useCreateGoal } from '@/hooks/use-goals';
import { toast } from '@/components/ui/use-toast';

const formSchema = z.object({
  description: z.string().min(2, '请描述目标'),
});

const templates = [
  '本周美妆号净涨 500 粉',
  '每天发 1 篇数码测评',
  '本周互动率提升 20%',
  '每月给母婴号写 30 篇笔记，每篇至少 500 字',
];

export function GoalForm({ onCreated }: { onCreated?: () => void }) {
  const [text, setText] = useState('');
  const { mutate, isPending } = useCreateGoal();

  async function submit() {
    const parsed = formSchema.safeParse({ description: text });
    if (!parsed.success) {
      toast({ title: '请输入目标描述', variant: 'destructive' });
      return;
    }
    try {
      // 提交时把整段 description 作为 theme 主字段，其他结构字段留空待 LLM 在 chat 对话中补全
      await mutate({
        type: 'natural_language',
        target: {
          theme: parsed.data.description,
          audience: '',
          product_category: '',
        },
      });
      toast({ title: '目标已创建', description: 'Agent 将开始执行' });
      setText('');
      onCreated?.();
    } catch (e) {
      toast({
        title: '创建失败',
        description: (e as Error).message,
        variant: 'destructive',
      });
    }
  }

  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <Label htmlFor="goal-desc">用自然语言描述目标</Label>
        <Textarea
          id="goal-desc"
          rows={3}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="例：本周美妆号净涨 500 粉"
        />
      </div>
      <div className="flex flex-wrap gap-2">
        <span className="text-xs text-muted-foreground">模板：</span>
        {templates.map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setText(t)}
            className="rounded-full border bg-muted/50 px-3 py-1 text-xs hover:bg-accent"
          >
            {t}
          </button>
        ))}
      </div>
      <Button onClick={submit} disabled={isPending || text.length < 2}>
        {isPending ? '提交中…' : '创建目标'}
      </Button>
    </div>
  );
}
