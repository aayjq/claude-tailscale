# ts-up：让 Claude Code 云端会话连上你的 Tailscale 设备

Claude Code 的云端会话（claude.ai/code）只能访问 HTTPS 网站，不能直接 SSH 到你的服务器。
这个脚本让云端会话通过 Tailscale 加入你自己的私有网络，然后直接 `ssh 设备名` 执行命令：
不需要密码，也不用在服务器上开放端口。

```
云端会话（用户态 tailscaled） → HTTPS 443 → Tailscale DERP 中继 → 你的设备（Tailscale SSH）
```

流量由 WireGuard 端到端加密，中继服务器看不到内容。

## 分工

**每台要连的设备上，你只做一件事**：安装 Tailscale 并登录。

```bash
curl -fsSL https://tailscale.com/install.sh | sh
```

```bash
tailscale up --ssh
```

**其余都交给 Claude**（通过 Tailscale API）：

- 写好 ACL：云端会话（`tag:claude`）只能访问你指定的设备（`tag:server`），你自己设备的权限不变
- 给新设备打 `tag:server`，关闭它的密钥过期
- 每个新会话自动用 API 生成一次性、10 分钟有效、带 `tag:claude` 的密钥加入网络，环境里不存长期密钥

## 一次性配置（约 5 分钟）

### 1. 生成 Tailscale API 令牌

Tailscale 后台 → Settings → Keys → **Generate access token**。

### 2. 配置云端环境

在 claude.ai/code 会话标题栏的环境菜单 → Edit：

- **Setup script**：加入下面这一行（固定到具体提交，仓库以后的改动不会自动影响你；想升级时换成新的提交号）：

  ```bash
  curl -fsSL https://raw.githubusercontent.com/aayjq/claude-tailscale/7325fd04ff967d0f3d2f30febb320c0633d90819/setup.sh | bash
  ```

  也可以把 [`setup.sh`](setup.sh) 的全部内容直接粘贴进去，这样就不依赖这个仓库。
- **Network secrets**：添加一条，Host 填 `api.tailscale.com`，Header 用 `Authorization: Bearer`，值填第 1 步的令牌。
  也可以直接在会话里跟 Claude 说“帮我保存 Tailscale API 令牌”，它会弹出安全表单。
  令牌由网络代理注入请求，Claude 和脚本都看不到它的值。

### 3. 新开一个会话，让 Claude 完成剩下的设置

> 你：按 claude-tailscale 的说明，帮我配置 Tailscale：写入 acl.hujson 里的规则，把 my-server 设为 tag:server

Claude 会先读取现有 ACL，校验新规则，给你看改动并等你确认，然后提交。规则模板见 [`acl.hujson`](acl.hujson)。

## 日常使用

> 你：连一下 my-server，看看磁盘和内存还剩多少
>
> Claude：运行 `ts-up` → `ssh my-server 'df -h; free -h'` → 汇报结果

> 你：我在新服务器上装好 Tailscale 了，叫 web-2
>
> Claude：通过 API 给 web-2 打上 `tag:server`、关闭密钥过期 → 重新运行 `ts-up` → 可以连了

第一次运行 `ts-up`、第一次修改 ACL 时，Claude 的自动权限检查可能会拦一下，回复确认即可。

## 可选：Claude 指挥，服务器上的 Qoder 干活

如果服务器上装了 [Qoder CLI](https://qoder.com.cn)（国内版 `qoderclicn` 或国际版 `qodercli`），Claude 会这样分工：

| 任务 | 谁来做 |
|---|---|
| 理解需求、拆解步骤 | Claude |
| 查状态、看日志等简单命令 | Claude 直接 `ssh` |
| 写代码、部署、调试 | 交给服务器上的 Qoder |
| 检查结果、检查有没有改到范围外的文件 | Claude 独立验证 |
| 删除数据、改防火墙、重启等有风险的操作 | Claude 先问你，再亲自执行 |

好处是：执行在服务器本机进行，不用每条命令都经过中继；写代码这类体力活用的是 Qoder 账号的额度。

Claude 通过 [`extras/qoder-task`](extras/qoder-task) 给 Qoder 下任务。这个脚本会：

- 把 Qoder 限定在一个工作目录里，并要求它按固定格式汇报改了什么、怎么测的
- 记录每个任务的日志（`/var/log/qoder-tasks/`）
- 支持后台运行长任务（`-b`），以及接着上次的对话继续（`-c`）
- 用 `qoder-task changes <任务号>` 列出工作目录以外被改动的文件

你只需要在服务器上装好 Qoder CLI 并登录一次（`qoderclicn login`），`qoder-task` 由 Claude 自动安装。

> 你：在服务器上用 Python 写个返回当前时间的网页服务，跑在 8080 端口
>
> Claude：检查 Python 和端口 → `qoder-task -d /srv/timeweb -n timeweb "…"` → Qoder 写代码、建 systemd 服务、自测 → Claude 用 curl 和 `qoder-task changes` 独立验证

## 不想用 API 令牌？

也可以不保存 API 令牌，按以下任一方式：

- 在 Tailscale 后台自己生成一个 Reusable + Ephemeral、带 `tag:claude` 的授权密钥，放进环境变量 `TAILSCALE_AUTHKEY`；ACL 和设备 tag 也在后台自己改。
- 什么都不配：每个新会话会给出一个登录链接，点一下批准即可。

## 安全须知

- **API 令牌拥有你 Tailscale 网络的完整管理权限**。它保存在你的云端环境里，该环境的每个会话都能用它调用 API。
  生成时有效期选短一些，到期再换；不用了就在环境设置里删掉，并在 Tailscale 后台作废。
- 云端会话只带 `tag:claude`，按 `acl.hujson` 只能访问 `tag:server` 设备，碰不到你的个人电脑。
- 不要把服务器密码发在聊天里或写进脚本。用了 Tailscale SSH 就不需要密码了。
- 脚本只从 `pkgs.tailscale.com` 下载 Tailscale 官方的最新稳定版。

## 常见问题

**`ts-up` 输出了登录链接**：API 令牌没配好或已过期，也没有 `TAILSCALE_AUTHKEY`。打开链接批准，再运行一次 `ts-up`。

**提示 "Tailscale SSH requires an additional check"**：ACL 里对应的 SSH 规则还是 `check`，改成 `accept`。

**连不上某台设备**：它可能还没有 `tag:server`，或者登录时没加 `--ssh`。让 Claude 查一下。

**Windows 设备**：Tailscale SSH 服务端只支持 Linux（以及开源版 macOS tailscaled），Windows 需要另外开 OpenSSH Server。

---

图文版安装手册：[`docs/index.html`](docs/index.html)（可用 GitHub Pages 发布）。本项目使用 [MIT 许可证](LICENSE)。

Tailscale 是 Tailscale Inc. 的产品，本项目与其无官方关联。
