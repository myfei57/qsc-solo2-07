"""命令行入口。

五个子命令覆盖日常使用：

``serve``   启动控制台 HTTP 服务，可选地在启动后探测页面；
``demo``    在本地跑一遍完整流程（启动顺序 → 控制周期 → 并发探测 → 标定 →
            停运顺序），用于交付前的真实运行检查；
``status``  打印当前状态快照；
``config``  打印生效配置；
``reset``   清掉落盘的引风机状态。
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from .app import CycleInputs, Platform, build_platform
from .clock import ManualClock, SystemClock
from .config import config_to_mapping, default_config, load_config
from .console.server import ConsoleServer, probe_pages
from .errors import FgdError

DEFAULT_DEMO_CYCLES = 12


def _build(args: argparse.Namespace) -> Platform:
    config = load_config(args.config) if getattr(args, "config", None) else default_config()
    if getattr(args, "storage_root", None):
        return build_platform(config, storage_root=args.storage_root)
    return Platform(config=config)


def _dump(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _cmd_serve(args: argparse.Namespace) -> int:
    platform = _build(args)
    server = ConsoleServer(platform, host=args.host, port=args.port)
    base_url = server.start()
    payload: dict = {"listening": base_url, "pages": list(platform.config.pages)}
    if args.probe:
        payload["probe"] = probe_pages(base_url, platform.config.pages)
    _dump(payload)
    if args.duration > 0:
        try:
            time.sleep(args.duration)
        except KeyboardInterrupt:  # pragma: no cover - 交互路径
            pass
        server.stop()
        return 0
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:  # pragma: no cover - 交互路径
        server.stop()
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    clock = ManualClock() if args.fast else SystemClock()
    config = load_config(args.config) if args.config else default_config()
    platform = build_platform(config, clock=clock, storage_root=args.storage_root)
    # 演示从干净基线起步：先清掉上一轮落盘的引风机状态，避免继承半程工况。
    platform.reset_state()

    startup = platform.start()
    cycles = [
        platform.cycle(
            CycleInputs(
                load=0.55 + 0.4 * (index / max(1, args.cycles - 1)),
                nox_target=42.0,
                ammonia_concentration_ppm=4.0 if index % 4 else 9.0,
                dt=1.0,
            )
        )
        for index in range(args.cycles)
    ]
    concurrency = platform.concurrency_probe(rounds=args.rounds)
    calibration = platform.calibration_probe(offset_mg_m3=6.5)
    shutdown = platform.stop()

    server = ConsoleServer(platform)
    base_url = server.start()
    probe = probe_pages(base_url, platform.config.pages)
    server.stop()

    report = {
        "startup": startup,
        "cycles_run": len(cycles),
        "last_cycle": cycles[-1],
        "concurrency": concurrency,
        "calibration": calibration,
        "shutdown": shutdown,
        "probe": probe,
        "audit": platform.audit.summary(),
    }
    _dump(report)
    failures = list(startup["violations"]) + list(shutdown["violations"])
    failures += [name for name, item in probe.items() if not item["ok"]]
    if failures:
        print(f"检查未通过: {failures}", file=sys.stderr)
        return 1
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    platform = _build(args)
    payload = platform.status()
    payload["topology"] = platform.topology_summary()
    payload["namespaces"] = platform.namespaces.describe()
    payload["trend"] = platform.trend.snapshot()
    _dump(payload)
    return 0


def _cmd_config(args: argparse.Namespace) -> int:
    config = load_config(args.config) if args.config else default_config()
    _dump(config_to_mapping(config))
    return 0


def _cmd_reset(args: argparse.Namespace) -> int:
    platform = _build(args)
    removed = platform.reset_state()
    _dump({"storage_root": platform.store.root, "fan_state_removed": removed})
    return 0


def build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器。"""

    parser = argparse.ArgumentParser(
        prog="fgd", description="燃煤电厂烟气脱硫脱硝与除尘控制平台"
    )
    parser.add_argument("--config", help="YAML 配置文件路径")
    parser.add_argument("--storage-root", help="覆盖状态存储目录")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="启动控制台服务")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--duration", type=float, default=0.0, help="运行多少秒后退出，0 表示常驻")
    serve.add_argument("--probe", action="store_true", help="启动后探测页面")
    serve.set_defaults(func=_cmd_serve)

    demo = sub.add_parser("demo", help="跑一遍完整流程")
    demo.add_argument("--cycles", type=int, default=DEFAULT_DEMO_CYCLES)
    demo.add_argument("--rounds", type=int, default=24, help="并发探测轮数")
    demo.add_argument("--fast", action="store_true", help="使用可推进时钟，跳过真实等待")
    demo.set_defaults(func=_cmd_demo)

    status = sub.add_parser("status", help="打印状态快照")
    status.set_defaults(func=_cmd_status)

    config = sub.add_parser("config", help="打印生效配置")
    config.set_defaults(func=_cmd_config)

    reset = sub.add_parser("reset", help="清掉落盘的引风机状态")
    reset.set_defaults(func=_cmd_reset)
    return parser


def main(argv: list[str] | None = None) -> int:
    """命令行主入口。"""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except FgdError as error:
        print(json.dumps(error.as_record(), ensure_ascii=False), file=sys.stderr)
        return 2
