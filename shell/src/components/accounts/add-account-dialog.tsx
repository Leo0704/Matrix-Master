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
import { useCreateAccount } from '@/hooks/use-accounts';
import { useDevices } from '@/hooks/use-devices';
import { usePersonas } from '@/hooks/use-personas';
import { useActiveBusinessId } from '@/stores/ui-store';
import { toast } from '@/components/ui/use-toast';

export function AddAccountDialog({ trigger }: { trigger?: React.ReactNode }) {
  const activeBusinessId = useActiveBusinessId();
  const [open, setOpen] = useState(false);
  const [handle, setHandle] = useState('');
  const [deviceId, setDeviceId] = useState('');
  const [personaId, setPersonaId] = useState('');

  const { data: devicesData } = useDevices();
  const { data: personasData } = usePersonas(
    activeBusinessId ? { business_id: activeBusinessId } : undefined,
  );
  const createAccount = useCreateAccount();

  const devices = devicesData?.items ?? [];
  const personas = personasData?.items ?? [];

  function reset() {
    setHandle('');
    setDeviceId('');
    setPersonaId('');
  }

  async function handleSubmit() {
    if (!activeBusinessId) {
      toast({ title: '请先选择业务', variant: 'destructive' });
      return;
    }
    if (!handle.trim() || !deviceId) {
      toast({ title: '请填完小红书号和设备', variant: 'destructive' });
      return;
    }
    try {
      await createAccount.mutateAsync({
        handle: handle.trim(),
        device_id: deviceId,
        persona_id: personaId || undefined,
        business_id: activeBusinessId,
      });
      toast({ title: '账号已添加' });
      setOpen(false);
      reset();
    } catch (e) {
      toast({
        title: '添加失败',
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
        {trigger ?? <Button>添加账号</Button>}
      </DialogTrigger>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>添加账号</DialogTitle>
          <DialogDescription>
            把已有的小红书号添加到系统，选要绑定的设备和人设。一台设备同时只能绑一个账号。
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="handle">小红书号</Label>
            <Input
              id="handle"
              value={handle}
              onChange={(e) => setHandle(e.target.value)}
              placeholder="例如：xiaohongshu123"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="device">绑定设备</Label>
            <select
              id="device"
              value={deviceId}
              onChange={(e) => setDeviceId(e.target.value)}
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            >
              <option value="">选择设备</option>
              {devices.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.nickname} {d.model ? `(${d.model})` : ''}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="persona">
              人设
              <span className="ml-1 text-xs text-muted-foreground">(可选，不选则从知识库自动匹配)</span>
            </Label>
            <select
              id="persona"
              value={personaId}
              onChange={(e) => setPersonaId(e.target.value)}
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            >
              <option value="">不绑定，自动匹配</option>
              {personas.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.title || p.id.slice(0, 8)}
                </option>
              ))}
            </select>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            取消
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={createAccount.isPending || !handle.trim() || !deviceId}
          >
            {createAccount.isPending ? '添加中…' : '添加'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
