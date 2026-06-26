# Server Migration Handoff

记录时间：2026-06-21

## 当前目标

把“手机 App 控制服务器终端”的方案从原生 iOS 转成 Windows 友好的 PWA/Web App：

- Windows 上开发和预览网页界面。
- 迁移到 Linux 服务器后，通过 WebSocket + PTY 连接真实 shell。
- iPhone 用 Safari 打开网页后，可以“添加到主屏幕”，近似 App 使用。

## 当前文件结构

核心文件：

- `web/index.html`：PWA 主页面。
- `web/styles.css`：移动端终端界面样式。
- `web/app.js`：WebSocket 客户端、输入栏、斜杠命令、本地输出缓冲。
- `web/manifest.webmanifest`：PWA manifest。
- `web/service-worker.js`：PWA 缓存。
- `web/icons/icon.svg`：PWA 图标。
- `gateway/server_console_gateway.py`：Python 标准库实现的 HTTP 静态文件服务 + WebSocket 终端网关。
- `scripts/start-preview.ps1`：Windows demo 预览脚本。
- `scripts/start-server.sh`：Linux 服务器真实 PTY 启动脚本。
- `scripts/smoke-gateway.py`：网关静态资源、认证、Origin 和握手冒烟测试。
- `deploy/systemd/server-console.service`：生产部署用 systemd 模板。
- `README.md`：当前主要使用说明。
- `docs/subagent-security-notes.md`：安全和协议建议。

仍保留但暂不作为主线：

- `ios/ServerConsole/`：原生 SwiftUI iOS 工程，需要 macOS/Xcode。用户当前只有 Windows，所以后续应优先推进 PWA。

## 已完成

1. 建立了 PWA 前端。
2. 建立了 Python 网关，设计为：
   - `/`、`/app.js`、`/styles.css` 等路径返回静态网页资源。
   - `/terminal?session=...` 升级为 WebSocket，token 通过 WebSocket subprotocol 传递；旧的 `?token=` 仍保留兼容。
3. WebSocket 协议使用 JSON 文本消息处理控制流；终端输入/输出优先用二进制帧，JSON/base64 保留兼容。
4. 前端已支持：
   - WebSocket URL 输入。
   - Access Token 单独输入，避免把 token 写进 URL/localStorage。
   - Connect / Disconnect。
   - 实时输出滚动。
   - 本地命令输入框。
   - `/clear`、`/ping`、`/ctrlc`、`/disconnect`、`/kill`、`/restart`。
   - Ctrl+C 按钮。
   - 输入框编辑时，远端输出仍可继续追加显示。
5. 网关新增了 `--mode`：
   - `auto`：默认模式，Unix-like 环境走 PTY，Windows 走 demo。
   - `pty`：真实 PTY 模式，给 Linux 服务器使用。
   - `demo`：Windows 预览模式，不执行真实命令，只模拟输出。

## 重要限制

- Windows 本地无法提供真实 Unix PTY，所以只能跑 demo 预览。
- 真实命令执行需要把项目迁移到 Linux/macOS 服务器后运行 `--mode pty`。
- `scripts/start-server.sh` 默认要求 token。只有明确设置 `REQUIRE_TOKEN=0` 时才会关闭认证，不能用于公网。
- 当前安全模型仍是原型级别，仅有 token，不能直接暴露公网生产使用。

## 迁移到服务器后的建议步骤

在服务器中进入项目根目录：

```bash
cd /path/to/server
```

启动真实 PTY 网关：

```bash
chmod +x scripts/start-server.sh
umask 077
openssl rand -hex 24 > .server-console-token
BIND_HOST=127.0.0.1 PORT=8765 TOKEN_FILE=.server-console-token ./scripts/start-server.sh
```

如果要用 systemd，先创建受限用户和 token 文件，再参考 `deploy/systemd/server-console.service` 安装到 `/etc/systemd/system/server-console.service`。不要用 root 长期运行网关。

然后在手机 Safari 打开：

```text
http://SERVER_IP:8765/
```

如果要用可恢复会话，建议改成 tmux：

```bash
BIND_HOST=127.0.0.1 PORT=8765 TOKEN_FILE=.server-console-token SHELL_CMD="tmux new-session -A -s phone" ./scripts/start-server.sh
```

## 迁移后优先验证

1. 服务器上运行：

```bash
python3 gateway/server_console_gateway.py --host 127.0.0.1 --port 8765 --token-file .server-console-token --require-token --mode pty
```

2. 浏览器打开：

```text
http://SERVER_IP:8765/
```

3. 点击 Connect。
4. 输入：

```bash
whoami
pwd
date
```

5. 测试编辑输入框时服务器输出是否继续刷新：

```bash
while true; do date; sleep 1; done
```

然后在手机输入框里编辑但暂不发送，观察输出是否继续变化。

6. 测试 `/clear`、`/ping`、`/ctrlc`。

## 待完成/待修

- 上一次 Windows 本地冒烟测试被中止，没有完成最终确认。
- 需要在服务器上重新跑 Python 语法检查、`python3 scripts/smoke-gateway.py` 和真实连接测试。
- 需要确认 `gateway/server_console_gateway.py` 在目标 Linux 发行版上能正常导入 `pty`、`fcntl`、`termios`。
- 需要确认 PWA service worker 在服务器路径 `/` 下缓存正常。
- 如果要公网访问，需要加 TLS，推荐 Nginx/Caddy 反代到本地 `127.0.0.1:8765`。
- 生产前需要补认证、审计、权限隔离、命令限制或容器隔离。

## 注意

不要把 `--host 0.0.0.0` + 示例 token 或弱 token 直接暴露公网。

更安全的服务器启动方式是：

```bash
python3 gateway/server_console_gateway.py --host 127.0.0.1 --port 8765 --token "随机长token" --mode pty
```

然后用 HTTPS 反向代理暴露给手机。
