# 🚀 RemnaShop-Pro

当前版本：`V3.8.1`

RemnaShop-Pro 是一个面向 **Remnawave 面板** 的 Telegram 机器人，提供订阅售卖、续费、状态查询与基础运维能力。

当前适配目标为 **Remnawave Panel 3.4.4**，对应官方
`@remnawave/backend-contract 3.4.15`。接口来源、完整调用清单和旧数据升级规则
分别见 [契约来源](docs/remnawave-contract-source.md)、
[完整 OpenAPI 差异](docs/remnawave-openapi-diff.md)、
[API 清单](docs/remnawave-api-inventory.md)、
[3.x 迁移说明](docs/remnawave-v3-migration.md) 和
[V3.7 RC 验收步骤](docs/v3.7-rc-runbook.md)、
[人工 UI 清单](docs/v3.7-rc-ui-checklist.md) 和
[V3.8 发布说明](docs/releases/v3.8.md) 和
[V3.8.1 发布说明](docs/releases/v3.8.1.md)。

---

## 功能

### 用户端
- 购买新订阅（选择套餐并提交付款信息）
- 我的订阅 / 续费
- 订阅详情查看（到期时间、状态、流量使用）
- 订阅链接二维码生成
- 查看和清理本人 HWID 设备、重置订阅凭据；重置后展示面板返回的最新链接和二维码
- 已有订阅续费用官方 Extend Action；结果不确定时订单进入人工核对状态，禁止重复执行
- 节点状态查询
- 联系客服

### 管理端
- 套餐管理（新增、查看、删除）
- 用户列表与订阅管理（查看、删除、重置流量、重置策略）
- 订单审核（通过 / 拒绝）
- 批量续期、批量撤销订阅（选定数值用户 ID、执行前确认）
- HWID 设备管理与统计、节点 GeoCheck、系统 Dashboard
- 面板用户 ID / shortUuid / 用户名 Resolve 检索和 Panel Tags 查看
- 到期提醒天数设置
- 过期清理天数设置
- 异常检测阈值与检测周期设置

---

## 部署要求

> 本仓库 **仅支持 Docker Compose 部署**，不再支持 systemd / 纯 Python / 服务器裸装方式。

- Linux 服务器（推荐 Debian / Ubuntu）
- 网络可访问 GitHub 与 Docker 镜像仓库
- 以可提权用户执行（root 或具备 sudo 权限）
- 生产镜像使用 Python 3.11.16

---

## 一键安装（唯一推荐方式）

```bash
curl -fsSL https://raw.githubusercontent.com/ike-sh/RemnaShop-Pro/main/bootstrap.sh | bash
```

该命令会执行仓库内 `bootstrap.sh`，自动完成：

1. 检查并自动安装通用基础依赖（缺失时）：`curl`、`ca-certificates`、`git`、`bash`、`tar`、`gzip`、`unzip`、`jq`、`sed`、`grep`、`awk`、`coreutils`
2. 检查并安装 Docker（缺失时）
3. 检查并安装 Docker Compose 插件（缺失时）
4. 克隆/更新仓库到 `/opt/remnashop-pro`
5. 若 `.env` 不存在则基于 `.env.example` 自动创建
6. 使用中文交互收集必填 `ADMIN_ID` 与 `BOT_TOKEN`（已有值可选择保留或替换），并自动写入 `.env`
7. 启动 Docker Compose 栈
8. 等待 `remnashop` 容器健康状态变为 `healthy` 后才报告安装成功

已有安装通过同一个 `bootstrap.sh install` 入口安全更新。若安装目录非空但不是
已验证的项目仓库，脚本会停止并保留目录内容。自定义 `INSTALL_DIR` 时，目录名仍须为
`remnashop-pro` 或 `RemnaShop-Pro`。

示例交互（节选）：

```text
[remnashop-bootstrap] 正在配置必填环境变量（仅 ADMIN_ID 与 BOT_TOKEN）。
请输入 ADMIN_ID:
请输入 BOT_TOKEN:
```

> `bootstrap.sh` 支持两种模式：
> - 交互菜单（直接执行 `bash bootstrap.sh`）
> - 非交互参数：`bash bootstrap.sh install` / `bash bootstrap.sh uninstall`

---

## 卸载（仅移除 RemnaShop-Pro 资源）

### 非交互卸载（服务器本地脚本）

