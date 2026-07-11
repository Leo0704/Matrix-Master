import { useEffect, useState } from 'react';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Button } from '@/components/ui/button';
import { useUIStore } from '@/stores/ui-store';
import { useTauri } from '@/hooks/use-tauri';
import { useSetting, useUpsertSetting } from '@/hooks/use-settings';
import { toast } from '@/components/ui/use-toast';

function SettingField({
  settingKey,
  label,
  description,
  type = 'text',
  defaultValue = '',
}: {
  settingKey: string;
  label: string;
  description?: string;
  type?: 'text' | 'password' | 'number';
  defaultValue?: string;
}) {
  const { data, isLoading } = useSetting(settingKey);
  const upsert = useUpsertSetting();
  const [value, setValue] = useState(defaultValue);

  useEffect(() => {
    if (data) {
      const v = (data.value as Record<string, unknown>).value;
      if (v != null) setValue(String(v));
    }
  }, [data]);

  async function save() {
    try {
      await upsert.mutateAsync({
        key: settingKey,
        value: { value },
        description,
      });
      toast({ title: `${label} 已保存` });
    } catch (e) {
      toast({
        title: '保存失败',
        description: (e as Error).message,
        variant: 'destructive',
      });
    }
  }

  if (isLoading) return <p className="text-xs text-muted-foreground">加载中…</p>;

  return (
    <div className="space-y-2">
      <div className="space-y-1">
        <Label htmlFor={settingKey}>{label}</Label>
        <Input
          id={settingKey}
          type={type}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={defaultValue}
        />
        {description && <p className="text-xs text-muted-foreground">{description}</p>}
      </div>
      <Button onClick={save} disabled={upsert.isPending} size="sm">
        {upsert.isPending ? '保存中…' : '保存'}
      </Button>
    </div>
  );
}

export function Settings() {
  const { theme, setTheme } = useUIStore();
  const tauri = useTauri();
  const upsert = useUpsertSetting();

  async function setThemePersisted(t: 'light' | 'dark') {
    setTheme(t);
    try {
      await upsert.mutateAsync({ key: 'ui.theme', value: { value: t } });
    } catch {
      // UI 已更新，DB 失败不阻塞
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">设置</h1>
        <p className="text-sm text-muted-foreground">主控配置（持久化到 app_config 表）</p>
      </div>

      <Tabs defaultValue="general">
        <TabsList>
          <TabsTrigger value="general">通用</TabsTrigger>
          <TabsTrigger value="llm">LLM</TabsTrigger>
          <TabsTrigger value="review">审核</TabsTrigger>
          <TabsTrigger value="integration">集成</TabsTrigger>
          <TabsTrigger value="danger">应急</TabsTrigger>
        </TabsList>

        <TabsContent value="general" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">外观</CardTitle>
              <CardDescription>UIStore 当前状态；点击后端会持久化</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="flex items-center gap-2">
                <Button
                  variant={theme === 'light' ? 'default' : 'outline'}
                  onClick={() => setThemePersisted('light')}
                >
                  亮色
                </Button>
                <Button
                  variant={theme === 'dark' ? 'default' : 'outline'}
                  onClick={() => setThemePersisted('dark')}
                >
                  暗色
                </Button>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="llm" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">LLM 配置</CardTitle>
              <CardDescription>API key 存 app_config 表（生产建议改用系统 keyring）</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <SettingField
                settingKey="llm.api_key_anthropic"
                label="Anthropic API Key"
                type="password"
                description="sk-ant-…"
              />
              <SettingField
                settingKey="llm.api_key_openai"
                label="OpenAI API Key"
                type="password"
                description="sk-…"
              />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="review" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">自动检查规则</CardTitle>
              <CardDescription>
                AI 写的每篇笔记会自动检查两件事：违禁词、跟规则库不冲突。检查不通过会进死信队列等你处理。
              </CardDescription>
            </CardHeader>
          </Card>
        </TabsContent>

        <TabsContent value="integration" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">通知</CardTitle>
              <CardDescription>邮件 / 飞书 / Slack / Webhook</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <SettingField
                settingKey="notify.webhook_url"
                label="Webhook URL"
                description="告警触发时 POST 至此 URL"
              />
              <SettingField
                settingKey="notify.feishu_url"
                label="飞书机器人 Webhook"
              />
              <SettingField
                settingKey="notify.slack_url"
                label="Slack Incoming Webhook"
              />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="danger" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base text-destructive">应急操作</CardTitle>
              <CardDescription>紧急情况使用</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2">
              <Button
                variant="destructive"
                onClick={() => toast({ title: '已触发紧急停止（mock）' })}
              >
                紧急停止所有 Goal
              </Button>
              {tauri.isTauri && (
                <Button
                  variant="outline"
                  onClick={() =>
                    tauri.restartBackend().then(() => toast({ title: '后端重启请求已发送' }))
                  }
                >
                  重启 Python 后端
                </Button>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
