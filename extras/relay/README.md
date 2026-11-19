# relay：双方都不知道对方 IP 的连接方式（实验）

给 Claude 一组**固定动作**去操作远端设备上的外接设备，不给命令行。Claude 和远端设备都只连一个中转站，谁也不直接连谁。

```
Claude（云端）  --HTTPS-->  中转站  <--HTTPS（主动连出去）--  资源端（装在远端设备上）
relayctl                    relay.py                          agent.py → devctl
```

| 文件 | 放在哪 | 作用 |
|---|---|---|
| `relay.py` | 一台公网机器，443 端口 | 只认令牌；对 Claude 只报设备**代号**，不报 IP；不记录任何访问者的 IP |
| `agent.py` | 每台远端设备 | 主动连中转站领任务；只执行白名单动作（调用 [`devctl`](../devctl)）；**没有"执行任意命令"**；输出里的 IP 会被抹掉 |
| `relayctl` | Claude 的云端环境 | 经出口代理连中转站，下任务、收文件；只信任中转站自己的证书 |

白名单动作：`list`、`events`、`disk_ls`、`disk_get`、`cam_snap`、`fake_disk`（测试用）。

## 验证到哪一步了

| 环节 | 状态 |
|---|---|
| Claude 经出口代理（443 + TLS）连中转站，不用 Tailscale | 已测通 |
| 列设备、列外设、浏览 U 盘、取文件、摄像头拍照（假外设）整条链路 | 已测通 |
| 被拒：执行命令、路径穿越（`../../etc/shadow`）、未知设备、错误令牌 | 已测 |
| Claude 收到的内容里没有 IP；中转站日志里没有任何人的 IP | 已测 |
| IP 抹除规则：IPv4、IPv6、MAC 抹掉；时间、USB 编号、版本号保留 | 已测（本地单元测试） |
| **资源端放在 Claude 从没见过的机器上，确认从头到尾拿不到它的 IP** | **没测** |
| 中转站挂到 Cloudflare 等后面，连中转站的 IP 也藏起来 | **没测** |
| 真实外设（U 盘、摄像头、手机） | **没测**（见 devctl 的说明） |

第一轮测试里，中转站和资源端放在同一台机器上，而那台机器的 IP 本来就已知，所以**这一轮还不能证明"藏住了 IP"**，只证明这套结构能用、白名单挡得住越权。

## 藏不住的东西

- 中转站自己的地址：Claude 必须知道，除非再挂到别的服务后面。
- 内容本身：照片里的街景、文件内容都可能暴露位置。
- 资源端没有"任意命令"才能保证 IP 不外泄。一旦加上这个动作，一条查本机 IP 的命令就会泄露。

## 用法（最小示例）

中转站机器上，目录里放 `tls.crt`、`tls.key`、`client.token`、`agent.token`，其中令牌是随机字符串：

```bash
python3 relay.py /path/to/dir
```

资源端：

```bash
python3 agent.py RELAY_HOST devA /path/to/agent.token /path/to/relay.crt
```

Claude 这边：

```bash
export RELAY_HOST=... RELAY_TOKEN_FILE=... RELAY_CA=relay.crt RELAY_OUT=./out
python3 relayctl devices
python3 relayctl run devA list
python3 relayctl run devA cam_snap fake
```

自签名证书需要带中转站地址的 `subjectAltName`，`relayctl` 用它做证书固定。长期使用请换成正规证书，并把程序放进 systemd 服务。
