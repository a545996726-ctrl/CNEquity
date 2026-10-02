# 安装

普通用户从 PyPI 安装即可；修改代码、使用仓库运维脚本或构建文档时再克隆源码。

## 系统要求

| 项目 | 要求 |
|---|---|
| Python | 3.10 或更高；CI 覆盖版本见[升级与反馈](upgrading.md) |
| 系统 | macOS、Linux、Windows；Windows 原生 x86-64 路径和文件锁有 CI，32 位及 ARM64 Windows 未验证 |
| 网络 | 真实采集需连接 TDX 与相应 HTTP 来源；安装后可用 sample 离线验证 |
| 磁盘 | 随证券范围、历史深度、频率、原始归档和保留版本增长；另留 staging / 快照空间 |

## 从 PyPI 安装

推荐在独立 Python 环境执行：

```bash
python -m pip install cnequity
cne --version
cne doctor
```

一次安装包含全部运行时 Python 依赖，**无需 extras**。可选来源仍需配置开关、适用的凭证或外部运行环境；依赖装好不表示源可达或拥有访问权限。

下一步直接进入[快速开始](quickstart.md)，运行 `cne init`。

### Windows 路径

PowerShell / cmd 下也使用 `cne`。例如正式湖放到 D 盘：

```powershell
cne config create --data-root D:/cnequity
cne init
```

生成器会处理 TOML 路径转义。PowerShell 5.1 不支持 `&&`，请逐行运行命令。

## 从源码安装

仓库主分支可能领先于稳定 PyPI 版；若要使用开发树刚加入的功能，使用源码安装，并记录 commit。

```bash
git clone https://github.com/rootSunc/CNEquity.git
cd CNEquity
python -m venv .venv
```

激活环境（选当前系统的一条）：

```bash
# macOS / Linux
source .venv/bin/activate
```

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

然后安装：

```bash
python -m pip install --upgrade pip
pip install -e . --group dev
```

`--group` 需要 pip 25.1+。也可用 `uv sync` 安装仓库锁定环境，再通过 `uv run cne ...` 执行。

同花顺公共页面的 hexin-v token 路径还需要 Deno 可执行程序；未安装时该路径会明确报错。它不是基础 TDX demo 的前提，细节见 `adapters/ths/hexin.py`。

只有修改控制台源码才需要 Node/npm；发布包内已带静态资源，见 [frontend 说明](https://github.com/rootSunc/CNEquity/blob/main/frontend/README.md)。文档工具另装 `pip install -r docs/requirements.txt`。

## 生成正式配置

`cne init` 在默认配置不存在时会自动生成，多数情况下不需要单独这一步。想在初始化前修改数据目录等设置时，先运行：

```bash
cne config create
```

默认输出 `configs/cnequity.toml`，`data.root` 转为绝对路径；macOS / Windows 使用保守的 `workers=1`。也可首次创建时加 `--data-root /abs/path/to/lake`。

直接复制模板不会执行路径解析和平台调整，因此优先使用生成命令。已有配置升级时用 `cne config upgrade` 自动补上新的调度 step。个人配置、凭证与数据目录不应提交到仓库。

## 升级已有安装

```bash
pip install --upgrade cnequity
cne config upgrade
```

`cne config upgrade` 把新版本加入的调度 step 和调度组补进配置，先备份原文件，写完后校验。先阅读目标版本的[更新日志](../changelog.md)和[升级与反馈](upgrading.md)，确认是否需要数据迁移。不要用 `config create --force` 代替配置升级。

旧版本的 `tdx` / `macro` / `nlp` 等 extras 已移除。历史版本的单位或 schema 迁移应按对应发布说明执行，不能把针对旧湖的脚本重复应用到新湖。当前 `httpx` 约束为 `>=0.25`；实际安装版本由环境解析，不能从安装命令推定一个固定版本。

## 依赖与诊断

| 依赖 | 用途 |
|---|---|
| Polars / PyArrow / DuckDB | 数据处理、Parquet、SQL |
| httpx / curl_cffi | HTTP 来源访问 |
| Baostock | 估值、历史 ST、退市行情回填 |
| pandas / openpyxl / xlrd | XLS/XLSX 来源解析 |
| NumPy / pypdf / SnowNLP | 期权计算、发行人公告文本、可选情绪计算 |
| FastAPI / Uvicorn / Click | 控制台服务与 CLI |

TDX 线协议客户端随包内置，无需安装通达信桌面软件。依赖约束以 `pyproject.toml` 为准。`cne doctor` 是离线诊断；真实源连通性请用限定源的 probe，见[源健康度](../operations/source-health.md)。

下一步：[快速开始](quickstart.md) → [初始化](initialization.md) → [配置参考](configuration.md)。
