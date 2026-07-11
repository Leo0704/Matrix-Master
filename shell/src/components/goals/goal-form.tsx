import { useState } from 'react';
import { Megaphone, FlaskConical, Repeat } from 'lucide-react';
import { z } from 'zod';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import { useCreateGoal } from '@/hooks/use-goals';
import { toast } from '@/components/ui/use-toast';
import { cn } from '@/lib/utils';

const formSchema = z.object({
  description: z.string().min(2, '请描述目标'),
  target_likes: z.number().int().min(1).max(1_000_000),
  notes_per_round: z.number().int().min(1).max(20),
  max_rounds: z.number().int().min(1).max(20),
});

// 3 个场景模板：对应中控能跑通的 3 类
type Scenario = {
  id: 'exposure' | 'test' | 'ongoing';
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  description: string;
  themeExample: string;
  target_likes: number;
  notes_per_round: number;
  max_rounds: number;
};

const SCENARIOS: Scenario[] = [
  {
    id: 'exposure',
    icon: Megaphone,
    title: '品牌曝光',
    description: '一波定输赢：发 N 篇同主题，看总浏览/点赞',
    themeExample: '夏季女生穿搭种草',
    target_likes: 1000,
    notes_per_round: 5,
    max_rounds: 1,
  },
  {
    id: 'test',
    icon: FlaskConical,
    title: '内容测试',
    description: '多轮找爆款：发 N 篇不同角度，KB 自动出爆款模板',
    themeExample: '夏季男生穿搭种草',
    target_likes: 100,
    notes_per_round: 5,
    max_rounds: 3,
  },
  {
    id: 'ongoing',
    icon: Repeat,
    title: '持续运营',
    description: '周更节奏：每轮少发，跑多轮，看哪周最爆',
    themeExample: '夏季女生穿搭周更',
    target_likes: 2000,
    notes_per_round: 3,
    max_rounds: 5,
  },
];

export function GoalForm({ onCreated }: { onCreated?: () => void }) {
  const [scenario, setScenario] = useState<Scenario['id'] | null>(null);
  const [text, setText] = useState('');
  const [targetLikes, setTargetLikes] = useState(1000);
  const [notesPerRound, setNotesPerRound] = useState(5);
  const [maxRounds, setMaxRounds] = useState(1);
  const { mutate, isPending } = useCreateGoal();

  function applyScenario(s: Scenario) {
    setScenario(s.id);
    setText(s.themeExample);
    setTargetLikes(s.target_likes);
    setNotesPerRound(s.notes_per_round);
    setMaxRounds(s.max_rounds);
  }

  async function submit() {
    const parsed = formSchema.safeParse({
      description: text,
      target_likes: targetLikes,
      notes_per_round: notesPerRound,
      max_rounds: maxRounds,
    });
    if (!parsed.success) {
      toast({
        title: '请检查表单',
        description: parsed.error.issues[0]?.message ?? '字段不合法',
        variant: 'destructive',
      });
      return;
    }
    try {
      await mutate({
        type: 'natural_language',
        target: {
          theme: parsed.data.description,
          audience: '',
          product_category: '',
        },
        target_likes: parsed.data.target_likes,
        notes_per_round: parsed.data.notes_per_round,
        max_rounds: parsed.data.max_rounds,
      });
      toast({ title: '目标已创建', description: '中控将开始自动运营' });
      setText('');
      setScenario(null);
      onCreated?.();
    } catch (e) {
      toast({
        title: '创建失败',
        description: (e as Error).message,
        variant: 'destructive',
      });
    }
  }

  return (
    <div className="space-y-4">
      {/* 3 个场景卡 */}
      <div>
        <Label className="mb-2 block">选个场景</Label>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
          {SCENARIOS.map((s) => {
            const Icon = s.icon;
            const active = scenario === s.id;
            return (
              <button
                key={s.id}
                type="button"
                onClick={() => applyScenario(s)}
                className={cn(
                  'flex flex-col items-start gap-1 rounded-md border p-3 text-left text-sm transition-colors',
                  active
                    ? 'border-primary bg-primary/5 ring-1 ring-primary'
                    : 'hover:border-primary/50 hover:bg-accent/30',
                )}
              >
                <div className="flex items-center gap-1.5 font-medium">
                  <Icon className="h-4 w-4" />
                  {s.title}
                </div>
                <div className="text-xs text-muted-foreground">
                  {s.description}
                </div>
                <div className="mt-1 text-xs text-muted-foreground">
                  {s.notes_per_round} 篇 × {s.max_rounds} 轮 ·{' '}
                  {s.target_likes} 赞收工
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* 主题 */}
      <div className="space-y-1">
        <Label htmlFor="goal-desc">目标主题</Label>
        <Textarea
          id="goal-desc"
          rows={2}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setScenario(null);
          }}
          placeholder="例：夏季女生穿搭种草"
        />
      </div>

      {/* 可调字段 */}
      <div className="grid grid-cols-3 gap-3">
        <div className="space-y-1">
          <Label htmlFor="target-likes" className="text-xs">
            收工赞数
          </Label>
          <Input
            id="target-likes"
            type="number"
            min={1}
            max={1_000_000}
            value={targetLikes}
            onChange={(e) => setTargetLikes(Number(e.target.value) || 0)}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="notes-per-round" className="text-xs">
            每轮篇数
          </Label>
          <Input
            id="notes-per-round"
            type="number"
            min={1}
            max={20}
            value={notesPerRound}
            onChange={(e) => setNotesPerRound(Number(e.target.value) || 1)}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="max-rounds" className="text-xs">
            最多轮数
          </Label>
          <Input
            id="max-rounds"
            type="number"
            min={1}
            max={20}
            value={maxRounds}
            onChange={(e) => setMaxRounds(Number(e.target.value) || 1)}
          />
        </div>
      </div>

      <Button
        onClick={submit}
        disabled={isPending || text.length < 2}
        className="w-full"
      >
        {isPending ? '提交中…' : '创建目标'}
      </Button>
    </div>
  );
}
