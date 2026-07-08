import { cn } from '@/lib/utils';
import { formatRisk } from '@/lib/format';

export function RiskIndicator({ score }: { score: number }) {
  const pct = Math.round(Math.max(0, Math.min(1, score)) * 100);
  const color =
    score > 0.7
      ? 'bg-destructive'
      : score > 0.4
        ? 'bg-warning'
        : 'bg-success';
  return (
    <div className="flex items-center gap-2">
      <div className="h-2 w-24 overflow-hidden rounded-full bg-muted">
        <div className={cn('h-full', color)} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-muted-foreground">{formatRisk(score)}</span>
    </div>
  );
}
