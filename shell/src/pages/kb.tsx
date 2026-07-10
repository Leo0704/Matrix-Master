import { useMemo, useState } from 'react';
import { Plus, Trash2, CheckCircle2 } from 'lucide-react';
import {
  useKbDocuments,
  useCreateKbDocument,
  useUpdateKbDocument,
  useDeleteKbDocument,
  usePublishKbDocument,
} from '@/hooks/use-kb';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { RuleForm } from '@/components/kb/rule-form';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/common/empty-state';
import { LoadingBlock } from '@/components/common/loading-spinner';
import { ErrorState } from '@/components/common/error-state';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { toast } from '@/components/ui/use-toast';
import type { KbDocument, KbDocumentCreate } from '@/types/api';

export function KB() {
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">知识库</h1>
        <p className="text-sm text-muted-foreground">Agent 写笔记的参考材料库，按类型分 4 个 tab</p>
      </div>

      <Tabs defaultValue="brand">
        <TabsList>
          <TabsTrigger value="brand">品牌</TabsTrigger>
          <TabsTrigger value="persona">人设</TabsTrigger>
          <TabsTrigger value="rule">规则</TabsTrigger>
          <TabsTrigger value="history">历史爆款</TabsTrigger>
        </TabsList>

        <TabsContent value="brand" className="space-y-4">
          <TypeTab ktype="brand" label="品牌资料" placeholder="卖点 / 风格 / 定位" />
        </TabsContent>
        <TabsContent value="persona" className="space-y-4">
          <TypeTab ktype="persona" label="人设语气" placeholder="亲切 / 活泼 / 专业 示例文本" />
        </TabsContent>
        <TabsContent value="rule" className="space-y-4">
          <RuleTab />
        </TabsContent>
        <TabsContent value="history" className="space-y-4">
          <TypeTab ktype="history" label="历史爆款" placeholder="粘贴历史爆款正文" />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function RuleTab() {
  const { data, isLoading, error, refetch } = useKbDocuments({ type: 'rule' });
  const createMut = useCreateKbDocument();
  const updateMut = useUpdateKbDocument();
  const deleteMut = useDeleteKbDocument();
  const publishMut = usePublishKbDocument();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<KbDocument | null>(null);

  const items = data?.items ?? [];

  async function handleCreate(body: KbDocumentCreate) {
    try {
      await createMut.mutateAsync(body);
      toast({ title: '规则已创建', description: '尚未发布，Agent 不可见' });
      setOpen(false);
    } catch (e) {
      toast({ title: '创建失败', description: (e as Error).message, variant: 'destructive' });
    }
  }

  async function handleUpdate(body: KbDocumentCreate) {
    if (!editing) return;
    try {
      await updateMut.mutateAsync({ id: editing.id, body });
      toast({ title: '规则已更新' });
      setEditing(null);
    } catch (e) {
      toast({ title: '更新失败', description: (e as Error).message, variant: 'destructive' });
    }
  }

  async function handleDelete(doc: KbDocument) {
    if (!confirm(`确认删除规则「${doc.title || doc.id.slice(0, 8)}」？`)) return;
    try {
      await deleteMut.mutateAsync(doc.id);
      toast({ title: '规则已删除' });
    } catch (e) {
      toast({ title: '删除失败', description: (e as Error).message, variant: 'destructive' });
    }
  }

  async function handlePublish(doc: KbDocument) {
    try {
      await publishMut.mutateAsync({ id: doc.id, reviewer: 'operator' });
      toast({ title: '已发布', description: 'Agent 现可检索' });
    } catch (e) {
      toast({ title: '发布失败', description: (e as Error).message, variant: 'destructive' });
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          规则库：DRAFT / REVIEW 节点会检索这里。带 <code>[forbidden]</code> 前缀的行会被解析为违禁词。
        </p>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild>
            <Button>
              <Plus className="mr-1 h-4 w-4" /> 新建规则
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>新建规则</DialogTitle>
              <DialogDescription>
                规则内容由 DRAFT / REVIEW 节点按相似度检索；违禁词命中即判草稿失败。
              </DialogDescription>
            </DialogHeader>
            <RuleForm
              onSubmit={handleCreate}
              onCancel={() => setOpen(false)}
              submitting={createMut.isPending}
            />
          </DialogContent>
        </Dialog>
      </div>

      {isLoading && <LoadingBlock />}
      {error && <ErrorState error={error} onRetry={() => refetch()} />}
      {!isLoading && items.length === 0 && (
        <EmptyState title="规则库为空" description="点击「新建规则」添加第一批" />
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
        {items.map((d) => (
          <RuleCard
            key={d.id}
            doc={d}
            onEdit={() => setEditing(d)}
            onDelete={() => handleDelete(d)}
            onPublish={() => handlePublish(d)}
            publishing={publishMut.isPending}
          />
        ))}
      </div>

      <Dialog open={!!editing} onOpenChange={(o) => !o && setEditing(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>编辑规则</DialogTitle>
            <DialogDescription>修改内容会自动重新切块 + 重新计算 embedding。</DialogDescription>
          </DialogHeader>
          {editing && (
            <RuleForm
              initial={editing}
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

function RuleCard({
  doc,
  onEdit,
  onDelete,
  onPublish,
  publishing,
}: {
  doc: KbDocument;
  onEdit: () => void;
  onDelete: () => void;
  onPublish: () => void;
  publishing: boolean;
}) {
  // 解析 [forbidden] 行做卡片预览
  const forbiddenWords = useMemo(() => {
    const words: string[] = [];
    for (const line of (doc.content ?? '').split(/\r?\n/)) {
      const m = line.match(/^\[forbidden\]\s*(.+)$/i);
      if (m && m[1]) words.push(m[1].trim());
    }
    return words;
  }, [doc.content]);
  const otherLines = useMemo(
    () =>
      (doc.content ?? '')
        .split(/\r?\n/)
        .filter((l) => l.trim() && !/^\[forbidden\]\s*/i.test(l)),
    [doc.content],
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between text-base">
          <span className="truncate">{doc.title || '(无标题)'}</span>
          {doc.is_published ? (
            <Badge variant="default" className="bg-emerald-500/20 text-emerald-700">
              <CheckCircle2 className="mr-1 h-3 w-3" /> 已发布
            </Badge>
          ) : (
            <Badge variant="muted">草稿</Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        {forbiddenWords.length > 0 && (
          <div>
            <p className="text-xs text-muted-foreground">违禁词</p>
            <div className="mt-1 flex flex-wrap gap-1">
              {forbiddenWords.map((w) => (
                <Badge key={w} variant="destructive" className="text-xs">
                  {w}
                </Badge>
              ))}
            </div>
          </div>
        )}
        {otherLines.length > 0 && (
          <p className="line-clamp-3 text-muted-foreground">{otherLines.join(' / ')}</p>
        )}
        <div className="flex items-center justify-end gap-1 pt-1">
          <Button variant="ghost" size="sm" onClick={onEdit}>
            编辑
          </Button>
          {!doc.is_published && (
            <Button variant="ghost" size="sm" onClick={onPublish} disabled={publishing}>
              发布
            </Button>
          )}
          <Button variant="ghost" size="sm" onClick={onDelete} className="text-destructive">
            <Trash2 className="h-3 w-3" />
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}


// ---------- 通用 Tab：brand / persona / history 复用 ----------
function TypeTab({ ktype, label, placeholder }: { ktype: 'brand' | 'persona' | 'history'; label: string; placeholder: string }) {
  const { data, isLoading, error, refetch } = useKbDocuments({ type: ktype });
  const createMut = useCreateKbDocument();
  const updateMut = useUpdateKbDocument();
  const deleteMut = useDeleteKbDocument();
  const publishMut = usePublishKbDocument();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<KbDocument | null>(null);

  const items = data?.items ?? [];

  async function handleCreate(body: KbDocumentCreate) {
    try {
      await createMut.mutateAsync(body);
      toast({ title: `${label}已创建`, description: '尚未发布，Agent 不可见' });
      setOpen(false);
    } catch (e) {
      toast({ title: '创建失败', description: (e as Error).message, variant: 'destructive' });
    }
  }
  async function handleUpdate(body: KbDocumentCreate) {
    if (!editing) return;
    try {
      await updateMut.mutateAsync({ id: editing.id, body });
      toast({ title: `${label}已更新` });
      setEditing(null);
    } catch (e) {
      toast({ title: '更新失败', description: (e as Error).message, variant: 'destructive' });
    }
  }
  async function handleDelete(doc: KbDocument) {
    if (!confirm(`确认删除「${doc.title || doc.id.slice(0, 8)}」？`)) return;
    try {
      await deleteMut.mutateAsync(doc.id);
      toast({ title: '已删除' });
    } catch (e) {
      toast({ title: '删除失败', description: (e as Error).message, variant: 'destructive' });
    }
  }
  async function handlePublish(doc: KbDocument) {
    try {
      await publishMut.mutateAsync({ id: doc.id, reviewer: 'operator' });
      toast({ title: '已发布', description: 'Agent 现可检索' });
    } catch (e) {
      toast({ title: '发布失败', description: (e as Error).message, variant: 'destructive' });
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">{label}：Agent 在 DRAFT/REVIEW 阶段会按相似度检索这里。</p>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild>
            <Button>
              <Plus className="mr-1 h-4 w-4" /> 新建{label}
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>新建{label}</DialogTitle>
              <DialogDescription>提交后会自动切块 + 计算 embedding。</DialogDescription>
            </DialogHeader>
            <TypeForm ktype={ktype} onSubmit={handleCreate} onCancel={() => setOpen(false)} submitting={createMut.isPending} placeholder={placeholder} />
          </DialogContent>
        </Dialog>
      </div>

      {isLoading && <LoadingBlock />}
      {error && <ErrorState error={error} onRetry={() => refetch()} />}
      {!isLoading && items.length === 0 && (
        <EmptyState title={`${label}为空`} description={`点击「新建${label}」添加第一批`} />
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
        {items.map((d) => (
          <TypeCard
            key={d.id}
            doc={d}
            onEdit={() => setEditing(d)}
            onDelete={() => handleDelete(d)}
            onPublish={() => handlePublish(d)}
            publishing={publishMut.isPending}
          />
        ))}
      </div>

      <Dialog open={!!editing} onOpenChange={(o) => !o && setEditing(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>编辑{label}</DialogTitle>
            <DialogDescription>修改内容会自动重新切块 + 重新计算 embedding。</DialogDescription>
          </DialogHeader>
          {editing && (
            <TypeForm
              ktype={ktype}
              initial={editing}
              onSubmit={handleUpdate}
              onCancel={() => setEditing(null)}
              submitting={updateMut.isPending}
              placeholder={placeholder}
            />
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

function TypeCard({ doc, onEdit, onDelete, onPublish, publishing }: {
  doc: KbDocument; onEdit: () => void; onDelete: () => void; onPublish: () => void; publishing: boolean;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between text-base">
          <span className="truncate">{doc.title || '(无标题)'}</span>
          {doc.is_published ? (
            <Badge variant="default" className="bg-emerald-500/20 text-emerald-700">
              <CheckCircle2 className="mr-1 h-3 w-3" /> 已发布
            </Badge>
          ) : (
            <Badge variant="muted">草稿</Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        <p className="line-clamp-4 whitespace-pre-wrap text-muted-foreground">{doc.content}</p>
        <div className="flex items-center justify-end gap-1 pt-1">
          <Button variant="ghost" size="sm" onClick={onEdit}>编辑</Button>
          {!doc.is_published && (
            <Button variant="ghost" size="sm" onClick={onPublish} disabled={publishing}>发布</Button>
          )}
          <Button variant="ghost" size="sm" onClick={onDelete} className="text-destructive">
            <Trash2 className="h-3 w-3" />
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function TypeForm({ ktype, initial, onSubmit, onCancel, submitting, placeholder }: {
  ktype: 'brand' | 'persona' | 'history';
  initial?: KbDocument;
  onSubmit: (body: KbDocumentCreate) => Promise<void> | void;
  onCancel: () => void;
  submitting?: boolean;
  placeholder: string;
}) {
  const [title, setTitle] = useState(initial?.title ?? '');
  const [content, setContent] = useState(initial?.content ?? '');
  const [metadata, setMetadata] = useState<string>(JSON.stringify(initial?.metadata ?? {}, null, 2));
  const [isPublished, setIsPublished] = useState<boolean>(initial?.is_published ?? false);

  async function handle() {
    let meta: Record<string, unknown> = {};
    if (metadata.trim()) {
      try { meta = JSON.parse(metadata); } catch { toast({ title: 'metadata 不是合法 JSON', variant: 'destructive' }); return; }
    }
    await onSubmit({ type: ktype, title, content, metadata: meta, is_published: isPublished });
  }

  return (
    <div className="space-y-3">
      <div>
        <Label>标题</Label>
        <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="一句话标题" />
      </div>
      <div>
        <Label>正文</Label>
        <Textarea value={content} onChange={(e) => setContent(e.target.value)} rows={8} placeholder={placeholder} />
      </div>
      <div>
        <Label>metadata（JSON，可选）</Label>
        <Textarea value={metadata} onChange={(e) => setMetadata(e.target.value)} rows={3} placeholder='{"key": "value"}' />
      </div>
      <div className="flex items-center gap-2">
        <input id="type-pub" type="checkbox" checked={isPublished} onChange={(e) => setIsPublished(e.target.checked)} />
        <Label htmlFor="type-pub">立即发布（Agent 可检索）</Label>
      </div>
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onCancel}>取消</Button>
        <Button onClick={handle} disabled={submitting || !content.trim()}>保存</Button>
      </div>
    </div>
  );
}
