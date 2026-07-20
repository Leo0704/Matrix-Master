import { useState } from 'react';
import { PowerOff, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { useToast } from '@/components/ui/use-toast';
import { useRetireDevice } from '@/hooks/use-devices';

interface DeviceRetireButtonProps {
  deviceId: string;
  deviceNickname: string;
  variant?: 'ghost' | 'outline' | 'destructive';
  size?: 'sm' | 'default';
}

export function DeviceRetireButton({
  deviceId,
  deviceNickname,
  variant = 'ghost',
  size = 'sm',
}: DeviceRetireButtonProps) {
  const retire = useRetireDevice();
  const { toast } = useToast();
  const [open, setOpen] = useState(false);

  async function handleRetire() {
    try {
      const res = await retire.mutateAsync(deviceId);
      toast({
        title: '设备已退役',
        description: res.unbound_account_handle
          ? `账号 ${res.unbound_account_handle} 已脱离该设备，设备密钥已撤销`
          : '设备密钥已撤销，不再参与运营',
      });
      setOpen(false);
    } catch (err) {
      toast({
        title: '退役失败',
        description: String(err),
        variant: 'destructive',
      });
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          variant={variant}
          size={size}
          className="text-xs text-muted-foreground hover:text-destructive"
          title="退役设备：清除账号绑定并撤销通信密钥"
        >
          <PowerOff className="mr-1 h-3 w-3" />
          退役设备
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>退役设备「{deviceNickname}」？</DialogTitle>
          <DialogDescription>
            设备将永久下线：清除绑定的账号、撤销通信密钥，并从设备列表中隐藏。
            账号的笔记数据不会丢失，之后可把账号绑到新设备。
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)} disabled={retire.isPending}>
            取消
          </Button>
          <Button variant="destructive" onClick={handleRetire} disabled={retire.isPending}>
            {retire.isPending && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
            确认退役
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
