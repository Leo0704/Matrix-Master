import { Badge } from '@/components/ui/badge';
import type { DeviceStatus, AccountStatus, NoteStatus, AgentRunStatus } from '@/types/api';
import { humanizeStatus } from '@/lib/format';
import { cn } from '@/lib/utils';

type Status = DeviceStatus | AccountStatus | NoteStatus | AgentRunStatus | string;

const statusToVariant: Record<string, 'default' | 'secondary' | 'destructive' | 'outline' | 'success' | 'warning' | 'muted'> = {
  // device
  pending: 'muted',
  active: 'success',
  offline: 'muted',
  tailscale_degraded: 'warning',
  disabled: 'muted',
  // account
  suspended: 'warning',
  banned: 'destructive',
  // note
  draft: 'muted',
  reviewing: 'secondary',
  scheduled: 'secondary',
  publishing: 'secondary',
  published: 'success',
  failed: 'destructive',
  deleted: 'muted',
  // agent run
  running: 'secondary',
  success: 'success',
  cancelled: 'muted',
  timeout: 'warning',
  // goal
  achieved: 'success',
  // health
  ok: 'success',
  degraded: 'warning',
  down: 'destructive',
  error: 'destructive',
  connected: 'success',
  disconnected: 'warning',
};

export function StatusBadge({ status, className, label }: { status: Status; className?: string; label?: string }) {
  const variant = statusToVariant[status] ?? 'default';
  return (
    <Badge variant={variant} className={cn('font-normal', className)}>
      {label ?? humanizeStatus(status)}
    </Badge>
  );
}
