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
import { ViralIngestForm } from '@/components/kb/viral-ingest-form';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/common/empty-state';
import { LoadingBlock } from '@/components/common/loading-spinner';
import { PageHeader } from '@/components/common/page-header';
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
import { useActiveBusinessId } from '@/stores/ui-store';
import type { KbDocument, KbDocumentCreate, KbType } from '@/types/api';

const KB_TYPE_LABEL: Record<KbType, string> = {
  brand: '品牌',
  persona: '人设',
  rule: '规则',
  history: '历史爆款',
  strategy_card: '套路卡',
};

export function KB() {
  // 拉全部未发布文档（limit 200 够用），顶部 banner 用
  const { data: allData } = useKbDocuments({ is_published: false, limit: 200 });
  const unpublished = allData?.items ?? [];
  const countByType: Record<string, number> = {};
  for (const d of unpublished) {
    countByType[d.type] = (countByType[d.type] ?? 0) + 1;
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="知识库"
        description="人工智能写笔记的参考材料库，按类型分 4 个标签页"
      />

      {/* 待发布 banner：中控复盘写到 KB 默认未发布，需要人工 review 后才生效 */}
      {unpublished.length > 0 && (
        <Card className="border-orange-300 bg-orange-50/50">
          <CardHeader>
            <CardTitle className="flex flex-col gap-2 text-base text-orange-700 sm:flex-row sm:items-center sm:justify-between">
              <span>
                ⏰ 有 {unpublished.length} 篇文档待审核（人工智能还看不到）
              </span>
              <span className="shrink-0 text-xs font-normal text-orange-600">
                {Object.entries(countByType)
                  .map(([t, n]) => `${KB_TYPE_LABEL[t as KbType] ?? t}: ${n}`)
                  .join(' · ')}
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent className="text-xs text-orange-600">
            中控每轮复盘自动写到知识库，<b>默认未发布</b>。点文档上的「发布」按钮即可生效。
            复盘不发布 → 下一轮拆任务时 人工智能看不到历史经验。
          </CardContent>
        </Card>
      )}

      <Tabs defaultValue="brand">
        <TabsList>
          <TabsTrigger value="brand">品牌</TabsTrigger>
          <TabsTrigger value="persona">人设</TabsTrigger>
          <TabsTrigger value="rule">规则</TabsTrigger>
          <TabsTrigger value="history">历史爆款</TabsTrigger>
          <TabsTrigger value="strategy_card">套路卡</TabsTrigger>
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
        <TabsContent value="strategy_card" className="space-y-4">
          <StrategyCardTab />
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
      toast({ title: '规则已创建', description: '尚未发布，人工智能还看不到' });
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
      toast({ title: '已发布', description: '人工智能现在能用' });
    } catch (e) {
      toast({ title: '发布失败', description: (e as Error).message, variant: 'destructive' });
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          规则库：哪些词不能写、哪些话不能说。每行前面加 <code>[禁]</code> 标记的，就是 人工智能写笔记时绝对不能用的违禁词。
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
                这些规则 人工智能写笔记时会自动参考。每行加 <code>[禁]</code> 标记的词，人工智能一写就报错。
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
            <DialogDescription>改完保存，人工智能写笔记时会用最新内容。</DialogDescription>
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
  // 解析 [禁] 行做卡片预览
  const forbiddenWords = useMemo(() => {
    const words: string[] = [];
    for (const line of (doc.content ?? '').split(/\r?\n/)) {
      const m = line.match(/^\[(禁|forbidden)\]\s*(.+)$/i);
      if (m && m[1]) words.push(m[1].trim());
    }
    return words;
  }, [doc.content]);
  const otherLines = useMemo(
    () =>
      (doc.content ?? '')
        .split(/\r?\n/)
        .filter((l) => l.trim() && !/^\[(禁|forbidden)\]\s*/i.test(l)),
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
const TYPE_HINTS: Record<'brand' | 'persona' | 'history', string> = {
  brand: '卖什么产品、给谁用、什么风格、什么价格。人工智能写笔记时会按这些写。',
  persona: '笔记用什么口吻写——亲切的、活泼的、还是专业的。人工智能写笔记时模仿这个口吻。',
  history: '之前发过的爆款笔记正文。人工智能写新笔记时模仿你的爆款套路。',
};

function TypeTab({ ktype, label }: { ktype: 'brand' | 'persona' | 'history'; label: string }) {
  const { data, isLoading, error, refetch } = useKbDocuments({ type: ktype });
  const deleteMut = useDeleteKbDocument();
  const [open, setOpen] = useState(false);
  const [pasteOpen, setPasteOpen] = useState(false);

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
        <p className="text-sm text-muted-foreground">{label}：{TYPE_HINTS[ktype]}</p>
        <div className="flex items-center gap-2">
          {ktype === 'history' && (
            <Dialog open={pasteOpen} onOpenChange={setPasteOpen}>
              <DialogTrigger asChild>
                <Button variant="outline">
                  <Plus className="mr-1 h-4 w-4" /> 粘贴爆款文案
                </Button>
              </DialogTrigger>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>粘贴爆款文案</DialogTitle>
                  <DialogDescription>
                    把别人的小红书爆款正文整段粘进来，人工智能自动拆解「为什么火」并入库。同时会生成一张待发布的「套路卡」。
                  </DialogDescription>
                </DialogHeader>
                <ViralIngestForm
                  onDone={() => setPasteOpen(false)}
                  onCancel={() => setPasteOpen(false)}
                />
              </DialogContent>
            </Dialog>
          )}
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
              <Button>
                <Plus className="mr-1 h-4 w-4" /> 上传{label}
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>上传{label}</DialogTitle>
                <DialogDescription>支持 Markdown 格式或纯文本文件。提交后自动分段理解 + 立即生效。</DialogDescription>
              </DialogHeader>
              <TypeForm ktype={ktype} onCancel={() => setOpen(false)} />
            </DialogContent>
          </Dialog>
        </div>
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
  const activeBusinessId = useActiveBusinessId();
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
      const doc = await uploadMut.mutateAsync({
        file,
        type: ktype,
        title: title || undefined,
        is_published: true,
        business_id: activeBusinessId ?? undefined,  // v0.7+ 业务归属
      });
      toast({ title: '上传成功', description: `${doc.title} 已发布，人工智能现在能用` });
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
        className={`rounded-lg border-2 border-dashed p-8 text-center transition-colors ${dragging ? 'border-primary bg-primary/5' : 'border-muted-foreground/30'}`}
      >
        {file ? (
          <div className="space-y-1">
            <p className="text-sm font-medium">{file.name}</p>
            <p className="text-xs text-muted-foreground">{(file.size / 1024).toFixed(1)} KB</p>
          </div>
        ) : (
          <div className="space-y-2">
            <p className="text-sm text-muted-foreground">把 Markdown 或纯文本文件拖到这里</p>
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


// ---------- 套路卡：人工智能从爆款提炼的可复用套路，草稿需人工发布 ----------

/** 尝试把 strategy_card 的 JSON content 解析成 lessons 列表；失败则原样返回。 */
function parseLessons(content: string): string[] {
  try {
    const obj = JSON.parse(content);
    const lessons = obj?.lessons;
    if (Array.isArray(lessons)) return lessons.map((x) => String(x));
  } catch {
    /* 老格式或非 JSON：降级为整段文本 */
  }
  return content ? [content] : [];
}

function StrategyCardTab() {
  const { data, isLoading, error, refetch } = useKbDocuments({ type: 'strategy_card' });
  const publishMut = usePublishKbDocument();
  const deleteMut = useDeleteKbDocument();

  const items = data?.items ?? [];

  async function handlePublish(doc: KbDocument) {
    try {
      await publishMut.mutateAsync({ id: doc.id, reviewer: 'operator' });
      toast({ title: '已发布', description: '人工智能写新笔记时会参考它' });
    } catch (e) {
      toast({ title: '发布失败', description: (e as Error).message, variant: 'destructive' });
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

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        套路卡：人工智能从爆款里提炼的可复用套路。粘贴爆款文案时自动生成，<b>默认草稿</b>，点「发布」后 人工智能写新笔记时才会参考。
      </p>

      {isLoading && <LoadingBlock />}
      {error && <ErrorState error={error} onRetry={() => refetch()} />}
      {!isLoading && items.length === 0 && (
        <EmptyState title="还没有套路卡" description="去「历史爆款」标签页粘贴一篇爆款文案，人工智能会自动提炼" />
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
        {items.map((d) => (
          <StrategyCardCard
            key={d.id}
            doc={d}
            onPublish={() => handlePublish(d)}
            onDelete={() => handleDelete(d)}
            publishing={publishMut.isPending}
          />
        ))}
      </div>
    </div>
  );
}

function StrategyCardCard({
  doc,
  onPublish,
  onDelete,
  publishing,
}: {
  doc: KbDocument;
  onPublish: () => void;
  onDelete: () => void;
  publishing: boolean;
}) {
  const lessons = useMemo(() => parseLessons(doc.content ?? ''), [doc.content]);

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
        <ul className="list-disc space-y-1 pl-4 text-muted-foreground">
          {lessons.slice(0, 6).map((l, i) => (
            <li key={i} className="line-clamp-2">{l}</li>
          ))}
        </ul>
        <div className="flex items-center justify-end gap-1 pt-1">
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
