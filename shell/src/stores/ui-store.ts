import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export type Theme = 'light' | 'dark';

export interface UIState {
  sidebarOpen: boolean;
  theme: Theme;
  deviceFilter: string | null;
  statusFilter: string | null;
  /** v0.7+ 业务模型重构：当前活跃业务 ID（持久化到 localStorage） */
  activeBusinessId: string | null;
  toggleSidebar: () => void;
  setSidebarOpen: (v: boolean) => void;
  setTheme: (t: Theme) => void;
  toggleTheme: () => void;
  setDeviceFilter: (id: string | null) => void;
  setStatusFilter: (s: string | null) => void;
  setActiveBusinessId: (id: string | null) => void;
}

export const useUIStore = create<UIState>()(
  persist(
    (set, get) => ({
      sidebarOpen: true,
      theme: 'light',
      deviceFilter: null,
      statusFilter: null,
      activeBusinessId: null,  // 启动时从 localStorage 恢复（见 partialize）
      toggleSidebar: () => set({ sidebarOpen: !get().sidebarOpen }),
      setSidebarOpen: (v) => set({ sidebarOpen: v }),
      setTheme: (t) => set({ theme: t }),
      toggleTheme: () => set({ theme: get().theme === 'light' ? 'dark' : 'light' }),
      setDeviceFilter: (id) => set({ deviceFilter: id }),
      setStatusFilter: (s) => set({ statusFilter: s }),
      setActiveBusinessId: (id) => set({ activeBusinessId: id }),
    }),
    {
      name: 'matrix-ui',
      partialize: (s) => ({
        sidebarOpen: s.sidebarOpen,
        theme: s.theme,
        activeBusinessId: s.activeBusinessId,
      }),
    },
  ),
);

/** 当前活跃业务 ID（响应式订阅，组件调用返回 string | null）。 */
export function useActiveBusinessId(): string | null {
  return useUIStore((s) => s.activeBusinessId);
}

/** 切换当前活跃业务（写入 store + 持久化 localStorage）。 */
export function useSetActiveBusinessId() {
  return useUIStore((s) => s.setActiveBusinessId);
}
