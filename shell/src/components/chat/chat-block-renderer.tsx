import { useState } from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, Info } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { GOAL_TYPE_LABEL } from '@/lib/format';
import { formatState, type ChatAction } from '@/types/api';

const ASK_DATA_SUBCOMMAND_LABEL: Record<string, string> = {
  summary: '汇总',
  running: '运行中',
  weekly_top: '周榜',
};

const KB_TYPE_LABEL: Record<string, string> = {
  brand: '品牌',
  persona: '人设',
  rule: '规则',
  history: '历史爆款',
  strategy_card: '套路卡',
};

interface Props {
  action: ChatAction;
  onConfirm?: (token: string) => void;
  onCancel?: (token: string) => void;
  onNavigate?: (path: string) => void;
}

/**
 * 把 ChatAction 按 type 分支渲染成可视化块。
 *
 * 第 1 期：ask_data（表格）/ browse_kb（表格占位）/ chitchat|noop（不渲染）/ 错误类（红卡）。
 * 第 2 期：preview_change（dialog 确认弹窗 + 触发 onConfirm/onCancel）。
 * 第 3 期：diagnose（KPI diff 卡 + 归因 + KB 召回）/ browse_kb 完整列表。
 */
export function ChatBlockRenderer({ action, onConfirm, onCancel }: Props) {
  switch (action.type) {
    case 'ask_data':
      return <AskDataBlock payload={action.payload ?? {}} />;
    case 'browse_kb':
      return <BrowseKbBlock payload={action.payload ?? {}} />;
    case 'preview_change':
      return (
        <PreviewChangeBlock
          payload={action.payload ?? {}}
          token={action.confirmation_token}
          onConfirm={onConfirm}
          onCancel={onCancel}
        />
      );
    case 'apply_change':
      return <ApplyChangeBlock payload={action.payload ?? {}} />;
    case 'diagnose':
      return <DiagnoseBlock payload={action.payload ?? {}} />;
    case 'chitchat':
    case 'noop':
      return null;
    case 'partial_success':
      return <PartialSuccessBlock payload={action.payload ?? {}} />;
    // 错误类
    case 'llm_error':
    case 'parse_error':
    case 'unknown_intent':
    case 'missing_args':
    case 'batch_too_large':
      return <ErrorBlock type={action.type} payload={action.payload ?? {}} />;
    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// ask_data 表格
// ---------------------------------------------------------------------------

function AskDataBlock({ payload }: { payload: Record<string, unknown> }) {
  const subcommand = String(payload.subcommand ?? 'summary');
  const items = Array.isArray(payload.items) ? payload.items : [];
  const total = Number(payload.total ?? items.length);

  // weekly_top / single：列不同
  if (subcommand === 'weekly_top' && items.length > 0) {
    const first = items[0] as Record<string, unknown>;
    const hasRound = 'round_number' in first;
    return (
      <Card className="mt-2">
        <CardContent className="p-3">
          <p className="mb-2 text-xs text-muted-foreground">
            最近 7 天热门 {total} 篇
          </p>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>主题</TableHead>
                {hasRound && <TableHead>轮次</TableHead>}
                <TableHead className="text-right">点赞</TableHead>
                <TableHead className="text-right">浏览</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((it, idx) => {
                const row = it as Record<string, unknown>;
                return (
                  <TableRow key={String(row.goal_id ?? idx)}>
                    <TableCell className="max-w-[200px] truncate">
                      {String(row.theme ?? '')}
                    </TableCell>
                    {hasRound && (
                      <TableCell>{String(row.round_number ?? '')}</TableCell>
                    )}
                    <TableCell className="text-right font-mono">
                      {Number(row.total_likes ?? 0)}
                    </TableCell>
                    <TableCell className="text-right font-mono">
                      {Number(row.total_views ?? 0)}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    );
  }

  // summary / running：列 theme + status + phase + round
  if (items.length > 0) {
    return (
      <Card className="mt-2">
        <CardContent className="p-3">
          <p className="mb-2 text-xs text-muted-foreground">
            共 {total} 个目标（{ASK_DATA_SUBCOMMAND_LABEL[subcommand] ?? '汇总'}）
          </p>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>主题</TableHead>
                <TableHead>类型</TableHead>
                <TableHead>阶段</TableHead>
                <TableHead className="text-right">轮次</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((it, idx) => {
                const row = it as Record<string, unknown>;
                return (
                  <TableRow key={String(row.goal_id ?? idx)}>
                    <TableCell className="max-w-[200px] truncate">
                      <Link
                        to={`/goals/${String(row.goal_id)}`}
                        className="text-primary hover:underline"
                      >
                        {String(row.theme ?? '(无主题)')}
                      </Link>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {GOAL_TYPE_LABEL[String(row.type ?? '')] ?? '未知'}
                    </TableCell>
                    <TableCell>{formatState(String(row.phase ?? row.status ?? ''))}</TableCell>
                    <TableCell className="text-right font-mono">
                      {String(row.current_round ?? '?')}/
                      {String(row.max_rounds ?? '?')}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    );
  }

  // 空数据：summary/running 没 goal
  return (
    <Card className="mt-2 border-dashed">
      <CardContent className="flex items-center gap-2 p-3 text-xs text-muted-foreground">
        <Info className="h-3.5 w-3.5" />
        当前没有目标
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// （已删除 ComingSoonBlock — 第 2/3 期都完整实现）
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// preview_change：dialog 确认弹窗（第 2 期）
// ---------------------------------------------------------------------------

function PreviewChangeBlock({
  payload,
  token,
  onConfirm,
  onCancel,
}: {
  payload: Record<string, unknown>;
  token?: string;
  onConfirm?: (token: string) => void;
  onCancel?: (token: string) => void;
}) {
  const [open, setOpen] = useState(true);

  const matched = Array.isArray(payload.matched) ? payload.matched : [];
  const diffs = Array.isArray(payload.diffs) ? payload.diffs : [];
  const summary = String(payload.action_summary ?? '');

  function handleCancel() {
    if (token && onCancel) onCancel(token);
    setOpen(false);
  }

  function handleConfirm() {
    if (token && onConfirm) onConfirm(token);
    setOpen(false);
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="text-base">确认执行</DialogTitle>
          <DialogDescription>
            {summary || `将影响 ${matched.length} 个目标`}
          </DialogDescription>
        </DialogHeader>

        {matched.length > 0 && (
          <div className="max-h-60 overflow-y-auto rounded border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>主题</TableHead>
                  <TableHead>类型</TableHead>
                  <TableHead>当前状态</TableHead>
                  <TableHead className="text-right">最多轮数</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {matched.map((m, idx) => {
                  const row = m as Record<string, unknown>;
                  return (
                    <TableRow key={String(row.goal_id ?? idx)}>
                      <TableCell className="max-w-[200px] truncate">
                        {String(row.theme ?? '')}
                      </TableCell>
                      <TableCell className="text-xs">
                        {GOAL_TYPE_LABEL[String(row.type ?? '')] ?? '未知'}
                      </TableCell>
                      <TableCell>{formatState(String(row.current_status ?? ''))}</TableCell>
                      <TableCell className="text-right font-mono">
                        {String(row.current_max_rounds ?? '?')}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        )}

        {diffs.length > 0 && (
          <div className="rounded bg-muted/40 p-2 text-xs">
            <p className="mb-1 font-medium">将执行：</p>
            <ul className="space-y-0.5 pl-4">
              {diffs.slice(0, 5).map((d, idx) => {
                const row = d as Record<string, unknown>;
                return (
                  <li key={idx}>
                    <span className="font-mono">{String(row.field)}</span>:
                    {' '}
                    <span className="text-muted-foreground">
                      {String(row.from)} →{' '}
                    </span>
                    <span className="font-mono">{String(row.to)}</span>
                  </li>
                );
              })}
              {diffs.length > 5 && (
                <li className="text-muted-foreground">
                  ...还有 {diffs.length - 5} 条
                </li>
              )}
            </ul>
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={handleCancel}>
            取消
          </Button>
          <Button onClick={handleConfirm} disabled={!token || !onConfirm}>
            确认执行
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// apply_change：执行结果简报（第 2 期）
// ---------------------------------------------------------------------------

function ApplyChangeBlock({ payload }: { payload: Record<string, unknown> }) {
  const succeeded = Number(payload.total_succeeded ?? 0);
  const failed = Number(payload.total_failed ?? 0);
  const failedList = Array.isArray(payload.failed) ? payload.failed : [];

  if (failed === 0) {
    return (
      <Card className="mt-2 border-emerald-500/40 bg-emerald-50/30">
        <CardContent className="p-3 text-xs text-emerald-700">
          ✓ 已成功修改 {succeeded} 个目标
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="mt-2 border-amber-500/40 bg-amber-50/30">
      <CardContent className="p-3 text-xs">
        <p className="font-medium text-amber-700">
          部分成功：成功 {succeeded}，失败 {failed}
        </p>
        {failedList.length > 0 && (
          <pre className="mt-1 overflow-x-auto rounded bg-amber-100/40 p-2 text-[10px]">
            {JSON.stringify(failedList, null, 2)}
          </pre>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// diagnose：KPI diff + 归因 + 关联 strategy_card（第 3 期）
// ---------------------------------------------------------------------------

function DiagnoseBlock({ payload }: { payload: Record<string, unknown> }) {
  const error = payload.error;
  if (error) {
    return (
      <Card className="mt-2 border-amber-500/40 bg-amber-50/30">
        <CardContent className="p-3 text-xs text-amber-700">
          <span>{`诊断失败：${String(error)}`}</span>
          {Boolean(payload.hint) && (
            <span className="ml-2 text-muted-foreground">
              {`（${String(payload.hint)}）`}
            </span>
          )}
        </CardContent>
      </Card>
    );
  }

  const theme = String(payload.theme ?? '');
  const rounds = Array.isArray(payload.rounds) ? payload.rounds : [];
  const kpiDiff = (payload.kpi_diff ?? {}) as Record<string, unknown>;
  const llmAttr = payload.llm_attribution ? String(payload.llm_attribution) : null;
  const related = Array.isArray(payload.related_strategy_cards)
    ? payload.related_strategy_cards
    : [];

  return (
    <Card className="mt-2">
      <CardContent className="space-y-3 p-3 text-xs">
        <div>
          <p className="text-muted-foreground">诊断目标</p>
          <p className="font-medium">{theme || String(payload.goal_id ?? '')}</p>
        </div>

        {Object.keys(kpiDiff).length > 0 && (
          <div className="rounded bg-muted/40 p-2">
            <p className="mb-1 font-medium">KPI 变化</p>
            <p>{String(kpiDiff.interpretation ?? '')}</p>
            {typeof kpiDiff.likes_pct === 'number' && (
              <p className="mt-1 text-muted-foreground">
                点赞变化：
                <span
                  className={
                    Number(kpiDiff.likes_pct) >= 0
                      ? 'font-mono text-emerald-700'
                      : 'font-mono text-red-600'
                  }
                >
                  {String(kpiDiff.likes_pct)}%
                </span>
              </p>
            )}
          </div>
        )}

        {rounds.length > 0 && (
          <div>
            <p className="mb-1 font-medium">历史轮次（{rounds.length}）</p>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>轮次</TableHead>
                  <TableHead className="text-right">浏览</TableHead>
                  <TableHead className="text-right">点赞</TableHead>
                  <TableHead className="text-right">笔记</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rounds.slice(-5).map((r, idx) => {
                  const row = r as Record<string, unknown>;
                  return (
                    <TableRow key={idx}>
                      <TableCell>{`#${String(row.round_number ?? '?')}`}</TableCell>
                      <TableCell className="text-right font-mono">
                        {String(row.total_views ?? 0)}
                      </TableCell>
                      <TableCell className="text-right font-mono">
                        {String(row.total_likes ?? 0)}
                      </TableCell>
                      <TableCell className="text-right font-mono">
                        {String(row.notes_created ?? 0)}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        )}

        {llmAttr && (
          <div className="rounded border-l-2 border-primary bg-primary/5 p-2">
            <p className="mb-1 font-medium">智能归因</p>
            <p className="whitespace-pre-wrap text-muted-foreground">{llmAttr}</p>
          </div>
        )}

        {related.length > 0 && (
          <div>
            <p className="mb-1 font-medium">相关经验卡（{related.length}）</p>
            <ul className="space-y-1">
              {related.map((c, idx) => {
                const card = c as Record<string, unknown>;
                return (
                  <li key={String(card.doc_id ?? idx)} className="text-muted-foreground">
                    <span>• {String(card.title ?? '')}</span>
                    {Boolean(card.snippet) && (
                      <span className="ml-1 text-[10px]">
                        {`— ${String(card.snippet).slice(0, 80)}...`}
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// browse_kb 完整实现（第 3 期）
// ---------------------------------------------------------------------------

function BrowseKbBlock({ payload }: { payload: Record<string, unknown> }) {
  const items = Array.isArray(payload.items) ? payload.items : [];
  const total = Number(payload.total ?? items.length);
  const docType = String(payload.type ?? 'strategy_card');

  return (
    <Card className="mt-2">
      <CardContent className="p-3">
        <p className="mb-2 text-xs text-muted-foreground">
          {KB_TYPE_LABEL[docType] ?? '未知类型'} · 共 {total} 条
        </p>
        {items.length === 0 ? (
          <p className="text-xs text-muted-foreground">最近没有新增</p>
        ) : (
          <ul className="space-y-2">
            {items.slice(0, 10).map((it, idx) => {
              const row = it as Record<string, unknown>;
              return (
                <li key={String(row.doc_id ?? idx)} className="text-xs">
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex-1">
                      <p className="font-medium">
                        {String(row.title || '(无标题)')}
                        {row.is_published ? (
                          <span className="ml-2 rounded bg-emerald-100 px-1 text-[10px] text-emerald-700">
                            已发布
                          </span>
                        ) : (
                          <span className="ml-2 rounded bg-amber-100 px-1 text-[10px] text-amber-700">
                            待发布
                          </span>
                        )}
                      </p>
                      <p className="text-muted-foreground">
                        {String(row.content_preview ?? '')}
                      </p>
                      <p className="mt-0.5 text-[10px] text-muted-foreground">
                        {String(row.updated_at ?? '')}
                      </p>
                    </div>
                  </div>
                </li>
              );
            })}
            {items.length > 10 && (
              <li className="text-xs text-muted-foreground">
                ...还有 {items.length - 10} 条
              </li>
            )}
          </ul>
        )}
        <Button asChild size="sm" variant="outline" className="mt-2">
          <Link to="/kb">去知识库页查看全部</Link>
        </Button>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// partial_success：批量执行部分成功
// ---------------------------------------------------------------------------

function PartialSuccessBlock({ payload }: { payload: Record<string, unknown> }) {
  const succeeded = Array.isArray(payload.succeeded) ? payload.succeeded : [];
  const failed = Array.isArray(payload.failed) ? payload.failed : [];
  return (
    <Card className="mt-2 border-amber-500/40 bg-amber-50/30">
      <CardContent className="p-3 text-xs">
        <p className="font-medium text-amber-700">
          部分成功：成功 {succeeded.length} 个，失败 {failed.length} 个
        </p>
        {failed.length > 0 && (
          <pre className="mt-1 overflow-x-auto rounded bg-amber-100/40 p-2 text-[10px]">
            {JSON.stringify(failed, null, 2)}
          </pre>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// 错误兜底（5 类）
// ---------------------------------------------------------------------------

function ErrorBlock({
  type,
  payload,
}: {
  type: string;
  payload: Record<string, unknown>;
}) {
  const title =
    type === 'unknown_intent'
      ? '不知道你要干啥'
      : type === 'missing_args'
        ? '参数不全'
        : type === 'batch_too_large'
          ? '匹配太多'
          : type === 'parse_error'
            ? '解析失败'
            : '服务异常';
  const detail =
    type === 'unknown_intent'
      ? `未知意图：${String(payload.raw_intent ?? '?')}。试试：「现在有几个目标在跑？」「把最多轮数改成 5」`
      : type === 'missing_args'
        ? `缺字段：${(Array.isArray(payload.missing) ? payload.missing : []).join(', ')}`
        : type === 'batch_too_large'
          ? `匹配到 ${String(payload.matched ?? '?')} 个，超过单次上限 ${String(payload.limit ?? 50)}。请缩小范围`
          : '';

  return (
    <Card className="mt-2 border-destructive/40 bg-destructive/5">
      <CardContent className="flex items-start gap-2 p-3 text-xs">
        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 text-destructive" />
        <div className="space-y-1">
          <p className="font-medium text-destructive">{title}</p>
          {detail && <p className="text-muted-foreground">{detail}</p>}
        </div>
      </CardContent>
    </Card>
  );
}