```bash
cd /opt/remnashop-pro
bash bootstrap.sh uninstall
```

### 远程一行卸载（不经过菜单）

```bash
curl -fsSL https://raw.githubusercontent.com/ike-sh/RemnaShop-Pro/main/bootstrap.sh | bash -s -- uninstall
```

卸载会**仅**清理以下 RemnaShop-Pro 资源：
- Compose 项目 `remnashop`
- 该项目创建的容器
- 该项目创建的本地镜像（`--rmi local`）
- 该项目创建的卷（`-v`）
- 项目目录 `/opt/remnashop-pro`

不会触碰其他 Compose 项目或无关 Docker 资源。

> 卸载安全行为：
> - 检测到交互终端时，必须手动确认才会执行删除（支持 `YES` / `yes` / `Y` / `y`）；
> - 非交互场景会跳过确认，但仅在安装目录通过仓库校验后处理该项目资源；
> - 若安装目录不存在，脚本不删除任何 Docker 资源；若目录存在但校验不通过，脚本报错退出。

---

## 安装成功判定与失败排查

安装脚本的**成功判定**不是 `docker compose up -d` 返回成功，而是：

- `remnashop` 容器实际进入 `health=healthy` 状态。

若出现以下情况会直接判定安装失败并给出日志排查提示：
- 容器退出（`exited/dead`）
- 健康检查 `unhealthy`
- 容器持续重启（`restarting` 或重启次数异常增长）
- 在超时时间内未进入 `healthy`

手动复核命令：

```bash
cd /opt/remnashop-pro
docker --version
docker compose version
test -f .env && echo ".env exists"
docker compose -p remnashop ps
docker compose logs --tail=100 remnashop
```

预期结果：
- `docker --version` 能输出版本号
- `docker compose version` 能输出版本号
- `.env exists` 输出成功
- `docker compose -p remnashop ps` 显示 `remnashop` 服务为 `running` 且健康检查最终为 `healthy`
- 日志中无持续崩溃重启

容器健康检查验证本地配置与 SQLite 完整性。它不以外部 Panel 或 Telegram
网络连接作为 Docker 健康判定；外部集成状态需另行查看运行日志和管理页面。

---

## 日常运维（Docker Compose）

```bash
cd /opt/remnashop-pro
./docker-manage.sh ps
./docker-manage.sh logs
./docker-manage.sh restart
./docker-manage.sh down
```

`docker-manage.sh up/update` 均调用唯一的安装入口 `bootstrap.sh install`。
升级生产实例前请备份 `remnashop-data` 数据卷；备份中包含 Secret 和用户数据。

### V3.6 → V3.7 升级前备份

先在旧实例上停止本项目服务，再将数据卷复制到**项目目录之外**的新目录。
以下命令使用旧版已有的 Python 镜像，不依赖尚未安装的 V3.7 备份工具。
服务停止期间不得运行其他写入该数据库的实例；任一步失败时停止后续升级：

```bash
cd /opt/remnashop-pro
set -e
docker volume inspect remnashop_remnashop-data >/dev/null
docker compose -p remnashop stop remnashop
test ! -e /opt/remnashop-backups/pre-v3.7
mkdir -p -m 700 /opt/remnashop-backups/pre-v3.7
docker run --rm -v remnashop_remnashop-data:/from:ro -v /opt/remnashop-backups/pre-v3.7:/to --entrypoint sh remnashop-remnashop:latest -c 'test -f /from/config.json && test -f /from/starlight.db && cp -a /from/. /to/'
docker run --rm -v /opt/remnashop-backups/pre-v3.7:/snapshot --entrypoint python remnashop-remnashop -c "import json,sqlite3; from pathlib import Path; p=Path('/snapshot'); assert (p/'config.json').is_file() and (p/'starlight.db').is_file(); c=json.loads((p/'config.json').read_text()); db=sqlite3.connect(p/'starlight.db'); assert c.get('admin_id') and c.get('bot_token') and db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'; db.close(); print('backup verified')"
```

只有校验通过后才继续升级。若暂不升级，使用
`docker compose -p remnashop start remnashop` 恢复原服务。V3.7 的
`docker-manage.sh backup` 会要求服务已停止、备份目录为空且位于仓库外，并自动
验证配置文件与 SQLite 完整性。再次备份需指定另一个空目录。

