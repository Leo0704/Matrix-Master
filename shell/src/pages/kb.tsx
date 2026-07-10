import { useMemo, useState } from 'react';
import { Plus, Trash2, CheckCircle2 } from 'lucide-react';
import {
  useKbDocuments,
  useCreateKbDocument,
  useUpdateKbDocument,
  useDeleteKbDocument,
  usePublishKbDocument,
  useUploadKbDocument,
} from '@/hooks/use-kb';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { RuleForm } from '@/components/kb/rule-form';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
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
          <TypeTab ktype="brand" label="品牌资料" />
        </TabsContent>
        <TabsContent value="persona" className="space-y-4">
          <TypeTab ktype="persona" label="人设语气" />
        </TabsContent>
        <TabsContent value="rule" className="space-y-4">
          <RuleTab />
        </TabsContent>
        <TabsContent value="history" className="space-y-4">
          <TypeTab ktype="history" label="历史爆款" />
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
function TypeTab({ ktype, label }: { ktype: 'brand' | 'persona' | 'history'; label: string }) {
  const { data, isLoading, error, refetch } = useKbDocuments({ type: ktype });
  const deleteMut = useDeleteKbDocument();
  const [open, setOpen] = useState(false);

  const items = data?.items ?? [];

  async function handleDelete(doc: KbDocument) {
    if (!confirm(`确认删除「${doc.title || doc.id.slice(0, 8)}」？`)) return;
    try {
      await deleteMut.mutateAsync(doc.id);
      toast({ title: '已删除' });
    } catch (e) {
      toast({ title: '删除失败', description: (e as Error).message, variant: 'destructive' });
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">{label}：拖文件上传，Agent 在 DRAFT/REVIEW 阶段按相似度检索。</p>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild>
            <Button>
              <Plus className="mr-1 h-4 w-4" /> 上传{label}
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>上传{label}</DialogTitle>
              <DialogDescription>支持 .md / .txt 文件。提交后自动切块 + 计算 embedding + 立即发布。</DialogDescription>
            </DialogHeader>
            <TypeForm ktype={ktype} onCancel={() => setOpen(false)} />
          </DialogContent>
        </Dialog>
      </div>

      {isLoading && <LoadingBlock />}
      {error && <ErrorState error={error} onRetry={() => refetch()} />}
      {!isLoading && items.length === 0 && (
        <EmptyState title={`${label}为空`} description={`点击「上传${label}」拖入文件`} />
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
        {items.map((d) => (
          <TypeCard
            key={d.id}
            doc={d}
            onDelete={() => handleDelete(d)}
          />
        ))}
      </div>
    </div>
  );
}

function TypeCard({ doc, onDelete }: { doc: KbDocument; onDelete: () => void }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between text-base">
          <span className="truncate">{doc.title || '(无标题)'}</span>
          <Badge variant="default" className="bg-emerald-500/20 text-emerald-700">
            <CheckCircle2 className="mr-1 h-3 w-3" /> 已发布
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        <p className="line-clamp-4 whitespace-pre-wrap text-muted-foreground">{doc.content}</p>
        <div className="flex items-center justify-end gap-1 pt-1">
          <Button variant="ghost" size="sm" onClick={onDelete} className="text-destructive">
            <Trash2 className="h-3 w-3" />
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function TypeForm({ ktype, onCancel }: {
  ktype: 'brand' | 'persona' | 'history';
  onCancel: () => void;
}) {
  const uploadMut = useUploadKbDocument();
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState<string>('');
  const [dragging, setDragging] = useState(false);

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragging(false);
    const f = e.dataTransfer.files?.[0];
    if (f) { setFile(f); if (!title) setTitle(f.name.replace(/\.[^.]+$/, '')); }
  }
  function onPick(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (f) { setFile(f); if (!title) setTitle(f.name.replace(/\.[^.]+$/, '')); }
  }

  async function handleUpload() {
    if (!file) return;
    try {
      const doc = await uploadMut.mutateAsync({ file, type: ktype, title: title || undefined, is_published: true });
      toast({ title: '上传成功', description: `${doc.title} 已发布，Agent 可检索` });
      setFile(null); setTitle(''); onCancel();
    } catch (e) {
      const msg = (e as Error)?.message || '上传失败';
      toast({ title: '上传失败', description: msg, variant: 'destructive' });
    }
  }

  return (
    <div className="space-y-3">
      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={\`rounded-lg border-2 border-dashed p-8 text-center transition-colors \${dragging ? 'border-primary bg-primary/5' : 'border-muted-foreground/30'}\`}
      >
        {file ? (
          <div className="space-y-1">
            <p className="text-sm font-medium">{file.name}</p>
            <p className="text-xs text-muted-foreground">{(file.size / 1024).toFixed(1)} KB</p>
          </div>
        ) : (
          <div className="space-y-2">
            <p className="text-sm text-muted-foreground">把 .md 或 .txt 文件拖到这里</p>
            <p className="text-xs text-muted-foreground">或者</p>
            <label className="inline-block cursor-pointer rounded-md border border-input bg-background px-3 py-1.5 text-sm hover:bg-accent">
              选择文件
              <input type="file" accept=".md,.txt" className="hidden" onChange={onPick} />
            </label>
          </div>
        )}
      </div>
      <div>
        <Label>标题（留空用文件名）</Label>
        <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="一句话标题" />
      </div>
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onCancel}>取消</Button>
        <Button onClick={handleUpload} disabled={!file || uploadMut.isPending}>
          {uploadMut.isPending ? '上传中...' : '上传'}
        </Button>
      </div>
    </div>
  );
}
