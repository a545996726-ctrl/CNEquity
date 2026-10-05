"""Reading the lake: `query`, `serve`, `mcp`.

SQL and services read the lake; explicit on-demand queries may fetch and cache
remote data, and MCP live mode is separately opt-in.
"""

from __future__ import annotations

import errno
import json
import logging
import socket
import sys
from pathlib import Path

import click

from cnequity.cli._root import cli
from cnequity.cli._shared import (
    _cfg,
    config_option,
    resolve_config_path,
)
from cnequity.query.on_demand import OnDemandService
from cnequity.query.views import ensure_duckdb_views

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
# macOS resolves localhost to ::1 before 127.0.0.1, and a browser does not
# fall back when that first address refuses the connection. Binding only the
# IPv4 loopback makes http://localhost:<port>/ look like the panel is down.
_LOOPBACK_FAMILIES = (
    (socket.AF_INET, "127.0.0.1"),
    (socket.AF_INET6, "::1"),
)


def _address_in_use(exc: OSError) -> bool:
    if exc.errno == errno.EADDRINUSE:
        return True
    # 10048 is WSAEADDRINUSE. 10013 is WSAEACCES from SO_EXCLUSIVEADDRUSE.
    return getattr(exc, "winerror", None) in {10048, 10013}


def bind_loopback(port: int) -> list[socket.socket]:
    """Listen on every loopback family this machine has.

    The sockets are already listening, so a browser can connect while the
    dashboard app is still being built. The kernel queues that connection
    until uvicorn accepts it, instead of answering with connection refused.
    """
    bound: list[socket.socket] = []
    chosen = port
    try:
        for family, address in _LOOPBACK_FAMILIES:
            sock = socket.socket(family, socket.SOCK_STREAM)
            try:
                if family is socket.AF_INET6:
                    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                # Windows SO_REUSEADDR lets a second socket bind a port that is
                # already listening, so a busy port would look free. Exclusive
                # use makes that bind fail. Other platforms still reuse the
                # address so a restart during TIME_WAIT can bind again.
                if sys.platform == "win32":
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                else:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((address, chosen))
                sock.listen(2048)
                sock.set_inheritable(True)
            except OSError as exc:
                sock.close()
                if _address_in_use(exc):
                    raise click.ClickException(
                        f"端口 {chosen or port} 已被占用，面板没有启动。"
                        "先停掉占用这个端口的进程，再运行 cne serve。"
                    ) from exc
                continue
            chosen = sock.getsockname()[1]
            bound.append(sock)
    except Exception:
        for sock in bound:
            sock.close()
        raise
    if not bound:
        raise click.ClickException("这台机器没有可用的回环地址，面板没有启动。")
    return bound


def _panel_url(sockets: list[socket.socket], token: str | None) -> str:
    """One loopback URL. localhost is the name browsers try first."""
    suffix = f"?token={token}" if token else ""
    port = sockets[0].getsockname()[1]
    names = [
        "localhost" if sock.getsockname()[0] == "::1" else sock.getsockname()[0] for sock in sockets
    ]
    name = "localhost" if "localhost" in names else names[0]
    return f"http://{name}:{port}/{suffix}"


def _run_server(app, host: str, port: int, sockets: list[socket.socket] | None) -> None:
    import uvicorn

    if sockets is not None:
        uvicorn.Server(uvicorn.Config(app, log_level="info")).run(sockets=sockets)
        return
    uvicorn.run(app, host=host, port=port, log_level="info")


