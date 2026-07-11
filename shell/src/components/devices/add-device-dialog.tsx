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
import { QrCode, Copy } from 'lucide-react';
import { useRegisterDevice } from '@/hooks/use-devices';
import { toast } from '@/components/ui/use-toast';

export function AddDeviceDialog({ trigger }: { trigger?: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState<'form' | 'pair'>('form');
  const [pairCode, setPairCode] = useState<string>('');
  const [nickname, setNickname] = useState('');
  const [model, setModel] = useState('');
  const [android, setAndroid] = useState('');
  const [apkVer, setApkVer] = useState('0.4.0');
  const [tailnetIp, setTailnetIp] = useState('');
  const register = useRegisterDevice();

  function reset() {
    setStep('form');
    setPairCode('');
    setNickname('');
    setModel('');
    setAndroid('');
    setApkVer('0.4.0');
    setTailnetIp('');
  }

  async function handleSubmit() {
    try {
      const device = await register.mutateAsync({
        nickname,
        model,
        android_version: android,
        apk_version: apkVer,
        tailnet_ip: tailnetIp,
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
                填写设备基本信息；提交后主控会生成配对码，手机扫码即可完成绑定。
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
                <Label htmlFor="model">型号</Label>
                <Input
                  id="model"
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  placeholder="Pixel 7"
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="android">Android 版本</Label>
                <Input
                  id="android"
                  value={android}
                  onChange={(e) => setAndroid(e.target.value)}
                  placeholder="14"
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="tailnetIp">Tailnet IP</Label>
                <Input
                  id="tailnetIp"
                  value={tailnetIp}
                  onChange={(e) => setTailnetIp(e.target.value)}
                  placeholder="100.64.0.x"
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="apkVer">APK 版本</Label>
                <Input
                  id="apkVer"
                  value={apkVer}
                  onChange={(e) => setApkVer(e.target.value)}
                />
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setOpen(false)}>
                取消
              </Button>
              <Button
                onClick={handleSubmit}
                disabled={!nickname || !model || !android || !tailnetIp || register.isPending}
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
                在 companion APK 中输入 6 位配对码，或扫描下方二维码。10 分钟内有效。
              </DialogDescription>
            </DialogHeader>
            <div className="flex flex-col items-center gap-4 py-4">
              <div className="flex h-32 w-32 items-center justify-center rounded-md border-2 border-dashed bg-muted">
                <QrCode className="h-16 w-16 text-muted-foreground" />
              </div>
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
              <p className="text-xs text-muted-foreground">等待手机端确认…</p>
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
