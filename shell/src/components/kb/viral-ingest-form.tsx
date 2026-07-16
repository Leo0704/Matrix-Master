import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { toast } from '@/components/ui/use-toast';
import { useIngestViral } from '@/hooks/use-kb';
import type { ViralIngestRequest } from '@/types/api';

interface ViralIngestFormProps {
  onDone?: () => void;
  onCancel?: () => void;
}

/**
 * 粘贴别人的爆款文案 → AI 拆解 → 入库。
 * 只粘文字（不抓链接）：一个大输入框贴正文，标题/数据可选填。
 * 提交后存一条「历史爆款」记录（已发布）+ 一张「套路卡」（草稿，需人工发布）。
 */
export function ViralIngestForm({ onDone, onCancel }: ViralIngestFormProps) {
  const ingestMut = useIngestViral();
  const [rawText, setRawText] = useState('');
  const [title, setTitle] = useState('');
  const [likes, setLikes] = useState('');
  const [collects, setCollects] = useState('');
  const [comments, setComments] = useState('');

  async function handleSubmit() {
    if (!rawText.trim()) return;

    const metrics: Record<string, number> = {};
    const push = (key: string, val: string) => {
      const n = Number(val);
      if (val.trim() && Number.isFinite(n)) metrics[key] = n;
    };
    push('likes', likes);
    push('collects', collects);
    push('comments', comments);

    const body: ViralIngestRequest = {
      raw_text: rawText.trim(),
      title: title.trim() || undefined,
      metrics: Object.keys(metrics).length ? metrics : undefined,
    };

    try {
      const res = await ingestMut.mutateAsync(body);
      toast({
        title: '已拆解入库',
        description: res.strategy_card_pending
          ? '爆款记录已发布；套路卡已生成（草稿），去「套路卡」tab 点发布后 AI 才会用'
          : '爆款记录已发布，AI 现在能参考',
      });
      setRawText('');
      setTitle('');
      setLikes('');
      setCollects('');
      setComments('');
      onDone?.();
    } catch (e) {
      toast({
        title: '拆解失败',
        description: (e as Error)?.message || '请稍后再试',
        variant: 'destructive',
      });
    }
  }

  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <Label htmlFor="viral-text">爆款文案（粘贴正文）</Label>
        <Textarea
          id="viral-text"
          rows={8}
          value={rawText}
          onChange={(e) => setRawText(e.target.value)}
          placeholder="把小红书爆款笔记的标题 + 正文整段粘进来，AI 会自动拆解它为什么火"
        />
      </div>
      <div className="space-y-1">
        <Label htmlFor="viral-title">标题（可选，留空由 AI 从正文提炼）</Label>
        <Input
          id="viral-title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="一句话标题"
        />
      </div>
      <div className="space-y-1">
        <Label>数据（可选，随手填看到的数字）</Label>
        <div className="grid grid-cols-3 gap-2">
          <Input
            type="number"
            value={likes}
            onChange={(e) => setLikes(e.target.value)}
            placeholder="点赞"
          />
          <Input
            type="number"
            value={collects}
            onChange={(e) => setCollects(e.target.value)}
            placeholder="收藏"
          />
          <Input
            type="number"
            value={comments}
            onChange={(e) => setComments(e.target.value)}
            placeholder="评论"
          />
        </div>
      </div>
      <div className="flex items-center justify-end gap-2 pt-2">
        {onCancel && (
          <Button variant="ghost" onClick={onCancel} disabled={ingestMut.isPending}>
            取消
          </Button>
        )}
        <Button onClick={handleSubmit} disabled={ingestMut.isPending || !rawText.trim()}>
          {ingestMut.isPending ? '拆解中…' : '拆解入库'}
        </Button>
      </div>
    </div>
  );
}