@cli.command()
@config_option
@click.option(
    "--host",
    default="127.0.0.1",
    show_default=True,
    help="回环地址会同时监听 127.0.0.1 和 localhost。非回环地址必须配 --token。",
)
@click.option("--port", default=8787, show_default=True)
@click.option(
    "--token",
    default=None,
    help="要求这个 bearer token（或 ?token=）。--host 不是回环地址时必须设置。",
)
@click.option(
    "--read-only",
    is_flag=True,
    help="只浏览。不注册操作页和存储清理的写入口。",
)
@click.option(
    "--allow-remote-ops",
    is_flag=True,
    help="非回环地址上也可以从面板发起取数。默认远程只能浏览和做存储清理；令牌在网址里，局域网又是明文。",
)
def serve(
    config_path: str,
    host: str,
    port: int,
    token: str | None,
    read_only: bool,
    allow_remote_ops: bool,
):
    """启动数据湖面板。回环地址上可以从页面发起取数和日更。

    \b
    查看覆盖、新鲜度和来源；操作页按白名单启动初始化、日更、回填和巡检；
    取数设置预览后写回配置，不启动取数；
    存储运维页检查并确认已到期版本和试验清理。``--read-only`` 关掉这些写入口。
    非回环地址必须带 ``--token``。远程默认不能发起取数，除非同时给 ``--allow-remote-ops``。
    回环地址同时接受 127.0.0.1 和 localhost。
    """
    from cnequity.serve.app import create_app

    # Checked before the config is even loaded: a typo in --config must not
    # mask the bind guard by failing first. The service has no other access
    # control, and a lake holds a full market history plus the paths and
    # sources that built it.
    if host not in _LOOPBACK and not token:
        raise click.ClickException(
            f"--host {host} 会把面板暴露到本机之外；"
            "请用 --token 要求令牌，或者把 --host 留在 127.0.0.1。"
        )
    if allow_remote_ops and host in _LOOPBACK:
        allow_remote_ops = False

    ctx = click.get_current_context()
    config_was_default = (
        ctx.get_parameter_source("config_path") is click.core.ParameterSource.DEFAULT
    )
    config_file = Path(config_path).expanduser()
    setup = config_was_default and not config_file.exists()
    if setup and host not in _LOOPBACK:
        raise click.ClickException("首次配置只能在本机打开，请把 --host 留在 127.0.0.1。")

    cfg = None if setup else _cfg(config_path)
    if read_only:
        mode = "只读（--read-only）"
    elif setup:
        mode = "首次配置"
    elif host not in _LOOPBACK and not allow_remote_ops:
        mode = "远程浏览（取数未开启；存储清理仍可用）"
    else:
        mode = "可从面板发起取数和日更"
    sockets: list[socket.socket] | None = None
    try:
        if host in _LOOPBACK:
            sockets = bind_loopback(port)
            port = sockets[0].getsockname()[1]
        if cfg is not None:
            click.echo(f"数据湖：  {cfg.data_root}")
        else:
            click.echo(f"还没有配置文件 {config_file}，面板进入首次配置。")
        if sockets is not None:
            url = _panel_url(sockets, token)
            click.echo(f"面板：    {url}")
            primary = url.split("?", 1)[0].rstrip("/")
        else:
            suffix = f"?token={token}" if token else ""
            primary = f"http://{host}:{port}"
            click.echo(f"面板：    {primary}/{suffix}")
        click.echo(f"API 文档：{primary}/api/docs")
        if not setup:
            query = f"?token={token}" if token else ""
            click.echo(f"源健康：  {primary}/source-health{query}")
        click.echo(f"模式：    {mode}")
        _run_server(
            create_app(
                cfg,
                token=token,
                read_only=read_only,
                allow_remote_ops=allow_remote_ops,
                setup=setup,
                config_path=config_file,
            ),
            host,
            port,
            sockets,
        )
    finally:
        for sock in sockets or []:
            try:
                sock.close()
            except OSError:
                pass


