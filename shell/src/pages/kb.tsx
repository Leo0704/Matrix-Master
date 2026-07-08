import { useState } from 'react';
import { Plus, Trash2, CheckCircle2 } from 'lucide-react';
import { usePersonas } from '@/hooks/use-personas';
import {
  useKbDocuments,
  useCreateKbDocument,
  useUpdateKbDocument,
  useDeleteKbDocument,
  usePublishKbDocument,
} from '@/hooks/use-kb';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
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
import { ProductForm } from '@/components/kb/product-form';
import { toast } from '@/components/ui/use-toast';
import type { KbDocument, KbDocumentCreate } from '@/types/api';

export function KB() {
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">知识库</h1>
        <p className="text-sm text-muted-foreground">
          persona / rule / topic / product 管理
        </p>
      </div>

      <Tabs defaultValue="product">
        <TabsList>
          <TabsTrigger value="product">商品库</TabsTrigger>
          <TabsTrigger value="persona">Persona</TabsTrigger>
          <TabsTrigger value="rule">Rule</TabsTrigger>
          <TabsTrigger value="topic">Topic</TabsTrigger>
          <TabsTrigger value="template">Template</TabsTrigger>
        </TabsList>

        <TabsContent value="product" className="space-y-4">
          <ProductTab />
        </TabsContent>

        <TabsContent value="persona" className="space-y-4">
          <PersonaTab />
        </TabsContent>

        <TabsContent value="rule">
          <EmptyState title="规则管理" description="规则 + 判据是 Agent 自动执行的前提（待接入）" />
        </TabsContent>
        <TabsContent value="topic">
          <EmptyState title="选题库" description="爆款选题 + 季节性选题（待接入）" />
        </TabsContent>
        <TabsContent value="template">
          <EmptyState title="文案模板" description="标题 / 收尾模板（待接入）" />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function PersonaTab() {
  const { data, isLoading, error, refetch } = usePersonas();
  return (
    <>
      {isLoading && <LoadingBlock />}
      {error && <ErrorState error={error} onRetry={() => refetch()} />}
      {(data?.items.length ?? 0) === 0 && !isLoading && (
        <EmptyState title="无人设" description="新建 persona 供 Agent 改写文案" />
      )}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {data?.items.map((p) => (
          <Card key={p.id}>
            <CardHeader>
              <CardTitle className="flex items-center justify-between text-base">
                {p.name}
                <Badge variant="muted">v{p.version}</Badge>
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <p className="text-muted-foreground">{p.style_guide}</p>
            </CardContent>
          </Card>
        ))}
      </div>
    </>
  );
}

function ProductTab() {
  const { data, isLoading, error, refetch } = useKbDocuments({ type: 'product' });
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
      toast({ title: '商品已创建', description: '尚未发布，Agent 不可见' });
      setOpen(false);
    } catch (e) {
      toast({ title: '创建失败', description: (e as Error).message, variant: 'destructive' });
    }
  }

  async function handleUpdate(body: KbDocumentCreate) {
    if (!editing) return;
    try {
      await updateMut.mutateAsync({ id: editing.id, body });
      toast({ title: '商品已更新' });
      setEditing(null);
    } catch (e) {
      toast({ title: '更新失败', description: (e as Error).message, variant: 'destructive' });
    }
  }

  async function handleDelete(doc: KbDocument) {
    if (!confirm(`确认删除商品「${doc.title || doc.id.slice(0, 8)}」？`)) return;
    try {
      await deleteMut.mutateAsync(doc.id);
      toast({ title: '商品已删除' });
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
          商品事实库：DRAFT 写稿时会按主题检索这里的具体商品（款式/尺码/价格/卖点）。
        </p>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild>
            <Button>
              <Plus className="mr-1 h-4 w-4" /> 新建商品
            </Button>
          </DialogTrigger>
          <DialogContent className="max-w-xl">
            <DialogHeader>
              <DialogTitle>新建商品</DialogTitle>
              <DialogDescription>
                商品事实将作为 DRAFT 节点写稿时的事实依据。未发布的商品 Agent 不可见。
              </DialogDescription>
            </DialogHeader>
            <ProductForm
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
        <EmptyState title="商品库为空" description="点击「新建商品」添加第一批" />
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
        {items.map((d) => (
          <Card key={d.id}>
            <CardHeader>
              <CardTitle className="flex items-center justify-between text-base">
                <span className="truncate">{d.title || '(无标题)'}</span>
                {d.is_published ? (
                  <Badge variant="default" className="bg-emerald-500/20 text-emerald-700">
                    <CheckCircle2 className="mr-1 h-3 w-3" /> 已发布
                  </Badge>
                ) : (
                  <Badge variant="muted">草稿</Badge>
                )}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <p className="line-clamp-3 text-muted-foreground">{d.content}</p>
              <div className="flex flex-wrap gap-1 text-xs">
                {d.metadata?.price != null && (
                  <Badge variant="muted">¥{String(d.metadata.price)}</Badge>
                )}
                {Array.isArray(d.metadata?.sizes) &&
                  (d.metadata.sizes as string[]).map((s) => (
                    <Badge key={s} variant="muted">
                      {s}
                    </Badge>
                  ))}
                {d.metadata?.style != null && (
                  <Badge variant="muted">{String(d.metadata.style)}</Badge>
                )}
                {d.metadata?.category != null && (
                  <Badge variant="muted">{String(d.metadata.category)}</Badge>
                )}
              </div>
              <div className="flex items-center justify-end gap-1 pt-1">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setEditing(d)}
                >
                  编辑
                </Button>
                {!d.is_published && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => handlePublish(d)}
                    disabled={publishMut.isPending}
                  >
                    发布
                  </Button>
                )}
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => handleDelete(d)}
                  className="text-destructive"
                >
                  <Trash2 className="h-3 w-3" />
                </Button>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <Dialog open={!!editing} onOpenChange={(o) => !o && setEditing(null)}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>编辑商品</DialogTitle>
            <DialogDescription>修改内容会自动重新切块 + 重新计算 embedding。</DialogDescription>
          </DialogHeader>
          {editing && (
            <ProductForm
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
