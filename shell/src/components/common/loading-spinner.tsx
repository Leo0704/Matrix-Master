import { Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';

export function LoadingSpinner({ className, size = 'h-6 w-6' }: { className?: string; size?: string }) {
  return <Loader2 className={cn('animate-spin text-muted-foreground', size, className)} />;
}

export function LoadingBlock({ tip = '加载中…' }: { tip?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 p-8 text-sm text-muted-foreground">
      <LoadingSpinner />
      <span>{tip}</span>
    </div>
  );
}
