# 消息已读统计工具

一个简单的「写消息 → 生成链接和二维码 → 监控对方是否打开」的小工具。

- **前端**：React + Vite + TypeScript。表单提交后渲染二维码，并轮询已读状态。
- **后端**：Go（标准库 `net/http`），内存存储，首次访问 `/open/:id` 的浏览器记为本人，其他浏览器访问才记为已读。

## 目录结构

```
.
├── client/         # React + Vite 前端
│   ├── src/
│   └── package.json
├── server/         # Go 后端
│   ├── main.go
│   └── go.mod
├── package.json    # 根目录脚本（联调用）
└── README.md
```

## 前置依赖

| 工具 | 推荐版本 |
|------|----------|
| Node.js | ≥ 18 |
| npm | 随 Node 自带 |
| Go | ≥ 1.22 |

> Windows 用户的命令以 **PowerShell** 为准。

## 一、首次安装

在项目根目录：

```powershell
npm install
```

Go 不需要单独 `go mod tidy`，仅使用标准库。

## 二、启动后端（Go）

### 方式 A：开发模式（`go run`）

```powershell
go -C server run .
```

默认监听 **`http://localhost:4000`**。停止：在该终端按 `Ctrl + C`。

### 方式 B：编译后运行（推荐，更干净）

`go run` 在 Windows 上会派生临时 exe，关闭终端时偶尔会留下孤儿进程占端口。编译后运行更可靠：

```powershell
go -C server build -o read-receipt-server.exe .
.\server\read-receipt-server.exe
```

### 方式 C：换端口

```powershell
$env:PORT = "4010"
go -C server run .
```

## 三、启动前端（React）

另开一个终端：

```powershell
npm run dev -w client
```

