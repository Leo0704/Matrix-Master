import { useState, useEffect } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import {
  ArrowLeft,
  CheckCircle2,
  Circle,
  Loader2,
  Pencil,
  Trash2,
  XCircle,
} from 'lucide-react';
import { useGoal, useGoalRounds, useUpdateGoal, useDeleteGoal } from '@/hooks/use-goals';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { StatusBadge } from '@/components/common/status-badge';
import { ErrorState } from '@/components/common/error-state';
import { LoadingBlock } from '@/components/common/loading-spinner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useToast } from '@/components/ui/use-toast';
import { formatDate, formatRelative } from '@/lib/format';
import type { GoalPhase, GoalType } from '@/types/api';

const PHASE_LABELS: Record<GoalPhase, string> = {
  PENDING: '排队中',
  PREPARING: '拆任务',
  EXECUTING: '跑稿中',
  MONITORING: '收数据',
  SUMMARIZING: '写复盘',
  DECIDING: '决策',
  DONE: '已收工',
};

const PHASE_ORDER: GoalPhase[] = [
  'PENDING',
  'PREPARING',
  'EXECUTING',
  'MONITORING',
  'SUMMARIZING',
  'DECIDING',
  'DONE',
];

const GOAL_TYPE_LABELS: Record<GoalType, string> = {
  publish_note: '发笔记（种草 / 带货）',
  interact: '互动（评论 · 点赞 · 关注）',
  collect_metrics: '数据回采',
  warmup: '养号',
  login: '登录',
  natural_language: '自然语言目标（AI 自动解析）',
  generic: '通用',
};

