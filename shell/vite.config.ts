/// <reference types="vitest" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

// Tauri expects a fixed port, fail if that port is not available
export default defineConfig(async () => ({
  plugins: [react()],

  // Prevent Vite from obscuring rust errors
  clearScreen: false,

  // Tauri uses a fixed port; fail if it's already in use
  server: {
    port: 1420,
    strictPort: true,
    host: 'localhost',
    watch: {
      // Tell Vite to ignore watching `src-tauri`
      ignored: ['**/src-tauri/**'],
    },
  },

  // Vite options tailored for Tauri development
  envPrefix: ['VITE_', 'TAURI_'],
  define: {
    // Inject global window.__TAURI__ helpers
    'process.env.NODE_ENV': JSON.stringify(process.env.NODE_ENV ?? 'development'),
  },

  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },

  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: true,
  },
}));