打开 [http://localhost:5173](http://localhost:5173) 即可使用。

> 默认前端会请求 `http://localhost:4000`。如果后端改了端口，需要同步设置：
>
> ```powershell
> $env:VITE_API_BASE = "http://localhost:4010"
> npm run dev -w client
> ```
>
> 或在 `client/` 下建 `.env.local`：
>
> ```
> VITE_API_BASE=http://localhost:4010
> ```

## 四、一键联调（前后端一起跑）

在项目根：

```powershell
npm run dev
```

- 后端：`http://localhost:4000`
- 前端：`http://localhost:5173`

## 五、生产模式（前端打包 + 同端口托管）

```powershell
npm run build           # 构建前端到 client/dist
go -C server build -o read-receipt-server.exe .
.\server\read-receipt-server.exe
```

打开 `http://localhost:4000` 即可，无需再跑 Vite。后端会自动托管 `client/dist`。

## 环境变量

| 变量 | 作用 | 默认 |
|------|------|------|
| `PORT` | 后端监听端口 | `4000` |
| `PUBLIC_BASE_URL` | 生成 `openUrl` / `statusUrl` 的根地址；**手机扫码必须设成手机能访问的地址**，不能用 `localhost` | `http://localhost:<PORT>` |
| `WEB_DIST` | 前端构建目录绝对路径，存在则同端口托管 SPA | 自动查找 `client/dist` |
| `VITE_API_BASE` | 前端开发时调用的 API 根地址 | 开发：`http://localhost:4000`；生产：同源 |

**给手机扫码示例**（电脑局域网 IP 设为 `192.168.1.10`）：

```powershell
$env:PORT = "4000"
$env:PUBLIC_BASE_URL = "http://192.168.1.10:4000"
.\server\read-receipt-server.exe
```

确保 Windows 防火墙放行该端口。

## 六、对外公网部署的 URL 配置

后端没有「API base」配置；用 `PUBLIC_BASE_URL` 控制返回给前端的 `openUrl` / `statusUrl`。
**`PORT` 与 `PUBLIC_BASE_URL` 的端口必须一致**（除非用了反代）。

以公网 IP `47.121.190.61` 为例：

### 后端（Windows PowerShell）

```powershell
$env:PORT = "4000"
$env:PUBLIC_BASE_URL = "http://47.121.190.61:4000"
.\server\read-receipt-server.exe
```

需要永久写入当前用户环境变量（重开终端生效）：

```powershell
[Environment]::SetEnvironmentVariable("PUBLIC_BASE_URL", "http://47.121.190.61:4000", "User")
```

撤销：

```powershell
[Environment]::SetEnvironmentVariable("PUBLIC_BASE_URL", $null, "User")
```

### 后端（Linux / macOS 部署）

```bash
PORT=4000 PUBLIC_BASE_URL=http://47.121.190.61:4000 ./read-receipt-server
```

### 验证是否生效

```powershell
Invoke-RestMethod -Uri "http://localhost:4000/api/messages" `
  -Method POST -ContentType "application/json" `
  -Body '{"toName":"x","body":"y"}'
```

返回里的 `openUrl` / `statusUrl` 应该都是 `http://47.121.190.61:4000/...`，
否则说明环境变量没生效或被旧终端缓存了。

### 前端怎么连这个后端

- **同端口托管（推荐）**：直接 `npm run build`，让 Go 服务托管 `client/dist`，
  浏览器访问 `http://47.121.190.61:4000` 即可。**不需要设 `VITE_API_BASE`**。

  ```powershell
  Remove-Item Env:VITE_API_BASE -ErrorAction SilentlyContinue
  npm run build
  ```

- **前后端分离（前端单独跑）**：在 `client/.env.local` 写入

  ```
  VITE_API_BASE=http://47.121.190.61:4000
  ```

  然后 `npm run dev -w client`。

### 配反代（Nginx 把 80 → 本机 4000）

```powershell
$env:PORT = "4000"
$env:PUBLIC_BASE_URL = "http://47.121.190.61"   # 不带端口
.\server\read-receipt-server.exe
```

### 部署清单（容易漏的）

- 服务进程：监听 `0.0.0.0:PORT`（Go 默认就是）。
- 系统防火墙：放行入站 `TCP PORT`。
- 云厂商安全组：放行入站 `TCP PORT`。
- `PUBLIC_BASE_URL`：写公网可达的 URL（IP 或域名）。

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/messages` | 创建消息，返回 `{ id, openUrl, statusUrl }` |
| `GET`  | `/api/messages/:id/status` | 查询已读状态，返回 `{ read, readAt, createdAt }` |
| `GET`  | `/open/:id` | 打开消息网页；首次访问者记为本人，后续不同浏览器访问才标记已读 |

请求示例：

```powershell
Invoke-RestMethod -Uri "http://localhost:4000/api/messages" `
  -Method POST -ContentType "application/json" `
  -Body '{"toName":"小明","body":"你好"}'
```

## 常见问题

### 1. `listen tcp :4000: bind: Only one usage...`

端口被占用。任选其一：

- 换端口：`$env:PORT = "4010"`。
- 杀掉占用进程：

  ```powershell
  Get-NetTCPConnection -LocalPort 4000 -State Listen |
    Select-Object -ExpandProperty OwningProcess -Unique |
    ForEach-Object { Stop-Process -Id $_ -Force }
  ```

### 2. 多次 `go run` 后端口还被占

很可能是 `go run` 留下的临时 exe。一次性清理常用端口：

```powershell
$ports = 4000, 4010, 5173
Get-NetTCPConnection -LocalPort $ports -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
```

或改用「方式 B：编译后运行」。

### 3. `go.mod file not found in current directory...`

在项目根直接 `go run .` 是不行的，要么：

```powershell
go -C server run .
```

要么：

```powershell
cd server
go run .
```

### 4. 手机扫码打不开

二维码里包含的链接是 `PUBLIC_BASE_URL`。如果用了默认 `localhost`，手机自然无法访问。改成你电脑的局域网 IP（见上文「环境变量」），并确认电脑和手机在同一网络且防火墙放行。

## 注意

- 当前消息存在 **内存** 里，服务重启会丢失。需要持久化可后续接 SQLite/Redis 等。
- 没有鉴权，已读依赖浏览器 cookie 区分访问者；清除 cookie、换浏览器或换设备会被视为新用户。

