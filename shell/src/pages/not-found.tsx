import { Link } from 'react-router-dom';
import { Button } from '@/components/ui/button';

export function NotFound() {
  return (
    <div className="flex h-[60vh] flex-col items-center justify-center gap-3 text-center">
      <p className="text-6xl font-bold text-muted-foreground">404</p>
      <h1 className="text-xl font-semibold">页面不存在</h1>
      <p className="text-sm text-muted-foreground">您访问的路径不存在或已被移除</p>
      <Button asChild>
        <Link to="/dashboard">回到总览</Link>
      </Button>
    </div>
  );
}
