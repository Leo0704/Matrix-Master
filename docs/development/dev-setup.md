# 开发环境搭建

| 项 | 内容 |
|---|---|
| 适用对象 | 新入职的后端 / Agent / APK / 前端开发 |
| 目标 | 三端（macOS / Windows / Ubuntu）从 0 到跑通 hello world |
| 配套 | [SDD.md](../architecture/SDD.md) / [database/schema.sql](../database/schema.sql) / [API 规范](../api/) |

## 1. 前置依赖

### 1.1 通用

| 工具 | 版本 | 用途 |
|---|---|---|
| Git | 2.30+ | 版本控制 |
| Docker | 24+ | PostgreSQL / Headscale / 后端 / 前端 vite / 测试容器化 |
| Docker Compose | v2 | 多容器编排 |
| Node.js | 20+ | host 上 npm install + `npx tauri dev` 用 |
| pnpm | 8+ | 可选；frontend service 默认用 npm |

### 1.2 后端 / 测试 / 数据库

| 工具 | 位置 | 备注 |
|---|---|---|
| Python 3.11 | docker 镜像 | Dockerfile / Dockerfile.backend 固定 |
| [uv](https://docs.astral.sh/uv/) | docker 镜像 | 包管理（在镜像内，host 不需要装） |
| Alembic | docker 镜像（test service） | 数据库迁移 |

### 1.3 APK 开发

| 工具 | 版本 | 备注 |
|---|---|---|
| Android Studio | Hedgehog+ | IDE |
| JDK | 17+ | 推荐 zulu |
| Android SDK | 34+ | platform-tools, build-tools 34 |
| Gradle | 8+ | 随 Android Studio |
| Kotlin | 1.9+ | 随 Android Studio |
| 模拟器或真机 | Android 10+ | AccessibilityService 需要 |

## 2. 克隆与初始化

```bash
# 克隆
git clone <repo-url> matrix
cd matrix

# 子模块（如有）
git submodule update --init --recursive

# 复制环境变量模板（host 端用，docker 内不需要）
cp .env.example .env
# 编辑 .env 填入 LLM API key 等
```

> host 不需要装 Python 依赖 —— 全部在 docker 镜像里（Dockerfile / Dockerfile.backend）。

## 3. 启动 PostgreSQL

### 3.1 用 Docker（推荐）

```bash
# 在仓库根目录
docker compose up -d postgres

# 验证
docker compose ps
docker compose logs postgres
```

`docker-compose.yml` 内容：

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: matrix
      POSTGRES_USER: matrix
      POSTGRES_PASSWORD: matrix_dev
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./docs/database/schema.sql:/docker-entrypoint-initdb.d/01-schema.sql

volumes:
  postgres_data:
```

### 3.2 应用迁移（容器内）

迁移走 docker test service，与测试共用同一镜像（alembic 已在镜像里）：

```bash
# 在仓库根目录
docker compose run --rm test alembic upgrade head

# 验证
psql -h localhost -U matrix -d matrix -c "\dt"
```

### 3.3 跑测试（容器内，约定）

本项目约定 **pytest 必须在 docker 内跑**，不要本地 `pytest`：

```bash
# 全部测试
docker compose run --rm test

# 单文件
docker compose run --rm test pytest tests/test_x.py

# 按关键字
docker compose run --rm test pytest tests -k agent

# 跑某个 mark
docker compose run --rm test pytest tests -m "not slow"
```

`backend/` 通过 volumes bind mount 挂进容器，源码改动立即生效，无需 rebuild 镜像。只有 `pyproject.toml` / `uv.lock` 变化才需要 `docker compose build test`。

## 4. 启动 Headscale（开发用）

```bash
# 启动 Headscale + DERP
docker compose up -d headscale derp

# 验证
docker compose logs headscale
```

开发环境 Headscale 用内存数据库（`database.type: sqlite`），无需额外配置。

注册测试设备：

```bash
# 在主控所在机器
docker exec -it headscale headscale nodes register --user default --key nodekey:xxx
```

## 5. 启动 Python 后端（docker 内）

```bash
# 起基础设施（如果 §3 §4 还没起）
docker compose up -d postgres headscale derp

# 起后端（uvicorn 在 docker 内，--reload 自动热重启）
docker compose up -d backend

# 健康检查
curl http://localhost:8666/api/v1/health
```

预期返回：

```json
{
  "status": "ok",
  "version": "0.3.0",
  "uptime_sec": 3,
  "db": "ok",
  "tailscale": "disconnected"
}
```

## 6. 启动前端 vite dev server

```bash
# 起前端 vite dev server（docker 内，端口 1420）
docker compose up -d frontend
```

首次启动会：
1. docker 容器内启动 vite dev server
2. 通过 http://localhost:1420 可在浏览器访问 React 页面（开发者工具 dev mode）
3. 浏览器通过 http://localhost:8666 调用后端 → 经 port mapping → backend 容器内 uvicorn

## 7. 启动 APK（开发模式）

### 7.1 用模拟器

```bash
# 启动 Android 模拟器（Android 10+）
emulator -avd Pixel_7_API_34

# 构建并安装 APK
cd apk
./gradlew installDebug

# 启动 APK
adb shell am start -n com.matrix.companion/.MainActivity
```

### 7.2 用真机

1. 打开 USB 调试。
2. 连接电脑，授权。
3. 验证：`adb devices`。
4. 装包：`./gradlew installDebug`。

## 8. 验证 hello world

### 8.1 主控调用 APK

```bash
# CLI 在 backend 容器内跑
docker compose exec backend python -m matrix.cli test_hello_world
```

预期输出：

```
[OK] 主控启动
[OK] PostgreSQL 连接
[OK] Tailscale 在线
[OK] APK 发现 (tailnet_ip: 100.64.0.2)
[OK] HMAC 鉴权通过
[OK] device_status 返回 {online: true, ...}
[SUCCESS] hello world
```

### 8.2 端到端发布测试

```bash
docker compose exec backend python -m matrix.cli test_publish_e2e
```

预期：从知识库生成一篇测试笔记 → 调度 → APK 发布 → 拿到 platform_note_id。

## 9. IDE 配置

### 9.1 VS Code

推荐插件：
- Python
- Pylance
- Even Better TOML
- SQLTools（PostgreSQL）

`settings.json`（host 端没有 .venv；Python 调试走 docker attach）：

```json
{
  "python.defaultInterpreterPath": null,
  "python.testing.pytestEnabled": false,
  "[python]": {
    "editor.formatOnSave": true,
    "editor.defaultFormatter": "ms-python.black-formatter"
  }
}
```

调试 Python 时用：

```bash
docker compose exec backend python -m pdb -m matrix.cli test_hello_world
```

或挂 VS Code Dev Containers 扩展连接到 `matrix-backend` 容器。

### 9.2 PyCharm

- Interpreter：选 `.venv/bin/python`
- Database：连 localhost:5432/matrix
- Run Configurations：导入 `.run/` 目录

## 10. 常见问题

### 10.1 PostgreSQL 启动失败

```bash
# 检查端口占用
lsof -i :5432
# 或 Windows
netstat -ano | findstr :5432

# 删除旧数据卷
docker compose down -v
docker compose up -d postgres
```

### 10.2 Tailscale 无法连接

```bash
# 检查 Tailscale 状态
tailscale status

# 重启 Tailscale
sudo tailscale down
sudo tailscale up
```

### 10.3 APK 无法发现

1. 确认手机与主控在同一 tailnet。
2. 确认 APK 的 `MainActivity` 启动成功（logcat）。
3. 确认主控配置中设备 tailnet IP 正确。

### 10.4 LLM 调用失败

1. 检查 `.env` 中 API key 是否正确。
2. 检查网络（curl 到 LLM provider）。
3. 查看 `~/.matrix/logs/` 日志。

## 11. 下一步

- 阅读 [architecture/SDD.md](../architecture/SDD.md) 了解系统设计
- 阅读 [api/](../api/) 了解接口规范
- 跑通 `docker compose run --rm test` 验证单元测试（在容器内）
- 加入一个 issue 开始编码
