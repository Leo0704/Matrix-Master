import { format, formatDistanceToNow, parseISO, isValid } from 'date-fns';
import { zhCN } from 'date-fns/locale';

export const GOAL_TYPE_LABEL: Record<string, string> = {
  publish_note: '发笔记',
  interact: '互动',
  collect_metrics: '收数据',
  warmup: '养号',
  login: '登录',
  natural_language: '自然语言',
  generic: '通用',
};

/**
 * Format an ISO date string for display.
 */
export function formatDate(iso: string | undefined, pattern = 'yyyy-MM-dd HH:mm'): string {
  if (!iso) return '-';
  try {
    const d = parseISO(iso);
    if (!isValid(d)) return iso;
    return format(d, pattern);
  } catch {
    return iso;
  }
}

/**
 * Relative time (e.g. "3 分钟前"). Chinese locale.
 */
export function formatRelative(iso: string | undefined): string {
  if (!iso) return '-';
  try {
    const d = parseISO(iso);
    if (!isValid(d)) return iso;
    return formatDistanceToNow(d, { addSuffix: true, locale: zhCN });
  } catch {
    return iso;
  }
}

const STATUS_LABELS: Record<string, string> = {
  // device
  pending: '待激活',
  active: '正常',
  offline: '离线',
  tailscale_degraded: '网络降级',
  disabled: '已禁用',
  // account
  suspended: '限流',
  banned: '封禁',
  // note
  draft: '草稿',
  reviewing: '审核中',
  scheduled: '已排期',
  publishing: '发布中',
  published: '已发布',
  failed: '失败',
  deleted: '已删除',
  // agent run
  running: '运行中',
  success: '成功',
  cancelled: '已取消',
  timeout: '超时',
  // goal
  achieved: '已完成',
  // health / subsystem
  ok: '正常',
  degraded: '降级',
  down: '宕机',
  error: '错误',
  connected: '已连接',
  disconnected: '未连接',
};

/**
 * 把状态枚举转成中文标签；未识别的返回原字符串（便于排查）。
 */
export function humanizeStatus(s: string | undefined): string {
  if (!s) return '-';
  return STATUS_LABELS[s] ?? s;
}
