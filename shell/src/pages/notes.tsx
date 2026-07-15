import { useState } from 'react';
import { Plus } from 'lucide-react';
import {
  useNotes,
  useCreateNote,
  useUpdateNote,
  useDeleteNote,
  type NoteCreateBody,
  type NoteUpdateBody,
} from '@/hooks/use-notes';
import { useAccounts } from '@/hooks/use-accounts';
import { NoteCard } from '@/components/notes/note-card';
import { PageHeader } from '@/components/common/page-header';
import { ErrorState } from '@/components/common/error-state';
import { EmptyState } from '@/components/common/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { toast } from '@/components/ui/use-toast';
import type { Note, NoteStatus } from '@/types/api';

const STATUSES: { value: NoteStatus | 'all'; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'draft', label: '草稿' },
  { value: 'reviewing', label: '审核中' },
  { value: 'scheduled', label: '已排期' },
  { value: 'published', label: '已发布' },
  { value: 'failed', label: '失败' },
];

function NoteForm({
  initial,
  defaultAccountId,
  onSubmit,
  onCancel,
  submitting,
}: {
  initial?: Note;
  /** v0.7 Phase 5：草稿可能 account_id 为空；空串表示未绑账号 */
  defaultAccountId: string | null | undefined;
  onSubmit: (body: NoteCreateBody | NoteUpdateBody) => Promise<void>;
  onCancel?: () => void;
  submitting?: boolean;
}) {
  const [accountId, setAccountId] = useState(initial?.account_id ?? defaultAccountId ?? '');
  const [title, setTitle] = useState(initial?.title ?? '');
  const [content, setContent] = useState(initial?.content ?? '');
  const [tagsText, setTagsText] = useState((initial?.tags ?? []).join(', '));
  const [status, setStatus] = useState<NoteStatus>(initial?.status ?? 'draft');

  async function handleSubmit() {
    const tags = tagsText
      .split(/[,，\s]+/)
      .map((t) => t.trim())
      .filter(Boolean);
    if (initial) {
      await onSubmit({
        title: title || undefined,
        content: content || undefined,
        tags,
        status,
      } as NoteUpdateBody);
    } else {
      await onSubmit({ account_id: accountId, title, content, tags, status } as NoteCreateBody);
    }
  }

  return (
    <div className="space-y-3">
      {!initial && (
        <div className="space-y-1">
          <Label htmlFor="note-account">所属账号</Label>
          <Input id="note-account" value={accountId} onChange={(e) => setAccountId(e.target.value)} />
          <p className="text-xs text-muted-foreground">填账号 UUID；后续可在账号页选</p>
        </div>
      )}
      <div className="space-y-1">
        <Label htmlFor="note-title">标题</Label>
        <Input id="note-title" value={title} onChange={(e) => setTitle(e.target.value)} />
      </div>
      <div className="space-y-1">
        <Label htmlFor="note-content">正文</Label>
        <Textarea id="note-content" rows={5} value={content} onChange={(e) => setContent(e.target.value)} />
      </div>
      <div className="space-y-1">
        <Label htmlFor="note-tags">标签</Label>
        <Input id="note-tags" value={tagsText} onChange={(e) => setTagsText(e.target.value)} placeholder="逗号分隔" />
      </div>
      <div className="space-y-1">
        <Label htmlFor="note-status">状态</Label>
        <select
          id="note-status"
          className="w-full rounded-md border bg-background px-3 py-1.5 text-sm"
          value={status}
          onChange={(e) => setStatus(e.target.value as NoteStatus)}
        >
          <option value="draft">草稿</option>
          <option value="reviewing">审稿中</option>
          <option value="scheduled">待发布</option>
          <option value="published">已发布</option>
          <option value="failed">失败</option>
        </select>
      </div>
      <div className="flex items-center justify-end gap-2 pt-2">
        {onCancel && (
          <Button variant="ghost" onClick={onCancel} disabled={submitting}>取消</Button>
        )}
        <Button
          onClick={handleSubmit}
          disabled={submitting || !title.trim() || !content.trim() || (!initial && !accountId.trim())}
        >
          {submitting ? '保存中…' : initial ? '更新' : '创建'}
        </Button>
      </div>
    </div>
  );
}

export function Notes() {
  const [status, setStatus] = useState<NoteStatus | 'all'>('all');
  const params = status === 'all' ? undefined : { status };
  const { data, isLoading, error, refetch } = useNotes(params);
  const accountsQ = useAccounts();
  const createMut = useCreateNote();
  const updateMut = useUpdateNote();
  const deleteMut = useDeleteNote();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Note | null>(null);

  const items = data?.items ?? [];
  const defaultAccountId = accountsQ.data?.items?.[0]?.id;

  async function handleCreate(body: NoteCreateBody | NoteUpdateBody) {
    try {
      await createMut.mutateAsync(body as NoteCreateBody);
      toast({ title: '笔记已创建' });
      setOpen(false);
    } catch (e) {
      toast({ title: '创建失败', description: (e as Error).message, variant: 'destructive' });
    }
  }

  async function handleUpdate(body: NoteCreateBody | NoteUpdateBody) {
    if (!editing) return;
    try {
      await updateMut.mutateAsync({ id: editing.id, body: body as NoteUpdateBody });
      toast({ title: '笔记已更新' });
      setEditing(null);
    } catch (e) {
      toast({ title: '更新失败', description: (e as Error).message, variant: 'destructive' });
    }
  }

  async function handleDelete(n: Note) {
    if (!confirm(`确认删除笔记「${n.title}」？`)) return;
    try {
      await deleteMut.mutateAsync(n.id);
      toast({ title: '已删除' });
    } catch (e) {
      toast({ title: '删除失败', description: (e as Error).message, variant: 'destructive' });
    }
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="内容"
        description="笔记列表 / 日历视图"
        actions={
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
              <Button>
                <Plus className="mr-1 h-4 w-4" /> 新建笔记
              </Button>
            </DialogTrigger>
            <DialogContent className="max-w-xl">
              <DialogHeader>
                <DialogTitle>新建笔记</DialogTitle>
                <DialogDescription>手动写一条笔记。AI run 也会自动创建。</DialogDescription>
              </DialogHeader>
              <NoteForm
                defaultAccountId={defaultAccountId}
                onSubmit={handleCreate}
                onCancel={() => setOpen(false)}
                submitting={createMut.isPending}
              />
            </DialogContent>
          </Dialog>
        }
      />

      <Tabs value={status} onValueChange={(v) => setStatus(v as NoteStatus | 'all')}>
        <TabsList>
          {STATUSES.map((s) => (
            <TabsTrigger key={s.value} value={s.value}>
              {s.label}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value={status} className="space-y-4">
          {error && <ErrorState error={error} onRetry={() => refetch()} />}
          {!isLoading && items.length === 0 && (
            <EmptyState title="无笔记" description="目标创建后 AI 会自动生成笔记" />
          )}
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
            {isLoading
              ? Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-40 w-full" />)
              : items.map((n) => (
                  <NoteCard key={n.id} note={n} onDelete={() => handleDelete(n)} />
                ))}
          </div>
        </TabsContent>
      </Tabs>

      <Dialog open={!!editing} onOpenChange={(o) => !o && setEditing(null)}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>编辑笔记</DialogTitle>
            <DialogDescription>状态改为 published 时自动写 published_at。</DialogDescription>
          </DialogHeader>
          {editing && (
            <NoteForm
              initial={editing}
              defaultAccountId={editing.account_id}
              onSubmit={handleUpdate}
              onCancel={() => setEditing(null)}
              submitting={updateMut.isPending}
            />
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
