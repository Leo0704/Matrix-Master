/**
 * Tauri IPC wrapper. All Rust-side commands (get_app_info / restart_python_backend / etc)
 * are exposed on `window.__TAURI__.core.invoke`. When running outside Tauri
 * (e.g. pure Vite dev), fall back to a no-op so the UI doesn't crash.
 */

interface TauriCore {
  invoke: (cmd: string, args?: Record<string, unknown>) => Promise<unknown>;
}

declare global {
  interface Window {
    __TAURI__?: { core?: TauriCore; tauri?: TauriCore };
  }
}

function isTauri(): boolean {
  return typeof window !== 'undefined' && !!window.__TAURI__;
}

async function invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T | null> {
  if (!isTauri()) {
    // eslint-disable-next-line no-console
    console.warn(`[use-tauri] ${cmd} called outside Tauri — returning null`);
    return null;
  }
  const tauri = window.__TAURI__?.core ?? window.__TAURI__?.tauri;
  if (!tauri) return null;
  return tauri.invoke(cmd, args) as Promise<T>;
}

export function useTauri() {
  return {
    isTauri: isTauri(),
    getAppInfo: () => invoke<{ version: string; name: string }>('get_app_info'),
    restartBackend: () => invoke<{ ok: boolean }>('restart_python_backend'),
    generateHmacKey: () =>
      invoke<{ key_id: string; hmac_key: string }>('generate_hmac_key'),
    showNotification: (title: string, body: string) =>
      invoke('show_notification', { title, body }),
  };
}
