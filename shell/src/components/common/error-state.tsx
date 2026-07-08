import { AlertTriangle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { describeError } from '@/lib/api-client';

export function ErrorState({
  error,
  onRetry,
  title = '加载失败',
}: {
  error: unknown;
  onRetry?: () => void;
  title?: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 p-8 text-center">
      <AlertTriangle className="h-10 w-10 text-destructive" />
      <h3 className="text-lg font-semibold">{title}</h3>
      <p className="max-w-md text-sm text-muted-foreground">{describeError(error)}</p>
      {onRetry && (
        <Button variant="outline" onClick={onRetry} size="sm">
          重试
        </Button>
      )}
    </div>
  );
}
