# Matrix Master - Web Frontend

Tauri 内嵌的 Web 前端（React 18 + TypeScript 5 + Vite 5 + Tailwind 3 + shadcn/ui）。

## 开发

```bash
# 1. 安装依赖
npm install

# 2. 启动 dev server（Tauri 会自动调用）
npm run dev

# 3. 跑测试
npm run test

# 4. 类型检查
npm run typecheck

# 5. 打包构建（Tauri 调此命令产 dist/）
npm run build
```

## 与 Tauri 集成

`src-tauri/tauri.conf.json` 已配置：
- `devUrl`: `http://localhost:1420`（Vite dev server）
- `frontendDist`: `../dist`（生产构建产物）

Vite 端口固定 `1420`，`strictPort: true`，避免 Tauri 找不到入口。

## 与后端通信

默认调 `http://localhost:8666/api/v1`（Python 后端，由 Tauri 拉起）。

- 开发环境：后端不可达时自动降级到 `src/lib/mock-data.ts` 假数据，方便纯前端调试。
- 生产环境：由 `use-tauri.ts` 通过 IPC 与 Rust 进程交互（如果启用了 Tauri 模式）。

## 目录结构

```
src/
  components/
    ui/         shadcn/ui 组件（自托管）
    layout/     布局（Sidebar / Topbar / AppLayout）
    common/     通用（StatusBadge / EmptyState / ErrorState / ...）
    dashboard/  Dashboard 专用（KPI 卡片 / 图表）
    devices/    设备相关
    accounts/   账号相关
    goals/      目标
    notes/      笔记
    chat/       自然语言对话
  hooks/        TanStack Query hooks
  lib/          工具（api-client / mock-data / utils / format）
  pages/        路由页面
  stores/       Zustand 状态
  types/        OpenAPI 对应 TS 类型
  test/         Vitest setup
```

## 状态管理

- **服务端状态**：TanStack Query（`@tanstack/react-query`），所有数据 fetch / cache / invalidate。
- **客户端状态**：Zustand（`use-ui-store`）：sidebar open / theme / current device filter。

## 暗色模式

默认 `class` 模式，由 `use-ui-store` 控制 `dark` class，shadcn CSS variables 自动切色。

## 测试

- `vitest` + `@testing-library/react` + `jsdom`
- 至少：每个 shadcn UI 组件 1 个测试 + Dashboard 页面 1 个测试
- 跑 `npm run test` 看结果

## OpenAPI 规范

权威 API 规范在 `docs/api/master-rest.openapi.yaml`，前端类型严格按此生成（见 `src/types/api.ts`）。
