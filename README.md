# 消息已读统计工具

一个简单的「写消息 -> 生成链接和二维码 -> 监控对方是否打开」的小工具。

- **前端**：React + Vite + TypeScript。表单提交后渲染二维码，并轮询已读状态。
- **后端**：Python 标准库 HTTP 服务 + Redis。消息和已读状态写入 Redis，90 天后自动过期。
- **安卓端**：Kotlin + Jetpack Compose。页面与前端一致，并在本机持久化自己发送过的 uuid 与发送时间。

## 目录结构

```text
.
├── backend/        # Python 后端
├── frontend/       # React + Vite 前端
├── app/            # Android Compose App
├── docker-compose.yml
└── README.md
```

## 快速启动（推荐）

使用 Docker Compose 同时启动 Redis、后端、前端和 Nginx；前端独立构建为单独 service：

```powershell
docker compose up --build
```

未配置证书时，容器内后端以 HTTP 监听 `:4000`，用于本地访问或作为 HTTPS 反向代理的 upstream。生产环境请通过反向代理或后端 TLS 证书对外提供 HTTPS。

如需让手机或公网用户打开链接，可以让反向代理传递 `X-Forwarded-Proto` / `X-Forwarded-Host`，或设置 `PUBLIC_BASE_URL` 为对方能访问的 HTTPS 地址，例如：

```yaml
PUBLIC_BASE_URL: "https://example.com"
```

## 本地开发

### 1. 安装前端依赖

```powershell
cd frontend
npm install
```

### 2. 启动 Redis

```powershell
docker compose up redis
```

### 3. 安装 Python 依赖

```powershell
pip install -r backend/requirements.txt
```

### 4. 启动后端

```powershell
$env:REDIS_ADDR = "localhost:6379"
python backend/main.py
```

如果要让 Python 后端直接提供 HTTPS，请设置证书路径：

```powershell
$env:TLS_CERT_FILE = "C:\certs\fullchain.pem"
$env:TLS_KEY_FILE = "C:\certs\privkey.pem"
```

如果不提供证书和私钥，后端仍以 HTTP 监听，适合放在 Nginx/Caddy/负载均衡后面做 HTTP upstream 转发。反向代理应传递 `X-Forwarded-Proto` 和 `X-Forwarded-Host`，后端会据此生成 HTTPS 的 `openUrl` / `statusUrl`；也可以设置 `PUBLIC_BASE_URL` 为公网 HTTPS 地址来显式覆盖。

### 5. 启动前端

```powershell
cd frontend
npm run dev
```

打开 [http://localhost:5173](http://localhost:5173)。开发前端会默认通过 HTTPS 调用后端接口。

如果后端不在默认地址，需要在 `frontend/.env.local` 中配置：

```text
VITE_API_BASE=https://localhost:4000
```

## 环境变量

| 变量 | 作用 | 默认 |
|------|------|------|
| `PORT` | 后端监听端口 | `4000` |
| `PUBLIC_BASE_URL` | 生成 `openUrl` / `statusUrl` 的根地址；未设置时从 `Host` / `X-Forwarded-*` 请求头推断 | 空 |
| `TLS_CERT_FILE` | 后端直接启用 HTTPS 时使用的证书文件 | 空 |
| `TLS_KEY_FILE` | 后端直接启用 HTTPS 时使用的私钥文件 | 空 |
| `REDIS_ADDR` | Redis 地址 | `localhost:6379` |
| `REDIS_PASSWORD` | Redis 密码 | 空 |
| `REDIS_DB` | Redis DB | `0` |
| `VITE_API_BASE` | 前端开发时调用的 API 根地址 | 开发：`https://localhost:4000`；生产：同源 |

## Redis 数据

消息存储为 Redis Hash：

```text
message:{uuid}
```

字段：

- `id`
- `toName`
- `body`
- `createdAt`
- `readAt`，空字符串表示未读
- `ownerVisitorID`，首次打开该链接的浏览器 cookie ID

每条消息创建时设置 90 天 TTL。更新已读状态不会延长过期时间。

## 已读逻辑

1. 创建消息后，后端返回 `openUrl` 和 `statusUrl`。
2. 第一个访问 `openUrl` 的浏览器会被记录为本人，只写入 `ownerVisitorID`，不会标记已读。
3. 本人后续访问不会触发已读。
4. 不同浏览器、不同设备、清除 cookie 后再次访问，会被视为新用户；第一个新用户访问时写入 `readAt`。

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/messages` | 创建消息，返回 `{ id, openUrl, statusUrl }` |
| `GET` | `/api/messages/:id/status` | 查询已读状态，返回 `{ read, readAt, createdAt }` |
| `GET` | `/open/:id` | 打开消息网页 |

请求示例：

```powershell
Invoke-RestMethod -Uri "https://localhost:4000/api/messages" `
  -Method POST -ContentType "application/json" `
  -Body '{"toName":"小明","body":"你好"}'
```

## Android App

安卓工程在 `app/` 目录，技术栈为 Kotlin + Jetpack Compose。

默认后端地址是 Android 模拟器访问宿主机的地址：

```text
https://10.0.2.2:4000
```

真机调试时请改成电脑局域网 IP 或公网地址：

```powershell
cd app
.\gradlew assembleDebug -PAPI_BASE_URL=https://example.com
```

如果本机没有 Gradle Wrapper，可用 Android Studio 打开 `app/` 后同步工程，或安装 Gradle 后运行：

```powershell
cd app
gradle assembleDebug -PAPI_BASE_URL=https://example.com
```

App 会在本地保存最近 50 条发送记录，字段为 `uuid` 和发送时间。

## 常见问题

### 手机扫码打不开

二维码里包含的是 `PUBLIC_BASE_URL`。如果使用默认 `localhost`，手机会访问它自己的本机，所以打不开。请改成电脑局域网 IP、公网 IP 或域名，并确认防火墙和安全组放行端口。

### Redis 数据多久过期

每条消息 90 天后过期。过期后状态接口返回 404，打开页显示链接无效或消息已过期。

### 已读判断是否绝对可靠

不是。当前依赖浏览器 cookie 区分访问者；清除 cookie、换浏览器或换设备会被视为新用户。