@cli.command()
@config_option
@click.option("--sql", default="SELECT COUNT(*) AS n FROM daily_bars")
@click.option("--dataset", default=None, help="按需抓取的数据集名")
@click.option("--symbol", default=None, help="按需抓取的标的代码")
@click.option(
    "--refresh",
    is_flag=True,
    help="抓取前先刷新按需缓存（需要同时给 --dataset 和 --symbol）。",
)
def query(
    config_path: str,
    sql: str,
    dataset: str | None,
    symbol: str | None,
    refresh: bool,
):
    """跑 DuckDB SQL，或按需抓取单个数据集。"""
    cfg = _cfg(config_path)
    if (dataset is None) != (symbol is None):
        raise click.UsageError("--dataset 和 --symbol 必须一起给")
    if refresh and dataset is None:
        raise click.UsageError("--refresh 需要同时给 --dataset 和 --symbol")
    if dataset and symbol:
        svc = OnDemandService(cfg)
        fetch_kwargs = {"refresh": True} if refresh else {}
        try:
            data = svc.fetch(dataset, symbol, **fetch_kwargs)
        except (ValueError, NotImplementedError, RuntimeError) as exc:
            raise click.ClickException(str(exc)) from None
        click.echo(json.dumps(data, indent=2, ensure_ascii=False, default=str))
        if data.get("error"):
            raise SystemExit(1)
        return
    db_path = ensure_duckdb_views(cfg)
    import duckdb

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        # A typo in the SQL is the most ordinary thing that happens here, and
        # DuckDB's own message already names the line, the column and the near
        # miss. Keep that text and drop the Python traceback wrapped around it.
        try:
            df = con.execute(sql).pl()
        except duckdb.Error as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(df)
    finally:
        con.close()


@cli.command("mcp")
@config_option
@click.option(
    "--live",
    is_flag=True,
    help=(
        "湖里没有的数据就按需向源头取，并且不落盘。只支持标的查找和未复权日线；其它工具宁可拒绝，也不会在没有复权、universe "
        "和 PIT 的情况下作答。"
    ),
)
def mcp_cmd(config_path: str, live: bool):
    """通过 MCP（stdio）把这个湖开放给 AI agent。

    \b
    它不是拿来手敲的：任何兼容 MCP 的客户端会拉起这个进程，并在管道上讲 JSON-RPC。
    各家客户端的注册界面不一样，但可移植的命令和参数就是：

    \b
      cne mcp --config /path/to/cnequity.toml

    \b
    把它填进客户端 MCP 配置里的 `command` / `args`。这里用的是标准 stdio 传输，
    不是某一家厂商专有的 Claude 集成。

    \b
    MCP 工具只读，不提供 serve 的操作页和网页确认清理。这些工具只查询湖；采集从 CLI 或 serve 的操作页发起。
    """

    from cnequity.mcp_server import serve_stdio

    cfg = _cfg(config_path)
    # Opt-in, never inferred. A lake user whose lake is broken must get "no
    # parquet data" and go fix it, not a quietly different answer from a vendor.
    cfg._mcp_live = live
    if not live:
        _guard_mcp_data_root(cfg, config_path)

    # stdout is the JSON-RPC wire. Anything else written there is a parse error
    # on the client with no indication of where it came from, so every log
    # record — ours and every library's — goes to stderr, which MCP clients
    # capture as the server's log.
    # `force=True` because the CLI root configures INFO logging for every
    # command; without it this call would be a no-op and the wire's log
    # would carry every INFO record the pipeline emits.
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING, force=True)
    serve_stdio(cfg)


def _guard_mcp_data_root(cfg, config_path: str) -> None:
    """Refuse to serve a lake with nothing in it, and say why it is empty.

    A relative ``data.root`` resolves against the working directory, and this is
    the one entry point where the working directory belongs to somebody else —
    an MCP client spawns the process from wherever it happens to be. The lake
    then resolves to a path that does not exist, every tool answers "no parquet
    data", and the agent reports that the data is missing. Which is true of that
    path and false of the user's lake.

    Cheap enough to do on every start: one directory walk that stops at the
    first file.
    """
    curated = cfg.curated_root
    if curated.exists() and next(curated.rglob("*.parquet"), None) is not None:
        return
    raise click.ClickException(
        f"{curated} 下没有任何 curated 数据。\n"
        f"  配置：     {resolve_config_path(config_path).resolve()}\n"
        f"  data.root：{cfg.data_root}\n"
        "如果这不是你的湖：`data.root` 是相对路径，会相对客户端拉起这个进程时的工作目录解析。"
        "请把 `--config` 和 `[data].root` 都写成绝对路径。\n"
        "如果这确实是你的湖、而且它真的是空的：`cne init` 会建一个，"
        "`cne init --profile demo` 三十秒内做出一个 5 只票的样例，"
        "而 `--live` 不需要湖也能直接从源头提供标的查找和未复权日线。"
    )
