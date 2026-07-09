import { format, formatDistanceToNow, parseISO, isValid } from 'date-fns';
import { zhCN } from 'date-fns/locale';

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

/**
 * Format seconds as e.g. "1h 23m" / "12m 34s".
 */
export function formatDuration(sec: number | undefined): string {
  if (sec == null || !Number.isFinite(sec)) return '-';
  if (sec < 60) return `${Math.round(sec)}s`;
  if (sec < 3600) {
    const m = Math.floor(sec / 60);
    const s = Math.round(sec % 60);
    return s > 0 ? `${m}m ${s}s` : `${m}m`;
  }
  const h = Math.floor(sec / 3600);
  const m = Math.round((sec % 3600) / 60);
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
}

/**
 * Format number with thousand separators.
 */
export function formatNumber(n: number | undefined, digits = 0): string {
  if (n == null || !Number.isFinite(n)) return '-';
  return n.toLocaleString('en-US', { maximumFractionDigits: digits });
}

/**
 * Format a risk score (0-1) as percentage.
 */
export function formatRisk(score: number | undefined): string {
  if (score == null) return '-';
  return `${Math.round(score * 100)}%`;
}

/**
 * Format bytes / KB / MB / GB.
 */
export function formatBytes(n: number | undefined): string {
  if (n == null) return '-';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(1)} GB`;
}

/**
 * Title-case a status enum, e.g. "tailscale_degraded" -> "Tailscale Degraded".
 */
export function humanizeStatus(s: string | undefined): string {
  if (!s) return '-';
  return s
    .split(/[_\s-]/)
    .map((w) => (w ? w[0]!.toUpperCase() + w.slice(1) : ''))
    .join(' ');
}
