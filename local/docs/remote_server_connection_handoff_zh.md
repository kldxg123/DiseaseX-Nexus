# 远程服务器连接与项目路径创建 — 交接文档（供另一个大模型读取）

> 适用范围：从本机（Windows + Git Bash）连接用户的远程 Linux 服务器，并在服务器上创建新项目目录。
> 本文档**不包含、也不允许打听**任何服务器地址、端口、账号、密码、私钥路径或令牌。连接所需的全部凭据已封装在本机 SSH 别名中。

## 0. 环境事实（已核实）

- 本机：Windows，shell 为 Git Bash（`C:\Program Files\Git\bin\bash.exe`），命令用 Unix 语法，路径用正斜杠。
- 远程：Linux（Ubuntu 系），默认 shell 为 bash；家目录下有 `miniconda3`、`projects/`、`work/` 等目录。
- 远程**没有安装 curl**；HTTP 探测请用 `python3 -c "import urllib.request..."` 或 `wget`（如有）。
- 远程系统 Python 为 `python3.10`（/usr/bin/python3.10）；另有 `~/miniconda3/bin/python`（3.13）。

## 1. 连接方式（唯一允许的通道）

使用本机已配置的 SSH 别名：

```bash
ssh -o ConnectTimeout=10 -o BatchMode=yes workstation-vm201-remote "echo ok && uname -a"
```

规则：
- 一律加 `-o BatchMode=yes`（禁止交互式密码提示，避免挂起）和 `ConnectTimeout`。
- **不得**读取、打印、复制或修改本机 SSH 配置、`服务器信息.txt`、`.env`、密钥文件等任何含秘密的文件。
- **不得**修改 SSH 配置、反向隧道、云服务器安全组；不得新开隧道或转发端口。
- 若连接失败（如 `connection refused` / 超时）：这是外部状态问题，**不要试图修复隧道或配置**，如实报告用户并停止。

## 2. 连通性自检清单（按顺序执行）

```bash
# 1) 基本连通 + 系统信息
ssh -o ConnectTimeout=10 -o BatchMode=yes workstation-vm201-remote "echo CONNECTED; uname -a; whoami | sed 's/./*/g' >/dev/null; pwd"

# 2) 磁盘与工具
ssh -o ConnectTimeout=10 -o BatchMode=yes workstation-vm201-remote "df -h ~ | tail -1; git --version; python3 --version"

# 3) 家目录结构（只看目录名，不读文件内容）
ssh -o ConnectTimeout=10 -o BatchMode=yes workstation-vm201-remote "ls -d ~/projects ~/work 2>/dev/null"
```

注意：不要执行 `whoami` 并把结果写进任何文档或日志；上面已演示用重定向丢弃输出。

## 3. 创建项目路径（约定）

远程项目一律放在登录用户家目录下的 `projects/` 中，用 `~` 引用，**不要硬编码用户名**：

```bash
# 以新项目 my_project 为例
ssh -o ConnectTimeout=10 -o BatchMode=yes workstation-vm201-remote \
  "mkdir -p ~/projects/my_project && cd ~/projects/my_project && pwd && ls -la"
```

建议的初始结构：

```bash
ssh -o ConnectTimeout=10 -o BatchMode=yes workstation-vm201-remote \
  "cd ~/projects/my_project && mkdir -p src data/{raw,processed} reports runtime logs && git init -q 2>/dev/null; ls -A"
```

## 4. 文件传输

```bash
# 上传（本地 -> 远程），同样只用别名
scp -o BatchMode=yes ./local_file.tar.gz workstation-vm201-remote:~/projects/my_project/
# 下载（远程 -> 本地）
scp -o BatchMode=yes "workstation-vm201-remote:~/projects/my_project/reports/result.zip" ./
```

- 不要传输任何含秘密的文件（凭据、密钥、`.env`、`服务器信息.txt` 等）。
- 大文件先打包压缩再传。

## 5. 执行远程命令的注意事项

- 每条命令都是全新 shell：`cd`、环境变量不跨命令保留；需要工作目录时用绝对路径或在同一条命令里 `cd`。
- 长任务（训练、批处理）用 `nohup ... > log 2>&1 &` 或 tmux（如已安装），并记录 pid 文件到项目 `runtime/`。
- 安装依赖优先用项目内虚拟环境（`python3 -m venv .venv`），**不要动系统级 Python 和已运行的服务**。
- 不要kill 不认识的进程；不要动 `~/projects` 下已有项目目录，除非任务明确要求。

## 6. 失败处理与汇报

| 情况 | 处理 |
|---|---|
| SSH 连接被拒/超时 | 停止，报告用户"当前无法连通"，不要重试超过 2–3 次，不要改配置 |
| 权限不足（mkdir/scp 失败） | 报告具体命令与错误，不要 sudo |
| 命令不存在（如 curl） | 换等价工具（python3 urllib / wget），并在报告中注明 |
| 发现疑似秘密的文件 | 不读、不传、不引用，绕行 |

完成后向用户汇报：连通性结果、创建的路径（用 `~` 表示）、磁盘/工具版本、以及任何未决问题。

## 7. 给读取本文档的模型的一句话总结

只用 `ssh -o BatchMode=yes workstation-vm201-remote "<cmd>"` 连接；用 `~/projects/<name>` 建项目；不碰任何秘密文件和隧道配置；连不上就如实报告，不自行修复。
