# ts-up：让 Claude Code 云端会话连上你的 Tailscale 设备

Claude Code 的云端会话（claude.ai/code）只能访问 HTTPS 网站，不能直接 SSH 到你的服务器。
这个脚本让云端会话通过 Tailscale 加入你自己的私有网络，然后直接 `ssh 设备名` 执行命令：
不需要密码，也不用在服务器上开放端口。

```
云端会话（用户态 tailscaled） → HTTPS 443 → Tailscale DERP 中继 → 你的设备（Tailscale SSH）
```

流量由 WireGuard 端到端加密，中继服务器看不到内容。

## 哪些是现成的，哪些要你自己做

| 部分 | 谁来做 |
|---|---|
| `setup.sh`：安装 Tailscale 和 ssh，生成 `ts-up`、SSH 配置和给 Claude 的使用说明 | 直接用 |
| `ts-up`：启动、自动登录、设备过期后自动重连、按设备名生成 SSH 别名 | 脚本自带 |
| Tailscale 账号，要连的设备上都装好 Tailscale 并开启 `--ssh` | 你 |
| ACL 里 SSH 规则改成 `accept` | 你 |
| 授权密钥 `TAILSCALE_AUTHKEY`（可选） | 你 |

## 安装

### 1. 在要连的设备上开启 Tailscale SSH

```bash
curl -fsSL https://tailscale.com/install.sh | sh
```

```bash
tailscale up --ssh
```

### 2. SSH 规则改成免确认

Tailscale SSH 默认是 `check` 模式，每台新设备都要在浏览器里确认一次。云端会话每次都是新设备，所以要改成 `accept`。

- **简单版**：在 Tailscale 后台 → Access controls，把默认 SSH 规则的 `"action": "check"` 改成 `"accept"`。
- **安全版（推荐）**：给云端会话和目标设备分别打 tag，只放行需要的设备：

```json
"tagOwners": {
  "tag:claude": ["autogroup:admin"],
  "tag:server": ["autogroup:admin"]
},
"grants": [
  {"src": ["tag:claude"], "dst": ["tag:server"], "ip": ["tcp:22"]}
],
"ssh": [
  {"action": "accept", "src": ["tag:claude"], "dst": ["tag:server"], "users": ["root"]}
]
```

然后在目标设备上运行：

```bash
tailscale up --ssh --advertise-tags=tag:server
```

### 3. 生成授权密钥（可选，推荐）

在 Tailscale 后台 → Settings → Keys 里生成，勾选 **Reusable** 和 **Ephemeral**；如果用了安全版规则，Tags 选 `tag:claude`。
不填密钥也能用：每个新会话会给出一个登录链接，点一下批准即可。

### 4. 配置云端环境

在 claude.ai/code 会话标题栏的环境菜单 → Edit：

- **Setup script**：加入下面这一行（固定到某个版本号，这样仓库以后改动不会自动影响你的环境）：

  ```bash
  curl -fsSL https://raw.githubusercontent.com/aayjq/claude-tailscale/v1.0.0/setup.sh | bash
  ```

  也可以把 [`setup.sh`](setup.sh) 的全部内容直接粘贴进去，这样就不依赖这个仓库。
- **Environment variables**：添加 `TAILSCALE_AUTHKEY=tskey-auth-…`
- **Network access**：保持默认即可

### 5. 新开一个会话，直接用

> 你：连一下我的服务器，看看磁盘和内存还剩多少
>
> Claude：运行 `ts-up` → `ssh 设备名 'df -h; free -h'` → 汇报结果

第一次运行 `ts-up` 时，Claude 的自动权限检查可能会拦一下，因为它会让容器加入你的网络。回复“确认”即可。

## 安全须知

- 云端会话加入后，等于你网络里多了一台设备，ACL 放行哪些设备它就能访问哪些。建议用安全版规则。
- 环境变量是明文保存的。密钥一定要选 Ephemeral，有 tag 更好；泄露了就在 Tailscale 后台作废。
- 不要把服务器密码发在聊天里或写进脚本。用了 Tailscale SSH 就不需要密码了。
- 脚本只从 `pkgs.tailscale.com` 下载 Tailscale 官方的最新稳定版。

## 常见问题

**`ts-up` 输出了登录链接**：没有设置密钥，或者密钥失效了。打开链接批准，再运行一次 `ts-up`。

**提示 "Tailscale SSH requires an additional check"**：第 2 步的规则还是 `check`。

**会话闲置后连不上了**：Ephemeral 设备闲置后会被自动删除。再运行一次 `ts-up`，它会用密钥重新加入。

**设备名是什么**：Tailscale 后台里显示的机器名（小写）。`ssh 用户名@100.x.y.z` 也可以。

---

Tailscale 是 Tailscale Inc. 的产品，本项目与其无官方关联。