恢复 V3.7 备份会覆盖项目数据，且只能在服务停止后执行：

```bash
cd /opt/remnashop-pro
docker compose -p remnashop stop remnashop
REMNASHOP_RESTORE_CONFIRM=YES BACKUP_DIR=/opt/remnashop-backups/pre-v3.7 ./docker-manage.sh restore
docker compose -p remnashop start remnashop
docker compose -p remnashop ps
```

不要将备份放在 `/opt/remnashop-pro` 内；项目卸载会删除该目录。恢复后仍需检查
容器健康与旧订阅迁移状态。

---

## 环境变量说明

首次安装会自动从 `.env.example` 生成 `.env`（若不存在），并在安装过程中交互收集并写入以下必填项：

- `ADMIN_ID`
- `BOT_TOKEN`

`.env.example` 中这两个字段默认是空值（`ADMIN_ID=`、`BOT_TOKEN=`），用于确保首次安装必须由管理员输入真实值。

若 `.env` 已存在，脚本会询问你“保留还是替换”，但不会显示现有 `BOT_TOKEN` 内容，
输入新 Token 时也不会在终端回显。
若 `.env` 来自模板且 `ADMIN_ID` / `BOT_TOKEN` 为空，脚本会直接要求输入新值，不会询问“是否保留空值”。
脚本会校验 `ADMIN_ID` 与 `BOT_TOKEN` 的基本格式。交互输入使用 `/dev/tty`；
无 TTY 时，只有 `.env` 已有有效必填值才能继续。

以下变量在安装阶段均为可选，不会阻塞部署；可后续在机器人/应用内配置：

- `PANEL_URL`
- `PANEL_TOKEN`
- `SUB_DOMAIN`
- `GROUP_UUID`
- `PANEL_VERIFY_TLS`

面板地址和 Token 未配置时，面板功能不可用；新购发货还需要配置默认内部组
`GROUP_UUID`。安装容器本身不要求这些可选值。

已有 `config.json` 时，`.env` 中非空的 `ADMIN_ID`、`BOT_TOKEN`、
`PANEL_TOKEN` 在容器启动时优先。空 `PANEL_TOKEN` 允许管理员在机器人中配置；
其他可选面板参数仅在首次生成配置时由环境变量初始化，随后保留后台修改。

## 生产镜像构建说明

生产镜像通过 `.dockerignore` 排除非运行时文件，不会打包以下内容：

- `tests/`
- `docs/`
- `README.md`
- `AGENTS.md`
- `LICENSE`
- `.gitignore`
- `.env.example`

---

## 迁移说明（旧版 standalone / server-install 用户）

旧版 `install.sh + systemd(remnashop.service)` 部署流已移除。

迁移步骤：

1. 备份旧机器中的 `config.json` 与 `starlight.db`
2. 执行新的一键安装命令（见上）
3. 将备份数据恢复到 Docker 数据卷（或容器 `/data`）
4. 使用 `docker compose ps` 与日志确认服务正常

> 若旧机器仍在运行 `remnashop.service`，请先停用旧服务再切换，避免重复实例同时运行。

### 从 Remnawave 2.x 升级到 3.4.4

升级前备份 `remnashop-data` 数据卷。旧 SQLite 中的用户 UUID 会完整保留；
新版以数值用户 ID 调用面板。只有 Telegram ID 对应关系唯一时才自动绑定；
其他记录显示为“待迁移”或“有歧义”，需要管理员按
[迁移说明](docs/remnawave-v3-migration.md)核对绑定。未绑定的旧订阅不会被
自动重建、续费或删除。旧版本机器人无法继续调用 3.4.4 已移除的 UUID 路由，
不要把回滚机器人代码当作可直接恢复兼容性的办法。

---

## 项目结构

- `bot.py`：主程序入口
- `docker-compose.yml`：唯一部署编排入口
- `bootstrap.sh`：一键安装引导脚本（用于 curl | bash）
- `docker-manage.sh`：Docker Compose 运维助手
- `handlers/`：消息与回调处理辅助代码
- `services/`：面板 API、订单相关服务代码
- `storage/`：数据库初始化与访问辅助代码
- `jobs/`：定时任务辅助代码
- `utils/`：通用工具函数

---

## 联系

- 作者：ike
- 群组：https://t.me/Remnawarecn

---

本项目仅供学习交流使用，请遵守当地法律法规。
