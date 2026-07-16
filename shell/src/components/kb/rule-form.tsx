import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import type { KbDocument, KbDocumentCreate } from '@/types/api';
import { useActiveBusinessId } from '@/stores/ui-store';

interface RuleFormProps {
  initial?: KbDocument;
  onSubmit: (body: KbDocumentCreate) => Promise<void>;
  onCancel?: () => void;
  submitting?: boolean;
}

/**
 * 规则表单：title + content。
 * content 约定每行一条 [禁] 词（或自由文本做检索素材）。
 * 提供"违禁词列表"快捷输入：逗号分隔，自动转成 [禁] 多行格式。
 */
export function RuleForm({ initial, onSubmit, onCancel, submitting }: RuleFormProps) {
  const activeBusinessId = useActiveBusinessId();
  const [title, setTitle] = useState(initial?.title ?? '');
  const [content, setContent] = useState(initial?.content ?? '');
  const [words, setWords] = useState('');

  useEffect(() => {
    if (initial) {
      setTitle(initial.title ?? '');
      setContent(initial.content);
    }
  }, [initial]);

  // 解析已有 content 里的 [禁] 词 → 回填到快捷输入框
  useEffect(() => {
    const lines = (initial?.content ?? '').split(/\r?\n/);
    const found: string[] = [];
    for (const line of lines) {
      const m = line.match(/^\[(禁|forbidden)\]\s*(.+)$/i);
      if (m && m[1]) found.push(m[1].trim());
    }
    if (found.length) setWords(found.join(', '));
  }, [initial]);

  async function handleSubmit() {
    if (!title.trim()) return;
    // 合并：[禁] 行 + 用户原始 content
    const forbiddenLines = words
      .split(/[,，\s]+/)
      .map((s) => s.trim())
      .filter(Boolean)
      .map((w) => `[禁] ${w}`);
    const baseContent = content
      .split(/\r?\n/)
      .filter((l) => l.trim() && !/^\[(禁|forbidden)\]\s*/i.test(l));
    const finalContent = [...forbiddenLines, ...baseContent].join('\n').trim();
    if (!finalContent) return;
    if (!activeBusinessId) return;
    await onSubmit({
      type: 'rule',
      title: title.trim(),
      content: finalContent,
      is_published: false,
      business_id: activeBusinessId,  // v0.7+ 业务归属
    });
  }

  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <Label htmlFor="rule-title">规则标题</Label>
        <Input
          id="rule-title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="例：广告法违禁词"
        />
      </div>
      <div className="space-y-1">
        <Label htmlFor="rule-words">违禁词列表（逗号分隔，自动加 [禁] 前缀）</Label>
        <Textarea
          id="rule-words"
          rows={3}
          value={words}
          onChange={(e) => setWords(e.target.value)}
          placeholder="例：最, 第一, 绝对, 国家级"
        />
      </div>
      <div className="space-y-1">
        <Label htmlFor="rule-content">补充规则文本（可选，例：字数 / 配图要求）</Label>
        <Textarea
          id="rule-content"
          rows={3}
          value={content}
          onChange={(e) => setContent(e.target.value)}
          placeholder="自由文本，每行一条；AI 写笔记时会参考"
        />
      </div>
      <div className="flex items-center justify-end gap-2 pt-2">
        {onCancel && (
          <Button variant="ghost" onClick={onCancel} disabled={submitting}>
            取消
          </Button>
        )}
        <Button onClick={handleSubmit} disabled={submitting || !title.trim()}>
          {submitting ? '保存中…' : initial ? '更新' : '创建'}
        </Button>
      </div>
    </div>
  );
}