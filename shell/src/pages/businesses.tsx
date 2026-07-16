/**
 * /businesses — 业务管理页（v0.7+ 业务模型重构）
 *
 * 功能：
 * - 表格：name / slug / status / created_at / actions
 * - 创建对话框（name / slug / description）
 * - 编辑对话框（name / slug / description，不暴露 status）
 * - Archive / Unarchive 确认弹窗
 * - 切业务：在列表里点击"设为当前"按钮 → 写入 ui-store
 */
import { useState } from 'react';
import { PageHeader } from '@/components/common/page-header';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Skeleton } from '@/components/ui/skeleton';
import { ErrorState } from '@/components/common/error-state';
import { EmptyState } from '@/components/common/empty-state';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  useBusinesses,
  useCreateBusiness,
  useUpdateBusiness,
  useArchiveBusiness,
  useUnarchiveBusiness,
} from '@/hooks/use-businesses';
import { useActiveBusinessId, useSetActiveBusinessId } from '@/stores/ui-store';
import { toast } from '@/components/ui/use-toast';
import { Plus, Pencil, Archive, ArchiveRestore, Check } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { Business } from '@/types/api';

export function Businesses() {
  const { data, isLoading, error, refetch } = useBusinesses();
  const activeBusinessId = useActiveBusinessId();
  const setActiveBusinessId = useSetActiveBusinessId();
  const createBiz = useCreateBusiness();
  const updateBiz = useUpdateBusiness();
  const archiveBiz = useArchiveBusiness();
  const unarchiveBiz = useUnarchiveBusiness();

  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<Business | null>(null);
  const [archiving, setArchiving] = useState<Business | null>(null);

  const items = data?.items ?? [];

  function handleSetActive(b: Business) {
    if (b.status === 'archived') {
      toast({
        title: '归档业务不能设为当前',
        description: '请先恢复（unarchive）业务',
        variant: 'destructive',
      });
      return;
    }
    setActiveBusinessId(b.id);
    toast({
      title: '已切换业务',
      description: `${b.name}（${b.slug}）`,
    });
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="业务管理"
        description={`共 ${items.length} 个业务（active ${items.filter((b) => b.status === 'active').length} 个）`}
        actions={
          <Button onClick={() => setCreateOpen(true)}>
            <Plus className="mr-1 h-4 w-4" /> 新建业务
          </Button>
        }
      />

      {error && <ErrorState error={error} onRetry={refetch} />}

      {isLoading ? (
        <Skeleton className="h-48" />
      ) : items.length === 0 ? (
        <EmptyState
          title="还没有业务"
          description="点右上角「新建业务」开始。每个账号 / 设备 / 笔记 / 目标都要挂在某个业务名下。"
        />
      ) : (
        <div className="rounded-md border">
          <table className="w-full text-sm">
            <thead className="border-b bg-muted/40">
              <tr className="text-left">
                <th className="px-3 py-2 font-medium">名称</th>
                <th className="px-3 py-2 font-medium">slug</th>
                <th className="px-3 py-2 font-medium">状态</th>
                <th className="px-3 py-2 font-medium">创建时间</th>
                <th className="px-3 py-2 font-medium text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              {items.map((b) => {
                const isActive = b.id === activeBusinessId;
                return (
                  <tr
                    key={b.id}
                    className={cn(
                      'border-b last:border-b-0',
                      isActive && 'bg-accent/30',
                    )}
                  >
                    <td className="px-3 py-2 font-medium">{b.name}</td>
                    <td className="px-3 py-2 text-muted-foreground">{b.slug}</td>
                    <td className="px-3 py-2">
                      <span
                        className={cn(
                          'inline-block rounded px-2 py-0.5 text-xs',
                          b.status === 'active'
                            ? 'bg-green-100 text-green-700'
                            : 'bg-gray-100 text-gray-600',
                        )}
                      >
                        {b.status === 'active' ? '活跃' : '已归档'}
                      </span>
                      {isActive && (
                        <span className="ml-2 inline-block rounded bg-primary px-2 py-0.5 text-xs text-primary-foreground">
                          当前
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-muted-foreground">
                      {new Date(b.created_at).toLocaleString()}
                    </td>
                    <td className="px-3 py-2 text-right space-x-1">
                      {!isActive && b.status === 'active' && (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => handleSetActive(b)}
                        >
                          <Check className="mr-1 h-3 w-3" /> 设为当前
                        </Button>
                      )}
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => setEditing(b)}
                      >
                        <Pencil className="h-3 w-3" />
                      </Button>
                      {b.status === 'active' ? (
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => setArchiving(b)}
                          title="归档（软删，下面的资源只读不可新建）"
                        >
                          <Archive className="h-3 w-3" />
                        </Button>
                      ) : (
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => unarchiveBiz.mutate(b.id)}
                          title="恢复"
                        >
                          <ArchiveRestore className="h-3 w-3" />
                        </Button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* 新建业务对话框 */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>新建业务</DialogTitle>
          </DialogHeader>
          <CreateForm
            onSubmit={async (body) => {
              try {
                const biz = await createBiz.mutateAsync(body);
                toast({ title: `已创建业务：${biz.name}` });
                setCreateOpen(false);
                // 自动设为当前
                setActiveBusinessId(biz.id);
              } catch (e) {
                toast({
                  title: '创建失败',
                  description: (e as Error).message,
                  variant: 'destructive',
                });
              }
            }}
            submitting={createBiz.isPending}
          />
        </DialogContent>
      </Dialog>

      {/* 编辑业务对话框 */}
      <Dialog open={!!editing} onOpenChange={(o) => !o && setEditing(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>编辑业务</DialogTitle>
          </DialogHeader>
          {editing && (
            <EditForm
              initial={editing}
              onSubmit={async (body) => {
                try {
                  await updateBiz.mutateAsync({ id: editing.id, body });
                  toast({ title: '已保存' });
                  setEditing(null);
                } catch (e) {
                  toast({
                    title: '保存失败',
                    description: (e as Error).message,
                    variant: 'destructive',
                  });
                }
              }}
              submitting={updateBiz.isPending}
            />
          )}
        </DialogContent>
      </Dialog>

      {/* 归档确认 */}
      <Dialog
        open={!!archiving}
        onOpenChange={(o) => !o && setArchiving(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>归档业务「{archiving?.name}」？</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            归档后这个业务下的资源不能再新建（goal / account / device / kb 文档
            等），但历史数据保留只读可查。要恢复请点「恢复」按钮。
          </p>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setArchiving(null)}>
              取消
            </Button>
            <Button
              variant="destructive"
              onClick={async () => {
                if (!archiving) return;
                try {
                  await archiveBiz.mutateAsync(archiving.id);
                  toast({ title: '已归档' });
                  setArchiving(null);
                } catch (e) {
                  toast({
                    title: '归档失败',
                    description: (e as Error).message,
                    variant: 'destructive',
                  });
                }
              }}
            >
              归档
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 内部表单组件
// ---------------------------------------------------------------------------

function CreateForm({
  onSubmit,
  submitting,
}: {
  onSubmit: (body: { name: string; slug: string; description?: string }) => Promise<void>;
  submitting: boolean;
}) {
  const [name, setName] = useState('');
  const [slug, setSlug] = useState('');
  const [description, setDescription] = useState('');

  // name → slug 自动生成（用户可改）
  function handleNameChange(v: string) {
    setName(v);
    if (!slug || slug === slugify(name)) {
      setSlug(slugify(v));
    }
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (!name.trim() || !slug.trim()) return;
        onSubmit({
          name: name.trim(),
          slug: slug.trim(),
          description: description.trim() || undefined,
        });
      }}
      className="space-y-3"
    >
      <div className="space-y-1">
        <label className="text-sm font-medium">名称</label>
        <Input value={name} onChange={(e) => handleNameChange(e.target.value)} placeholder="例：平价学生党女鞋" />
      </div>
      <div className="space-y-1">
        <label className="text-sm font-medium">slug（路由前缀，英文+数字+短横线）</label>
        <Input
          value={slug}
          onChange={(e) => setSlug(e.target.value)}
          placeholder="例：budget-school-shoes"
        />
      </div>
      <div className="space-y-1">
        <label className="text-sm font-medium">描述（可选）</label>
        <Textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={2}
          placeholder="例：面向大学生的平价女鞋带货"
        />
      </div>
      <DialogFooter>
        <Button type="submit" disabled={submitting || !name.trim() || !slug.trim()}>
          {submitting ? '创建中…' : '创建'}
        </Button>
      </DialogFooter>
    </form>
  );
}

function EditForm({
  initial,
  onSubmit,
  submitting,
}: {
  initial: Business;
  onSubmit: (body: { name?: string; slug?: string; description?: string }) => Promise<void>;
  submitting: boolean;
}) {
  const [name, setName] = useState(initial.name);
  const [slug, setSlug] = useState(initial.slug);
  const [description, setDescription] = useState(initial.description ?? '');

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit({
          name: name.trim() || undefined,
          slug: slug.trim() || undefined,
          description: description.trim(),
        });
      }}
      className="space-y-3"
    >
      <div className="space-y-1">
        <label className="text-sm font-medium">名称</label>
        <Input value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="space-y-1">
        <label className="text-sm font-medium">slug</label>
        <Input value={slug} onChange={(e) => setSlug(e.target.value)} />
      </div>
      <div className="space-y-1">
        <label className="text-sm font-medium">描述</label>
        <Textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={2}
        />
      </div>
      <DialogFooter>
        <Button type="submit" disabled={submitting}>
          {submitting ? '保存中…' : '保存'}
        </Button>
      </DialogFooter>
    </form>
  );
}

/** 简单 slug 化：中英文 → ASCII + 短横线，全小写。 */
function slugify(s: string): string {
  return s
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9一-鿿]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 64);
}