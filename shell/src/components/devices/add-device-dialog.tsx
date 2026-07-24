import { useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Copy, KeyRound } from 'lucide-react';
import { useRegisterDevice } from '@/hooks/use-devices';
import { useActiveBusinessId } from '@/stores/ui-store';
import { toast } from '@/components/ui/use-toast';

export function AddDeviceDialog({ trigger }: { trigger?: React.ReactNode }) {
  const activeBusinessId = useActiveBusinessId();
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState<'form' | 'pair'>('form');
  const [pairCode, setPairCode] = useState<string>('');
  const [nickname, setNickname] = useState('');
  // P2-3：型号 / Android / APK 版本 / Tailnet IP 由 APK 配对时自报上去，
  // 用户不必再填。手填的数据反而会被 APK 上线后真实值覆盖。
  const [adbSerial, setAdbSerial] = useState('');
  const register = useRegisterDevice();

  function reset() {
    setStep('form');
    setPairCode('');
    setNickname('');
    setAdbSerial('');
  }

  async function handleSubmit() {
    try {
      if (!activeBusinessId) {
        toast({ title: '请先选择业务', variant: 'destructive' });
        return;
      }
      const device = await register.mutateAsync({
        nickname,
        adb_serial: adbSerial || undefined,
        business_id: activeBusinessId,
      });
      if (!device.pair_code) throw new Error('主控没有返回配对码');
      setPairCode(device.pair_code);
      setStep('pair');
      toast({ title: '设备已注册', description: '请在手机端输入配对码' });
    } catch (e) {
      toast({
        title: '注册失败',
        description: (e as Error).message,
        variant: 'destructive',
      });
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        setOpen(v);
        if (!v) reset();
      }}
    >
      <DialogTrigger asChild>
        {trigger ?? <Button>添加设备</Button>}
      </DialogTrigger>
      <DialogContent className="max-w-md">
        {step === 'form' ? (
          <>
            <DialogHeader>
              <DialogTitle>添加新设备</DialogTitle>
              <DialogDescription>
                填个昵称，提交后主控生成配对码。手机装配套客户端，
                输入配对码后，型号 / 安卓版本 / 客户端版本 / 内网 IP 会自动识别。
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-3">
              <div className="space-y-1">
                <Label htmlFor="nickname">设备昵称</Label>
                <Input
                  id="nickname"
                  value={nickname}
                  onChange={(e) => setNickname(e.target.value)}
                  placeholder="Pixel-01"
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="adbSerial">
                  调试串号
                  <span className="ml-1 text-xs text-muted-foreground">(可选)</span>
                </Label>
                <Input
                  id="adbSerial"
                  value={adbSerial}
                  onChange={(e) => setAdbSerial(e.target.value)}
                  placeholder="例如 12ab34cd"
                />
              </div>
              <p className="text-xs text-muted-foreground">
                型号、安卓版本、客户端版本、IP 等信息会在设备首次连接后自动识别。
              </p>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setOpen(false)}>
                取消
              </Button>
              <Button
                onClick={handleSubmit}
                disabled={!nickname.trim() || register.isPending}
              >
                {register.isPending ? '注册中…' : '注册并生成配对码'}
              </Button>
            </DialogFooter>
          </>
        ) : (
          <>
            <DialogHeader>
              <DialogTitle>手机端配对</DialogTitle>
              <DialogDescription>
                在 companion 客户端里手动输入下方 8 位配对码。10 分钟内有效。
              </DialogDescription>
            </DialogHeader>
            <div className="flex flex-col items-center gap-4 py-4">
              <div className="flex items-center gap-2 rounded-md border bg-muted px-4 py-2 font-mono text-2xl tracking-widest">
                {pairCode}
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => {
                    navigator.clipboard.writeText(pairCode);
                    toast({ title: '已复制配对码' });
                  }}
                >
                  <Copy className="h-4 w-4" />
                </Button>
              </div>
              <p className="flex items-center text-xs text-muted-foreground">
                <KeyRound className="mr-1 h-3 w-3" />
                把这串数字输进手机客户端，等待手机端确认…
              </p>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setOpen(false)}>
                关闭
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
