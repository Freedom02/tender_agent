# tender_agent 共享开发库（Docker + PostgreSQL 16）

开发阶段你和伙伴共用一个 PostgreSQL 实例。本目录是开箱即用的 Compose 配置。

**先读这句**：Docker 容器跑在谁的电脑上，数据库就是谁的**本机资源**。让伙伴连上，靠的是**网络方案**（局域网 / 虚拟组网 / 云主机），不是 Docker 配置。请先看第 4 节再决定。

---

## 1. 前置：先把 WSL2 装上（Windows 家庭版必做）

本机实测：Docker Desktop 已装，但 **WSL2 未安装**，守护进程起不来（`docker version` 只有 Client 段）。
家庭版没有 Hyper-V，Docker Desktop 只能用 WSL2 后端。

管理员 PowerShell：

```powershell
wsl --install --no-distribution    # 只装 WSL2 内核，不拉 Ubuntu 发行版，省几 GB
# 然后重启电脑
```

重启后启动 **Docker Desktop**（开始菜单搜一下），等托盘图标变成运行中，再验证：

```powershell
docker version          # 必须能看到 Server: 段
docker-compose version  # 本机实测用连字符版（docker compose 插件不可用）
```

> 可选：限制 WSL2 占用内存。新建 `C:\Users\乔远\.wslconfig`：
> ```ini
> [wsl2]
> memory=8GB
> processors=8
> swap=4GB
> ```

---

## 2. 启动数据库

```powershell
cd <本目录>
Copy-Item .env.example .env
notepad .env          # 改 POSTGRES_PASSWORD；要给伙伴连就改 PG_BIND=0.0.0.0

docker-compose up -d
docker-compose ps                 # STATUS 应显示 healthy
docker-compose logs -f postgres   # 看首次初始化日志（建表）
```

首次启动时官方镜像会自动执行 `init/` 下的脚本：建 16 张表；`EXTRA_DATABASES` 里列的库也会一并建好并套用同一套结构。

**`init/` 只在数据卷为空时执行一次**，之后改表结构不会重跑（见第 6 节）。

## 3. 验证连接

```powershell
# 容器内自检
docker-compose exec postgres psql -U tender_app -d tender_agent_dev -c "\dt"

# 从 Windows 宿主连（需要 psql 客户端；或直接用 pgAdmin / DBeaver）
docker-compose exec postgres psql -U tender_app -d tender_agent_dev -c "SELECT version();"
```

连接串（应用/客户端都用这个）：

```
postgresql://tender_app:你的密码@127.0.0.1:5432/tender_agent_dev
```

---

## 4. 让伙伴连上：四种方案（**这一步才是关键**）

### 方案对照

| 方案 | 适用场景 | 优点 | 代价 |
|---|---|---|---|
| **A. 局域网直连** | 你和伙伴**同一办公室/同一 WiFi** | 零成本、延迟最低 | 你的电脑关机/休眠库就没了；IP 可能变；只限内网 |
| **B. Tailscale 虚拟组网**（异地协作首选） | 伙伴在异地 | 免公网 IP、免端口转发、端到端加密、个人版免费 | 双方各装一个客户端 |
| **C. 云主机部署**（团队长期共用首选） | 需要 7×24 在线 | 不依赖任何人的电脑、稳定 IP | 有月费、要维护 |
| **D. 直接暴露公网 5432** | — | — | **绝对不要**，PostgreSQL 开放到公网，几小时内就会被爆破扫描 |

### 方案 A：局域网直连

```powershell
# 1) .env 里改 PG_BIND=0.0.0.0，然后重启
docker-compose up -d

# 2) 放行防火墙（管理员 PowerShell）——建议限定网段，别全网放行
netsh advfirewall firewall add rule name="tender-pg 5432" dir=in action=allow protocol=TCP localport=5432 remoteip=192.168.0.0/16

# 3) 查你的局域网 IP
ipconfig | findstr IPv4
```

伙伴用 `postgresql://tender_app:密码@<你的局域网IP>:5432/tender_agent_dev`。

注意：Windows 家用宽带分配的 IP 会变，建议在路由器里给你的机器绑定静态 IP（DHCP 保留）。

