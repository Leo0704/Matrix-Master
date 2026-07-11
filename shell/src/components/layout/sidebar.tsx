import { NavLink } from 'react-router-dom';
import {
  LayoutDashboard,
  MessageSquare,
  Smartphone,
  Users,
  UserCircle,
  FileText,
  Target,
  Activity,
  BarChart3,
  BookOpen,
  Bell,
  Settings,
} from 'lucide-react';
import { useUIStore } from '@/stores/ui-store';
import { cn } from '@/lib/utils';

const nav = [
  { to: '/dashboard', label: '总览', icon: LayoutDashboard },
  { to: '/chat', label: '对话', icon: MessageSquare },
  { to: '/devices', label: '设备', icon: Smartphone },
  { to: '/accounts', label: '账号', icon: Users },
  { to: '/notes', label: '内容', icon: FileText },
  { to: '/goals', label: '目标', icon: Target },
  { to: '/agent-runs', label: 'Agent', icon: Activity },
  { to: '/data', label: '数据', icon: BarChart3 },
  { to: '/kb', label: '知识库', icon: BookOpen },
  { to: '/alerts', label: '告警', icon: Bell },
];

export function Sidebar() {
  const sidebarOpen = useUIStore((s) => s.sidebarOpen);

  return (
    <aside
      className={cn(
        'flex h-full flex-col border-r bg-card transition-all duration-200',
        sidebarOpen ? 'w-56' : 'w-14',
      )}
    >
      <div className="flex h-14 items-center gap-2 border-b px-4">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground">
          <span className="text-sm font-bold">M</span>
        </div>
        {sidebarOpen && <span className="truncate font-semibold">Matrix Master</span>}
      </div>
      <nav className="flex-1 overflow-y-auto p-2">
        <ul className="space-y-1">
          {nav.map((item) => {
            const Icon = item.icon;
            return (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  className={({ isActive }) =>
                    cn(
                      'flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors',
                      isActive
                        ? 'bg-accent text-accent-foreground'
                        : 'text-muted-foreground hover:bg-accent/50 hover:text-foreground',
                    )
                  }
                  title={!sidebarOpen ? item.label : undefined}
                >
                  <Icon className="h-4 w-4 shrink-0" />
                  {sidebarOpen && <span className="truncate">{item.label}</span>}
                </NavLink>
              </li>
            );
          })}
        </ul>
      </nav>
    </aside>
  );
}
