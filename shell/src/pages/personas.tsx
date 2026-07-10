import { useState } from 'react';
import { Plus, Trash2, Edit } from 'lucide-react';
import {
  usePersonas,
  useCreatePersona,
  useUpdatePersona,
  useDeletePersona,
  type PersonaUpdate,
} from '@/hooks/use-personas';
import type { Persona, PersonaCreate } from '@/types/api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { LoadingBlock } from '@/components/common/loading-spinner';
import { ErrorState } from '@/components/common/error-state';
import { EmptyState } from '@/components/common/empty-state';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { toast } from '@/components/ui/use-toast';

function PersonaForm({
  initial,
  onSubmit,
  onCancel,
  submitting,
}: {
  initial?: Persona;
  onSubmit: (body: PersonaCreate | PersonaUpdate) => Promise<void>;
  onCancel?: () => void;
  submitting?: boolean;
}) {
  const [name, setName] = useState(initial?.name ?? '');
  const [tone, setTone] = useState(initial?.tone ?? '');
  const [styleGuide, setStyleGuide] = useState(initial?.style_guide ?? '');
  const [forbidden, setForbidden] = useState(
    (initial?.forbidden_words ?? []).join(', '),
  );

  async function handleSubmit() {
    const forbiddenList = forbidden
      .split(/[,，\s]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    await onSubmit({
      ...(initial ? {} : { name, tone, style_guide: styleGuide, forbidden_words: forbiddenList, sample_note_ids: [] }),
      name: name || undefined,
      tone: tone || undefined,
      style_guide: styleGuide || undefined,
      forbidden_words: forbiddenList,
    } as PersonaCreate | PersonaUpdate);
  }

  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <Label htmlFor="persona-name">名称</Label>
        <Input id="persona-name" value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="space-y-1">
        <Label htmlFor="persona-tone">语气</Label>
        <Input id="persona-tone" value={tone} onChange={(e) => setTone(e.target.value)} />
      </div>
      <div className="space-y-1">
        <Label htmlFor="persona-style">风格指南</Label>
        <Textarea
          id="persona-style"
          rows={3}
          value={styleGuide}
          onChange={(e) => setStyleGuide(e.target.value)}
        />
      </div>
      <div className="space-y-1">
        <Label htmlFor="persona-forbidden">违禁词（逗号分隔）</Label>
        <Input
          id="persona-forbidden"
          value={forbidden}
          onChange={(e) => setForbidden(e.target.value)}
          placeholder="例：最, 第一, 绝对"
        />
      </div>
      <div className="flex items-center justify-end gap-2 pt-2">
        {onCancel && (
          <Button variant="ghost" onClick={onCancel} disabled={submitting}>
            取消
          </Button>
        )}
        <Button onClick={handleSubmit} disabled={submitting || !name.trim() || !tone.trim()}>
          {submitting ? '保存中…' : initial ? '更新' : '创建'}
        </Button>
      </div>
    </div>
  );
}

export function Personas() {
  const { data, isLoading, error, refetch } = usePersonas();
  const createMut = useCreatePersona();
  const updateMut = useUpdatePersona();
  const deleteMut = useDeletePersona();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Persona | null>(null);

  const items = data?.items ?? [];

  async function handleCreate(body: PersonaCreate | PersonaUpdate) {
    try {
      await createMut.mutateAsync(body as PersonaCreate);
      toast({ title: '人设已创建' });
      setOpen(false);
    } catch (e) {
      toast({ title: '创建失败', description: (e as Error).message, variant: 'destructive' });
    }
  }

  async function handleUpdate(body: PersonaCreate | PersonaUpdate) {
    if (!editing) return;
    try {
      await updateMut.mutateAsync({ id: editing.id, body: body as PersonaUpdate });
      toast({ title: '人设已更新' });
      setEditing(null);
    } catch (e) {
      toast({ title: '更新失败', description: (e as Error).message, variant: 'destructive' });
    }
  }

  async function handleDelete(p: Persona) {
    if (!confirm(`确认删除人设「${p.name}」？`)) return;
    try {
      await deleteMut.mutateAsync(p.id);
      toast({ title: '已删除' });
    } catch (e) {
      toast({ title: '删除失败', description: (e as Error).message, variant: 'destructive' });
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">人设</h1>
          <p className="text-sm text-muted-foreground">账号人设管理（风控隔离）</p>
        </div>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild>
            <Button>
              <Plus className="mr-1 h-4 w-4" /> 新建人设
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>新建人设</DialogTitle>
              <DialogDescription>人设会被 AI 写笔记时检索用于改写文案。</DialogDescription>
            </DialogHeader>
            <PersonaForm
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
        <EmptyState title="无人设" description="点击「新建人设」添加第一个" />
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {isLoading
          ? Array.from({ length: 2 }).map((_, i) => <Skeleton key={i} className="h-40 w-full" />)
          : items.map((p) => (
              <Card key={p.id}>
                <CardHeader>
                  <CardTitle className="flex items-center justify-between text-base">
                    <span>{p.name}</span>
                    <div className="flex items-center gap-1">
                      <Badge variant="muted">v{p.version}</Badge>
                      <Button variant="ghost" size="sm" onClick={() => setEditing(p)}>
                        <Edit className="h-3 w-3" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handleDelete(p)}
                        className="text-destructive"
                      >
                        <Trash2 className="h-3 w-3" />
                      </Button>
                    </div>
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-2 text-sm">
                  <div>
                    <p className="text-xs text-muted-foreground">语气</p>
                    <p>{p.tone}</p>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground">风格指南</p>
                    <p className="whitespace-pre-wrap text-muted-foreground">{p.style_guide}</p>
                  </div>
                  {p.forbidden_words && p.forbidden_words.length > 0 && (
                    <div>
                      <p className="text-xs text-muted-foreground">违禁词</p>
                      <div className="mt-1 flex flex-wrap gap-1">
                        {p.forbidden_words.map((w) => (
                          <Badge key={w} variant="destructive" className="text-xs">
                            {w}
                          </Badge>
                        ))}
                      </div>
                    </div>
                  )}
                </CardContent>
              </Card>
            ))}
      </div>

      <Dialog open={!!editing} onOpenChange={(o) => !o && setEditing(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>编辑人设</DialogTitle>
            <DialogDescription>修改后 AI 写笔记时立即看到新版本。</DialogDescription>
          </DialogHeader>
          {editing && (
            <PersonaForm
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