### 方案 B：Tailscale（推荐用于异地）

1. 你和伙伴都装 Tailscale 并登录同一 tailnet（免费额度够小团队）；
2. 你的机器会拿到一个 `100.x.x.x` 的虚拟 IP；
3. 保持 `PG_BIND=0.0.0.0`（或更严格：只绑定 Tailscale 网卡）；
4. 伙伴用 `postgresql://tender_app:密码@100.x.x.x:5432/tender_agent_dev`。

隧道已加密，不需要端口转发，也不需要公网 IP。

### 方案 C：云主机（如果这是团队长期要用的库）

在云主机上装 Docker，把本目录 `scp` 上去，`docker-compose up -d`，安全组只放行**你们团队的 IP 或 VPN 网段**。好处是**不依赖你个人电脑开机**——开发期大家随时能连，这一点在协作里通常是刚需。

---

## 5. 数据从本地 JSON 搬进来

用上一级目录的迁移脚本，把 DSN 指向容器：

```powershell
cd ..    # 回到 tender-agent-migration

# 先 dry-run 看源数据计数
py migrate_to_db.py --dry-run

# 迁入共享库
py migrate_to_db.py --dsn "postgresql://tender_app:密码@127.0.0.1:5432/tender_agent_dev"
```

> 库里的表已由 `init/01-schema.sql` 建好，脚本只负责灌数据（它不建表）。

---

## 6. 协作规范（多人共用必须约定，否则很快会乱）

### 6.1 一人一库，别共用一张表
开发期最大的摩擦是"我加的记录被你删了"。两种做法：

- **推荐**：`.env` 里 `EXTRA_DATABASES=tender_dev_alice,tender_dev_bob`，一人一个库，结构相同、数据隔离；
- 或者统一用 `tender_agent_dev` 放**公共/演示数据**，各自另建库做实验。

### 6.2 表结构变更必须走迁移文件
`init/` 只跑一次，之后改表不会再自动生效。约定：

```
migrations/
  0001_init.sql              （= init/01-schema.sql）
  0002_add_xxx_column.sql
  0003_create_yyy_table.sql
```

规则：**改结构 = 新增一个编号文件，不改老的**。谁改谁在群里说一声，另一方拉代码后执行：

```powershell
docker-compose exec -T postgres psql -U tender_app -d tender_agent_dev -f - < migrations/0002_add_xxx_column.sql
```

每个文件用 `BEGIN; ... COMMIT;` 包住，保证要么全成要么全不成。

### 6.3 别把 .env 提交进仓库
`.env` 里有密码，加入 `.gitignore`。仓库里只放 `.env.example`。

### 6.4 重置与备份

```powershell
# 备份（自定义格式，可 pg_restore）
docker-compose exec -T postgres pg_dump -U tender_app -Fc tender_agent_dev > backup_dev.dump

# 恢复
docker-compose exec -T postgres pg_restore -U tender_app -d tender_agent_dev --clean < backup_dev.dump

# 彻底重置（!! 会删除所有数据，仅限开发库）
docker-compose down -v && docker-compose up -d
```

---

## 7. 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| `failed to connect to the docker API at npipe://...` | Docker Desktop 没启动，或 WSL2 没装（见第 1 节） |
| `docker: unknown command: docker compose` | 本机 compose 插件不可用，改用 `docker-compose`（连字符） |
| 容器起来但伙伴连不上 | 检查 `PG_BIND` 是否为 `0.0.0.0`、防火墙规则、是否在同一网段/同一 tailnet |
| 中文变问号 | 库必须是 UTF8 编码；本配置用 `POSTGRES_INITDB_ARGS=--encoding=UTF8`，**已初始化的旧卷改了参数也不会变**，需 `down -v` 重建 |
| 改了 `.env` 不生效 | 需要 `docker-compose up -d` 重建容器（`restart` 不会重读 .env） |
| 端口 5432 被占 | 改 `.env` 的 `PG_PORT`，注意容器内始终是 5432 |

> 补充：Docker Desktop 对**大企业**商用需付费订阅（员工数/营收超阈值）。若公司规模触发，可改为在 WSL2 里直接装 Docker Engine（不装 Desktop），或直接走方案 C 的云主机。
