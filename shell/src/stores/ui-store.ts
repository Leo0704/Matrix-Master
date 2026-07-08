import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export type Theme = 'light' | 'dark';

export interface UIState {
  sidebarOpen: boolean;
  theme: Theme;
  deviceFilter: string | null;
  statusFilter: string | null;
  toggleSidebar: () => void;
  setSidebarOpen: (v: boolean) => void;
  setTheme: (t: Theme) => void;
  toggleTheme: () => void;
  setDeviceFilter: (id: string | null) => void;
  setStatusFilter: (s: string | null) => void;
}

export const useUIStore = create<UIState>()(
  persist(
    (set, get) => ({
      sidebarOpen: true,
      theme: 'light',
      deviceFilter: null,
      statusFilter: null,
      toggleSidebar: () => set({ sidebarOpen: !get().sidebarOpen }),
      setSidebarOpen: (v) => set({ sidebarOpen: v }),
      setTheme: (t) => set({ theme: t }),
      toggleTheme: () => set({ theme: get().theme === 'light' ? 'dark' : 'light' }),
      setDeviceFilter: (id) => set({ deviceFilter: id }),
      setStatusFilter: (s) => set({ statusFilter: s }),
    }),
    {
      name: 'matrix-ui',
      partialize: (s) => ({ sidebarOpen: s.sidebarOpen, theme: s.theme }),
    },
  ),
);
