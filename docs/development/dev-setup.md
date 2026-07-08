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
| Docker | 24+ | PostgreSQL / Headscale 容器化 |
| Docker Compose | v2 | 多容器编排 |
| Node.js | 20+ | 前端构建（如用 Tauri-Web） |
| pnpm | 8+ | 前端包管理（推荐） |

### 1.2 Python 后端

| 工具 | 版本 | 备注 |
|---|---|---|
| Python | 3.11+ | pyenv 管理 |
| Poetry | 1.7+ | 依赖管理 |
| Alembic | latest | 数据库迁移 |

### 1.3 Tauri Shell

| 工具 | 版本 | 备注 |
|---|---|---|
| Rust | 1.75+ | rustup 管理 |
| Tauri CLI | 1.5+ | `cargo install tauri-cli` |
| WebView2 | latest | Windows 专用 |

### 1.4 APK 开发

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

# 创建 Python 虚拟环境
python3.11 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 安装 Python 依赖
cd backend
poetry install

# 复制环境变量模板
cp .env.example .env
# 编辑 .env 填入 LLM API key 等
```

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

### 3.2 应用迁移

```bash
cd backend
alembic upgrade head

# 验证
psql -h localhost -U matrix -d matrix -c "\dt"
```

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

## 5. 启动 Python 后端

```bash
cd backend
source ../.venv/bin/activate

# 开发模式（自动 reload）
uvicorn matrix.api.app:app --reload --port 8666

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

## 6. 启动 Tauri Shell

```bash
cd shell
npm install
npm run tauri dev
```

首次启动会：
1. 检查 Python 后端是否在 8666 端口监听
2. 未监听则报错并提示启动 Python 后端
3. 启动后打开桌面 UI

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
# 在主控端
cd backend
python -m matrix.cli test_hello_world
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
python -m matrix.cli test_publish_e2e
```

预期：从知识库生成一篇测试笔记 → 调度 → APK 发布 → 拿到 platform_note_id。

## 9. IDE 配置

### 9.1 VS Code

推荐插件：
- Python
- Pylance
- Rust Analyzer
- Tauri
- Even Better TOML
- SQLTools（PostgreSQL）

`settings.json`：

```json
{
  "python.defaultInterpreterPath": ".venv/bin/python",
  "python.testing.pytestEnabled": true,
  "rust-analyzer.cargo.features": "all",
  "[python]": {
    "editor.formatOnSave": true,
    "editor.defaultFormatter": "ms-python.black-formatter"
  }
}
```

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
- 跑通 `pytest backend/tests/` 验证单元测试
- 加入一个 issue 开始编码
