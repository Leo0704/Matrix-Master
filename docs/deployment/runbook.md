# 部署 Runbook

| 项 | 内容 |
|---|---|
| 适用对象 | 运维 / 部署 |
| 配套 | [architecture/SDD.md](../architecture/SDD.md) / [release-process.md](./release-process.md) / [monitoring-runbook.md](../operations/monitoring-runbook.md) |

## 1. 部署架构

```
┌─────────────────────────────────────────────┐
│  VPS（自托管）                               │
│  - Headscale 控制面                          │
│  - 自建 DERP 中继                            │
│  - 监控（Prometheus / Grafana）               │
│  - 备份（OSS / S3）                          │
└─────────────────────────────────────────────┘
                    ↑
                    │ Tailscale mesh
                    │
┌───────────────────┴────────────────────────┐
│ 主控（macOS / Windows / Linux，开发机）       │
│  - Web frontend（浏览器访问 http://localhost:1420） │
│  - Python 后端                               │
│  - PostgreSQL（本地）                        │
│  - Tailscale 客户端                         │
└─────────────────────────────────────────────┘
                    ↑
                    │ Tailscale mesh + 蜂窝数据
                    │
       ┌────────────┴────────────┐
       │                         │
┌──────┴──────┐         ┌────────┴─────┐
│ 手机① APK   │         │ 手机⑩ APK    │
└─────────────┘         └──────────────┘
```

## 2. 初次部署

### 2.1 准备 VPS

**最低配置**：
- 2 vCPU / 4GB RAM / 40GB SSD
- 公网 IP（必备）
- 域名（推荐，DERP 用）

**推荐供应商**：阿里云 / 腾讯云 / AWS（任选）

**基础配置**：
```bash
# 更新系统
apt update && apt upgrade -y  # Ubuntu
# 或
yum update -y  # CentOS

# 安装 Docker
curl -fsSL https://get.docker.com | sh
systemctl enable docker
systemctl start docker

# 安装 Docker Compose
apt install docker-compose-plugin  # Ubuntu
# 或
yum install docker-compose-plugin  # CentOS

# 配置防火墙
ufw allow 22/tcp      # SSH
ufw allow 80/tcp      # HTTP（Headscale Web）
ufw allow 443/tcp     # HTTPS
ufw allow 3478/udp    # STUN（DERP）
# 限制 SSH 来源 IP
ufw enable

# 创建非 root 用户
adduser deploy
usermod -aG docker deploy
```

### 2.2 部署 Headscale

```bash
# 克隆配置仓库
git clone <config-repo> /opt/matrix-infra
cd /opt/matrix-infra

# 创建 Headscale 配置
mkdir -p headscale
cat > headscale/config.yaml <<'EOF'
server_url: https://hs.example.com
listen_addr: 0.0.0.0:443
metrics_listen_addr: 0.0.0.0:9090

private_key: <生成>
noise:
  private_key: <生成>

database:
  type: sqlite
  sqlite:
    path: /var/lib/headscale/db.sqlite

dns:
  magic_dns: true
  base_domain: matrix.local

policy:
  path: /etc/headscale/policy.hujson

derp:
  urls: []
  auto_update_enabled: false
  server:
    enabled: true
    region_id: 999
    region_code: "self"
    region_name: "Self-hosted DERP"
    stun_listen_addr: "0.0.0.0:3478"
    private_key: <生成>
EOF

# 启动 Headscale
docker compose up -d headscale

# 验证
docker logs headscale
curl https://hs.example.com/health
```

### 2.3 部署自建 DERP

如果 DERP 与 Headscale 同机：
- 配置见上一步 `derp.server` 段

如果分离部署：
```yaml
# derp/docker-compose.yml
services:
  derp:
    image: ghcr.io/tailscale/derper:latest
    command: /derper --hostname=derp.example.com --verify-clients=false --certmode=letsencrypt --certdir=/var/lib/derper/certs
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
      - "3478:3478/udp"
    volumes:
      - /var/lib/derper:/var/lib/derper
    environment:
      - DERP_DOMAIN=derp.example.com
```

### 2.4 配置 DNS

```
hs.example.com     A    <VPS_IP>
derp.example.com   A    <VPS_IP 或 DERP_IP>
```

### 2.5 申请 SSL 证书

用 Let's Encrypt：
```bash
# 安装 certbot
apt install certbot

# 申请证书
certbot certonly --standalone -d hs.example.com
certbot certonly --standalone -d derp.example.com

# 复制到 Headscale
cp /etc/letsencrypt/live/hs.example.com/* /opt/matrix-infra/headscale/certs/

# 配置自动续期
cat > /etc/cron.d/certbot-renew <<'EOF'
0 3 * * * certbot renew --quiet --deploy-hook "cd /opt/matrix-infra && docker compose restart headscale"
EOF
```

### 2.6 注册用户

```bash
# 创建用户
docker exec headscale headscale users create default

# 为主控创建 preauth key
docker exec headscale headscale preauthkeys create --user default --reusable --expiration 30d
```

### 2.7 部署主控

详见主控安装包（PyInstaller 打包的 `.app` / `.exe`）。

