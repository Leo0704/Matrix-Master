import {
  BarChart3,
  BookOpen,
  HelpCircle,
  type LucideIcon,
  Pause,
  Repeat,
  Stethoscope,
  Trophy,
} from 'lucide-react';
import { cn } from '@/lib/utils';

interface QuickPrompt {
  id: string;
  icon: LucideIcon;
  title: string;
  description: string;
  prompt: string;
}

const QUICK_PROMPTS: QuickPrompt[] = [
  {
    id: 'summary',
    icon: BarChart3,
    title: '总览',
    description: '看所有 goal 当前状态',
    prompt: '现在有几个 goal 在跑？数据怎么样？',
  },
  {
    id: 'weekly',
    icon: Trophy,
    title: '周榜',
    description: '最近一周数据最好',
    prompt: '最近一周哪个 goal 数据最好？',
  },
  {
    id: 'diagnose',
    icon: Stethoscope,
    title: '诊断',
    description: '诊断数据下滑原因',
    prompt: '帮我看看最近数据最差的 goal 为什么掉了',
  },
  {
    id: 'pause',
    icon: Pause,
    title: '批量暂停',
    description: '按主题暂停 goal',
    prompt: '暂停所有 product_category=鞋子 的 goal',
  },
  {
    id: 'rounds',
    icon: Repeat,
    title: '改轮数',
    description: '把 goal 改成跑更多轮',
    prompt: '把 max_rounds=3 的 goal 改成 5',
  },
  {
    id: 'kb',
    icon: BookOpen,
    title: '审 KB',
    description: '看新写的 strategy_card',
    prompt: '看看这周 KB 里新写了哪些 strategy_card',
  },
  {
    id: 'help',
    icon: HelpCircle,
    title: '能做什么',
    description: '列出所有能力',
    prompt: '你能帮我做什么？',
  },
];

interface Props {
  onPick: (prompt: string) => void;
  disabled?: boolean;
}

export function QuickPromptButtons({ onPick, disabled }: Props) {
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-7">
      {QUICK_PROMPTS.map((p) => {
        const Icon = p.icon;
        return (
          <button
            key={p.id}
            type="button"
            disabled={disabled}
            onClick={() => onPick(p.prompt)}
            className={cn(
              'flex flex-col items-start gap-1 rounded-md border p-2 text-left text-xs transition-colors',
              'hover:border-primary/50 hover:bg-accent/30',
              'disabled:cursor-not-allowed disabled:opacity-50',
            )}
            title={p.prompt}
          >
            <div className="flex items-center gap-1 font-medium">
              <Icon className="h-3.5 w-3.5" />
              {p.title}
            </div>
            <div className="line-clamp-2 text-muted-foreground">
              {p.description}
            </div>
          </button>
        );
      })}
    </div>
  );
}