function PhaseStepper({ current }: { current: GoalPhase | undefined }) {
  const cur = current ?? 'PENDING';
  const curIdx = PHASE_ORDER.indexOf(cur);
  return (
    <div className="flex items-center gap-1 overflow-x-auto py-2">
      {PHASE_ORDER.map((p, i) => {
        const isDone = i < curIdx || cur === 'DONE';
        const isCurrent = i === curIdx && cur !== 'DONE';
        return (
          <div key={p} className="flex items-center gap-1">
            <div
              className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${
                isDone
                  ? 'bg-emerald-100 text-emerald-700'
                  : isCurrent
                    ? 'bg-blue-100 text-blue-700 ring-2 ring-blue-400'
                    : 'bg-muted text-muted-foreground'
              }`}
            >
              {isDone ? (
                <CheckCircle2 className="h-3 w-3" />
              ) : isCurrent ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Circle className="h-3 w-3" />
              )}
              {PHASE_LABELS[p]}
            </div>
            {i < PHASE_ORDER.length - 1 && (
              <div
                className={`h-0.5 w-3 ${
                  isDone ? 'bg-emerald-300' : 'bg-muted'
                }`}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

export function GoalDetail() {
  const { id } = useParams<{ id: string }>();
  const { data, isLoading, error, refetch } = useGoal(id);
  const { data: roundsData } = useGoalRounds(id);
  const updateGoal = useUpdateGoal();
  const deleteGoal = useDeleteGoal();
  const navigate = useNavigate();
  const { toast } = useToast();
  const [editOpen, setEditOpen] = useState(false);
  const [stopOpen, setStopOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [editTheme, setEditTheme] = useState('');
  const [editAudience, setEditAudience] = useState('');
  const [editCategory, setEditCategory] = useState('');
  // 保留 target 里用户看不见的字段（persona_id / goal_type / 其它），保存时原样带回，避免整体覆盖丢数据
  const [editTargetBase, setEditTargetBase] = useState<Record<string, unknown>>({});
  const [editDeadline, setEditDeadline] = useState('');
  const [editTargetLikes, setEditTargetLikes] = useState('');
  const [editNotesPerRound, setEditNotesPerRound] = useState('');
  const [editMaxRounds, setEditMaxRounds] = useState('');
  const [editType, setEditType] = useState('');

  // 进入编辑态时用当前值填充（避免点编辑后空白）
  useEffect(() => {
    if (!editOpen || !data) return;
    const target = (data.target ?? {}) as Record<string, unknown>;
    setEditTargetBase(target);
    setEditTheme(String(target.theme ?? ''));
    setEditAudience(String(target.audience ?? ''));
    setEditCategory(String(target.product_category ?? ''));
    setEditDeadline(data.deadline ? data.deadline.slice(0, 16) : '');
    setEditTargetLikes(String(data.target_likes ?? 500));
    setEditNotesPerRound(String(data.notes_per_round ?? 3));
    setEditMaxRounds(String(data.max_rounds ?? 3));
    setEditType(data.type);
  }, [editOpen, data]);

  async function handleSave() {
    if (!data) return;
    if (!editTheme.trim()) {
      toast({ title: '请填写主题', variant: 'destructive' });
      return;
    }
    // 在原 target 基础上覆盖用户改过的三项，其它隐藏字段原样保留
    const nextTarget: Record<string, unknown> = {
      ...editTargetBase,
      theme: editTheme.trim(),
      audience: editAudience.trim(),
      product_category: editCategory.trim(),
    };
    try {
      await updateGoal.mutateAsync({
        id: data.id,
        body: {
          type: (editType || undefined) as GoalType | undefined,
          target: nextTarget,
          ...(editDeadline ? { deadline: new Date(editDeadline).toISOString() } : {}),
          target_likes: Number(editTargetLikes) || 500,
          notes_per_round: Number(editNotesPerRound) || 3,
          max_rounds: Number(editMaxRounds) || 3,
        },
      });
      toast({ title: '已保存', description: '改动会在下一轮运营时生效' });
      setEditOpen(false);
    } catch (err) {
      toast({ title: '保存失败', description: String(err), variant: 'destructive' });
    }
  }

  async function handleStop() {
    if (!data) return;
    try {
      await updateGoal.mutateAsync({
        id: data.id,
        body: { status: 'cancelled' },
      });
      toast({
        title: '目标已停止',
        description: 'KPI 和复盘数据会保留',
      });
      setStopOpen(false);
    } catch (err) {
      toast({ title: '停止失败', description: String(err), variant: 'destructive' });
    }
  }

  async function handleDelete() {
    if (!data) return;
    try {
      await deleteGoal.mutateAsync(data.id);
      toast({ title: '目标已删除' });
      navigate('/goals');
    } catch (err) {
      toast({ title: '删除失败', description: String(err), variant: 'destructive' });
    }
  }

  if (isLoading) return <LoadingBlock />;
  if (error) return <ErrorState error={error} onRetry={() => refetch()} />;
  if (!data) return null;

  const rounds = roundsData?.items ?? [];
  const currentRound = rounds.find((r) => !r.ended_at);

  return (
    <div className="space-y-4">
      <Button variant="ghost" size="sm" asChild className="-ml-2">
        <Link to="/goals">
          <ArrowLeft className="mr-1 h-4 w-4" />
          返回目标列表
        </Link>
      </Button>

      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">
            {(data.target as { theme?: string })?.theme ?? data.type}
          </h1>
          <p className="text-sm text-muted-foreground">ID: {data.id}</p>
        </div>
        <div className="flex items-center gap-2">
          {data.status === 'active' && (
            <Button
              variant="destructive"
              size="sm"
              onClick={() => setStopOpen(true)}
            >
              <XCircle className="mr-1 h-3 w-3" />
              停止
            </Button>
          )}
          <Button
            variant="ghost"
            size="sm"
            className="text-destructive hover:bg-destructive/10"
            onClick={() => setDeleteOpen(true)}
          >
            <Trash2 className="mr-1 h-3 w-3" />
            删除
          </Button>
          <Button variant="outline" size="sm" onClick={() => setEditOpen(true)}>
            <Pencil className="mr-1 h-3 w-3" />
            编辑
          </Button>
          <StatusBadge status={data.status} />
          {data.phase && (
            <span className="rounded bg-muted px-2 py-0.5 text-xs">
              第 {data.current_round ?? 1} / {data.max_rounds ?? 3} 轮
            </span>
          )}
        </div>
      </div>

      {/* Phase 进度 */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">运营进度</CardTitle>
        </CardHeader>
        <CardContent>
          <PhaseStepper current={data.phase} />
          {data.phase_updated_at && (
            <p className="mt-2 text-xs text-muted-foreground">
              上次更新：{formatRelative(data.phase_updated_at)}
            </p>
          )}
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {/* 目标参数 */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">目标参数</CardTitle>
          </CardHeader>
          <CardContent>
            {data.target && typeof data.target === 'object' &&
            ('theme' in data.target || 'audience' in data.target) ? (
              <div className="space-y-2 text-sm">
                {(data.target as { theme?: string }).theme && (
                  <div className="flex items-start gap-2">
                    <span className="w-16 shrink-0 text-muted-foreground">主题</span>
                    <span>{(data.target as { theme?: string }).theme}</span>
                  </div>
                )}
                {(data.target as { audience?: string }).audience && (
                  <div className="flex items-start gap-2">
                    <span className="w-16 shrink-0 text-muted-foreground">人群</span>
                    <span>{(data.target as { audience?: string }).audience}</span>
                  </div>
                )}
                {(data.target as { product_category?: string }).product_category && (
                  <div className="flex items-start gap-2">
                    <span className="w-16 shrink-0 text-muted-foreground">类目</span>
                    <span>{(data.target as { product_category?: string }).product_category}</span>
                  </div>
                )}
                {(data.target as { angle_reason?: string }).angle_reason && (
                  <div className="flex items-start gap-2">
                    <span className="w-16 shrink-0 text-muted-foreground">角度</span>
                    <span>{(data.target as { angle_reason?: string }).angle_reason}</span>
                  </div>
                )}
              </div>
            ) : (
              <pre className="overflow-x-auto rounded bg-muted p-3 text-xs">
                {JSON.stringify(data.target, null, 2)}
              </pre>
            )}
          </CardContent>
        </Card>

        {/* 时间 + 状态 */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">时间</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">截止</span>
              <span>{formatDate(data.deadline)}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">状态</span>
              <StatusBadge status={data.status} />
            </div>
          </CardContent>
        </Card>
      </div>

      {/* 复盘 */}
      {data.learning_summary && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">运营复盘</CardTitle>
          </CardHeader>
          <CardContent>
            <pre className="whitespace-pre-wrap text-sm text-foreground">
              {data.learning_summary}
            </pre>
          </CardContent>
        </Card>
      )}

      {/* 编辑目标 dialog */}
      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>编辑目标</DialogTitle>
            <DialogDescription>
              改完点保存，改动会在下一轮运营时生效。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <Label htmlFor="edit-theme">主题</Label>
              <Input
                id="edit-theme"
                value={editTheme}
                onChange={(e) => setEditTheme(e.target.value)}
                placeholder="例：夏季女生穿搭种草"
              />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <Label htmlFor="edit-audience">面向人群</Label>
                <Input
                  id="edit-audience"
                  value={editAudience}
                  onChange={(e) => setEditAudience(e.target.value)}
                  placeholder="例：20-30 岁女生"
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="edit-category">商品类目（选填）</Label>
                <Input
                  id="edit-category"
                  value={editCategory}
                  onChange={(e) => setEditCategory(e.target.value)}
                  placeholder="例：连衣裙"
                />
              </div>
            </div>
            <div className="space-y-1">
              <Label htmlFor="deadline">截止时间（选填）</Label>
              <Input
                id="deadline"
                type="datetime-local"
                value={editDeadline}
                onChange={(e) => setEditDeadline(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="edit-type">目标类型</Label>
              <select
                id="edit-type"
                value={editType}
                onChange={(e) => setEditType(e.target.value)}
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {(Object.keys(GOAL_TYPE_LABELS) as GoalType[]).map((t) => (
                  <option key={t} value={t}>
                    {GOAL_TYPE_LABELS[t]}
                  </option>
                ))}
              </select>
            </div>
            <div className="grid grid-cols-3 gap-2">
              <div className="space-y-1">
                <Label htmlFor="target_likes">收工赞数</Label>
                <Input
                  id="target_likes"
                  type="number"
                  min={1}
                  value={editTargetLikes}
                  onChange={(e) => setEditTargetLikes(e.target.value)}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="notes_per_round">每轮篇数</Label>
                <Input
                  id="notes_per_round"
                  type="number"
                  min={1}
                  max={20}
                  value={editNotesPerRound}
                  onChange={(e) => setEditNotesPerRound(e.target.value)}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="max_rounds">最多轮数</Label>
                <Input
                  id="max_rounds"
                  type="number"
                  min={1}
                  max={20}
                  value={editMaxRounds}
                  onChange={(e) => setEditMaxRounds(e.target.value)}
                />
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditOpen(false)} disabled={updateGoal.isPending}>
              取消
            </Button>
            <Button onClick={handleSave} disabled={updateGoal.isPending}>
              {updateGoal.isPending && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 停止目标 dialog（二次确认） */}
      <Dialog open={stopOpen} onOpenChange={setStopOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>停止这个目标？</DialogTitle>
            <DialogDescription>
              停止后不再自动跑新的一轮。已经跑过的轮次、数据和复盘记录都会保留。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setStopOpen(false)}>
              不停
            </Button>
            <Button
              variant="destructive"
              onClick={handleStop}
              disabled={updateGoal.isPending}
            >
              {updateGoal.isPending && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
              确认停止
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 删除目标 dialog（二次确认，硬删） */}
      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认要删？</DialogTitle>
            <DialogDescription>
              删掉这个目标和它的运营记录。<b>已经发出去的笔记不受影响</b>（小红书上照常能看，复盘资料也会保留）。
              <br />
              <br />
              <b className="text-destructive">删除后无法恢复。</b>
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteOpen(false)}>
              取消
            </Button>
            <Button
              variant="destructive"
              onClick={handleDelete}
              disabled={deleteGoal.isPending}
            >
              {deleteGoal.isPending && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
              确认删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 轮次 + KPI */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">每轮 KPI</CardTitle>
        </CardHeader>
        <CardContent>
          {rounds.length === 0 ? (
            <p className="text-sm text-muted-foreground">还没开始运营</p>
          ) : (
            <div className="space-y-3">
              {rounds.map((r) => {
                const isCurrent = !r.ended_at;
                return (
                  <div
                    key={r.id}
                    className={`rounded-md border p-3 ${
                      isCurrent ? 'border-blue-300 bg-blue-50/50' : ''
                    }`}
                  >
                    <div className="mb-2 flex items-center justify-between">
                      <div className="font-medium">
                        第 {r.round_number} 轮
                        {isCurrent && (
                          <span className="ml-2 text-xs text-blue-600">
                            （进行中）
                          </span>
                        )}
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {formatRelative(r.started_at)}
                        {r.ended_at && ` → ${formatRelative(r.ended_at)}`}
                      </div>
                    </div>
                    <div className="grid grid-cols-4 gap-2 text-sm">
                      <Stat label="笔记" value={r.notes_created} />
                      <Stat label="浏览" value={r.total_views} />
                      <Stat label="点赞" value={r.total_likes} />
                      <Stat
                        label="收藏"
                        value={
                          (r.kpi_summary.total_collects as number) ?? 0
                        }
                      />
                    </div>
                  </div>
                );
              })}
              {currentRound && (
                <p className="text-xs text-muted-foreground">
                  自动刷新中（每 10 秒）
                </p>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="font-mono text-base">{value}</div>
    </div>
  );
}