安装包会引导：
1. 复制到 Applications（macOS）/ Program Files（Windows）
2. 检测 Python 后端（首次启动自动 spawn）
3. 自动建库
4. Headscale 登录（输入 preauth key）
5. 显示主界面

### 2.8 配置主控

主控配置文件 `~/.matrix/config.yaml`：

```yaml
headscale:
  url: https://hs.example.com
  preauth_key: <粘贴>

llm:
  anthropic:
    api_key: sk-...
    default_model: claude-sonnet-4-5
  openai:
    api_key: sk-...
    default_embedding: text-embedding-3-small

database:
  url: postgresql://matrix:matrix_dev@localhost/matrix

scheduler:
  per_account_daily:
    publish: 3
    interact: 20
  active_hours: "09:00-23:00"
  jitter_sigma: 0.5

monitoring:
  otel_endpoint: http://localhost:4317
  metrics_port: 9091
```

### 2.9 部署手机端

1. 装 Tailscale app，用 preauth key 登录。
2. 装 companion APK。
3. 启动 companion → 配对 → 加入设备。

## 3. 日常运维

### 3.1 启动 / 停止

```bash
# 启动所有
cd /opt/matrix-infra
docker compose up -d

# 停止
docker compose down

# 重启单个服务
docker compose restart headscale
```

### 3.2 备份

```bash
# 数据库备份（每日 cron）
0 2 * * * /opt/matrix-infra/backup/run.sh

# backup/run.sh
#!/bin/bash
set -e
BACKUP_DIR=/var/backups/matrix
mkdir -p $BACKUP_DIR
TS=$(date +%Y%m%d_%H%M%S)

# PostgreSQL 备份
docker exec postgres pg_dump -U matrix matrix | gzip > $BACKUP_DIR/db_$TS.sql.gz

# 加密
gpg --symmetric --cipher-algo AES256 $BACKUP_DIR/db_$TS.sql.gz

# 传到 OSS
ossutil cp $BACKUP_DIR/db_$TS.sql.gz.gpg oss://matrix-backups/db/

# 清理本地旧备份
find $BACKUP_DIR -name "*.gz.gpg" -mtime +7 -delete
```

### 3.3 恢复

```bash
# 1. 拉备份
ossutil cp oss://matrix-backups/db/db_20260708.sql.gz.gpg /tmp/

# 2. 解密
gpg -d /tmp/db_20260708.sql.gz.gpg > /tmp/db_20260708.sql.gz

# 3. 解压
gunzip /tmp/db_20260708.sql.gz

# 4. 恢复
docker exec -i postgres psql -U matrix matrix < /tmp/db_20260708.sql
```

### 3.4 升级 Headscale

```bash
# 1. 备份配置
cp -r headscale/config.yaml headscale/config.yaml.bak

# 2. 拉新镜像
docker compose pull headscale

# 3. 看 release notes
# 4. 重启
docker compose up -d headscale

# 5. 验证
docker logs headscale | head -50
```

### 3.5 扩容（多 DERP 节点）

```yaml
# 增加 DERP
derp:
  server:
    enabled: true
    region_id: 999
  urls:
    - https://derp2.example.com
```

## 4. 故障恢复

### 4.1 VPS 不可用

1. 启动备用 VPS（预先准备镜像）。
2. 更新 DNS 指向新 VPS。
3. 备份 Headscale 数据库（`/var/lib/headscale/db.sqlite`）。
4. 主控重新登录。
5. 通知运营者：可能 1-2 小时不可用。

### 4.2 数据库损坏

1. 停止主控。
2. 拉最近备份恢复（见 §3.3）。
3. 启动主控，验证数据完整性。
4. 评估数据丢失量（24h 内）。

### 4.3 APK 大量掉线

1. 检查 Headscale 健康。
2. 检查 DERP 状态。
3. 临时方案：所有 APK 切换到直连主控（需要主控有公网 IP，且加防火墙规则）。
4. 长期方案：多 DERP + 多 VPS 冗余。

## 5. 容量规划

详见 [planning/capacity-plan.md](../planning/capacity-plan.md)。

## 6. 安全

详见 [architecture/threat-model.md](../architecture/threat-model.md)。

要点：
- VPS 启用防火墙，仅开放必要端口
- Headscale 数据库权限限制
- SSL 证书自动续期
- 备份加密
- SSH 密钥登录，禁用密码

## 7. 监控部署

```bash
# Prometheus + Grafana
docker compose up -d prometheus grafana

# Prometheus 配置（scrape 主控 metrics）
cat > prometheus/prometheus.yml <<'EOF'
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'master'
    static_configs:
      - targets: ['host.docker.internal:9091']
EOF
```

Grafana 默认账号 admin/admin，首次登录改密。

预置 dashboard 见 [monitoring-runbook.md §5](../operations/monitoring-runbook.md)。

## 8. 巡检清单（每日）

- [ ] Headscale 健康
- [ ] DERP 可达
- [ ] VPS 磁盘 / 内存
- [ ] 数据库备份成功
- [ ] SSL 证书有效
- [ ] 监控告警无未处理
