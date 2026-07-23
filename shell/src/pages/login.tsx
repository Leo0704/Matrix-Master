import { useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiClient, AUTH_TOKEN_KEY, describeError } from '@/lib/api-client';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';

/**
 * 登录页：输入控制台访问密码（= 后端 MATRIX_API_SECRET；
 * 自动生成的见后端启动日志或 backend/.api_secret）。
 * 提交后先存 token 再调一个轻量端点探活：成功 → /chat；401 → 提示重试。
 */
export function Login() {
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const token = password.trim();
    if (!token || busy) return;
    setBusy(true);
    setError(null);
    localStorage.setItem(AUTH_TOKEN_KEY, token);
    try {
      await apiClient.get('/devices');
      navigate('/chat', { replace: true });
    } catch (err) {
      // 401 时拦截器已清 token；这里只负责提示
      localStorage.removeItem(AUTH_TOKEN_KEY);
      setError(
        `密码不正确或后端不可达，请重试（${describeError(err)}）`,
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Matrix 控制台</CardTitle>
          <CardDescription>
            请输入访问密码（见后端启动日志，或服务器上的 backend/.api_secret 文件）
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="console-password">访问密码</Label>
              <Input
                id="console-password"
                type="password"
                autoFocus
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="粘贴密码后回车"
              />
            </div>
            {error && <p className="text-sm text-destructive">{error}</p>}
            <Button type="submit" className="w-full" disabled={busy || !password.trim()}>
              {busy ? '验证中…' : '进入控制台'}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
