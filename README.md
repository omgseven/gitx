# gitx

通过 SSH 与 libgit2 完成 git 同步的轻量命令行工具，运行全程不调用 `git` 命令。

## 为什么存在

工作电脑上的安全软件对 `git` 命令有弹窗拦截。gitx 基于 pygit2（libgit2）实现 clone、fetch、pull、push 与 sync，使用标准 SSH 传输（git-upload-pack / git-receive-pack），在网络层面与 git 等价，与 git 二进制无关。

## 快速开始

无需仓库，一条命令完成安装：

```sh
curl -fsSL https://raw.githubusercontent.com/omgseven/gitx/main/install.sh | sh
```

也可以克隆仓库后，在仓库目录执行：

```sh
sh install.sh
```

无仓库安装时，`gitx.py` 会下载到 `~/.local/share/gitx`；从仓库安装时，使用仓库内的副本。安装脚本自动完成全部依赖与配置：

1. **准备 Python**：扫描 Python 3.10 及以上的解释器；未找到时，在 Homebrew 可用的环境下自动执行 `brew install python` 完成安装。
2. **创建虚拟环境**：在程序目录生成 `.venv`，与系统 Python 隔离。
3. **安装 pygit2**：装入虚拟环境，不污染系统环境。
4. **生成启动器**：在 `~/.local/bin/gitx` 落位，任何仓库目录下都可执行 `gitx`。

若 `~/.local/bin` 不在 PATH 中，脚本会输出需要添加的 export 行，将该行写入 shell 配置文件后重新打开终端即可。

重复执行安装命令是安全的：已存在的虚拟环境与依赖会被复用，程序文件更新到最新版本。移动仓库位置或更换 Python 版本后，重新执行安装命令即可；依赖损坏时，也可在任何目录执行 `gitx install` 修复。

## 命令一览

| 命令 | 作用 |
|---|---|
| `gitx install` | 重建本地环境：虚拟环境、pygit2 与启动器 |
| `gitx clone <owner/name> [dir]` | 通过 SSH 克隆仓库，支持完整 URL 与本地路径 |
| `gitx status` | 显示当前分支、跟踪关系与工作区状态 |
| `gitx fetch [remote]` | 下载远端更新，列出发生变化的引用 |
| `gitx pull [remote]` | 下载更新并集成到当前分支 |
| `gitx push [remote]` | 推送当前分支 |
| `gitx sync [remote]` | 下载、集成、推送一步完成 |

remote 参数省略时默认为 `origin`。

## 同步行为

- **集成策略**：能快进则快进；历史分叉时生成 merge commit。不提供 rebase，merge 同样支持两台电脑任意顺序提交的同步。
- **冲突保护**：merge 出现冲突时自动回滚，仓库保持操作前的状态，并列出冲突文件供手工处理。
- **改动保护**：工作区存在未提交的跟踪文件改动时，pull 与 sync 拒绝执行；未跟踪文件不受影响。
- **推送保护**：本地缺少远端提交时拒绝推送，提示先执行 `gitx sync`；推送完成后核对远端引用，确认提交真实落地。

## 认证

按以下顺序尝试 SSH 凭据，密钥读取自 `~/.ssh`：

1. `github_ssh`（对应 `~/.ssh/config` 中 github.com 条目的密钥配置）
2. `id_ed25519`
3. `id_rsa`
4. ssh-agent（通过 `SSH_AUTH_SOCK`）

## 依赖

- Python 3.10 及以上（需要 venv 模块），缺失时由安装脚本在 Homebrew 可用时自动安装
- pygit2，由安装脚本自动装入 `.venv`

## 仓库结构

```
gitx.py      单文件 CLI，含 install 子命令与全部功能
install.sh   一键安装入口
```
