"""
MetaCore 后端服务启动脚本（生产/开发通用）

存放位置: meta_core/run_server.py （与 app.py 同级）

功能:
  1. 自动注入 PYTHONPATH（保证 meta_core 包可被 import）
  2. 解析命令行参数（host/port/reload/log-level/workers）
  3. 启动前预检（依赖、配置目录、池目录）
  4. 启动 uvicorn（带统一的 logging 配置）
  5. Ctrl+C 优雅退出

使用:
  python run_server.py                          # 默认 127.0.0.1:8000
  python run_server.py --port 8080 --host 0.0.0.0
  python run_server.py --reload                 # 监听文件变更（开发用）
  python run_server.py --log-level debug
  python run_server.py --check                  # 仅做预检，不启动
"""
import argparse
import logging
import os
import sys
import time
from pathlib import Path

# ─── Windows GBK 编码修正（emoji 字符能正确打印） ──────────────────
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ─── 路径预注入（在 import uvicorn / meta_core 之前） ───────────────
# 本脚本位于 meta_core/scripts/run_server.py，故：
#   SCRIPT_DIR = .../meta_core/scripts/
#   BASE_DIR   = .../meta_core/ 的父目录（项目根）
SCRIPT_DIR = Path(__file__).resolve().parent          # meta_core/scripts/
BASE_DIR = SCRIPT_DIR.parent.parent                   # 项目根（含 Lib、TDX dll 等）
META_CORE_DIR = SCRIPT_DIR.parent                     # meta_core/
WEB_DIR = META_CORE_DIR / "web"

for p in (str(BASE_DIR), str(META_CORE_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)
os.environ.setdefault("PYTHONPATH", str(BASE_DIR))


# ─── Banner ───────────────────────────────────────────────────────
BANNER = """
╔══════════════════════════════════════════════════════════════╗
║           MetaCore Stock Pool Platform Server                ║
║           http://{host}:{port}                                ║
║           API docs: http://{host}:{port}/docs                ║
╚══════════════════════════════════════════════════════════════╝
"""


def setup_logging(level: str, log_file: str | None = None) -> None:
    """统一 logging 配置：控制台 +（可选）文件"""
    fmt = "%(asctime)s | %(levelname)-7s | %(name)-22s | %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
        handlers=handlers,
        force=True,
    )
    # 抑制 uvicorn 默认重复 access log
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def preflight_checks(skip: bool = False) -> dict:
    """
    启动前预检。返回状态字典。
    skip=True 时全部返回 ok（用于生产/容器环境）。
    """
    report: dict = {
        "python": "", "uvicorn": "", "fastapi": "", "meta_core": "",
        "web_dir": "", "errors": [], "warnings": [],
    }

    # 1. Python 版本
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    report["python"] = py_ver
    if sys.version_info < (3, 9):
        report["errors"].append(f"Python 3.9+ required, got {py_ver}")

    if skip:
        return report

    # 2. 关键依赖
    for mod in ("uvicorn", "fastapi"):
        try:
            __import__(mod)
            report[mod] = "ok"
        except ImportError as e:
            report[mod] = f"MISSING: {e}"
            report["errors"].append(f"缺少依赖 {mod}: pip install {mod}")

    # 3. meta_core 可导入
    try:
        from meta_core.app import app  # noqa: F401
        report["meta_core"] = "ok"
    except Exception as e:
        report["meta_core"] = f"FAIL: {e}"
        report["errors"].append(f"meta_core.app 导入失败: {e}")

    # 4. web 目录
    if WEB_DIR.is_dir():
        report["web_dir"] = f"ok ({len(list(WEB_DIR.glob('**/*')))} files)"
    else:
        report["web_dir"] = "MISSING"
        report["errors"].append(f"web 目录不存在: {WEB_DIR}")

    # 5. dzhpool/tdxpool 目录（可选但常用）
    for sub in ("dzhpool", "tdxpool"):
        d = META_CORE_DIR / sub
        if d.is_dir():
            count = len(list(d.glob("*.xml")))
            report[sub] = f"{count} XML files"
        else:
            report[sub] = "absent (ok)"

    return report


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="run_server.py",
        description="MetaCore Stock Pool Platform Server",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--host", default=os.environ.get("META_CORE_HOST", "127.0.0.1"),
                   help="监听地址（0.0.0.0 表示所有网卡）")
    p.add_argument("--port", type=int, default=int(os.environ.get("META_CORE_PORT", "8000")),
                   help="监听端口")
    p.add_argument("--reload", action="store_true",
                   help="启用热重载（仅开发用）")
    p.add_argument("--workers", type=int, default=1,
                   help="worker 数量（>1 时不能用 --reload）")
    p.add_argument("--log-level", default="info",
                   choices=["debug", "info", "warning", "error", "critical"],
                   help="日志级别")
    p.add_argument("--log-file", default=None,
                   help="日志文件路径（不指定则只输出到控制台）")
    p.add_argument("--check", action="store_true",
                   help="仅运行预检，不启动服务器")
    p.add_argument("--skip-preflight", action="store_true",
                   help="跳过预检（生产/容器环境）")
    return p.parse_args()


def print_preflight(report: dict) -> bool:
    """打印预检报告。返回 True 表示通过（可继续启动）。"""
    print("\n── Pre-flight Checks ─────────────────────────────────────")
    for key in ("python", "uvicorn", "fastapi", "meta_core", "web_dir", "dzhpool", "tdxpool"):
        if key in report:
            status = report[key]
            mark = "✓" if status in ("ok",) or status.endswith("files") or "ok (" in status else ("⚠" if status.startswith("absent") else "✗")
            print(f"  {mark} {key:<12s}: {status}")
    if report.get("warnings"):
        print("\n  ⚠ Warnings:")
        for w in report["warnings"]:
            print(f"    - {w}")
    if report.get("errors"):
        print("\n  ✗ Errors:")
        for e in report["errors"]:
            print(f"    - {e}")
    print("──────────────────────────────────────────────────────────\n")
    return not report["errors"]


def main() -> int:
    args = parse_args()

    # 日志先于一切初始化（uvicorn 也会用它）
    setup_logging(args.log_level, args.log_file)
    logger = logging.getLogger("run_server")

    logger.info("启动参数: host=%s port=%d reload=%s workers=%d log_level=%s",
                args.host, args.port, args.reload, args.workers, args.log_level)
    logger.info("脚本位置: %s", SCRIPT_DIR)

    # 预检
    report = preflight_checks(skip=args.skip_preflight)
    passed = print_preflight(report)
    if not passed:
        logger.error("预检未通过，请先解决上述错误")
        return 1
    if args.check:
        logger.info("仅预检模式，退出")
        return 0

    # Banner
    print(BANNER.format(host=args.host, port=args.port))

    # 启动 uvicorn
    try:
        import uvicorn
    except ImportError:
        logger.error("uvicorn 未安装: pip install uvicorn[standard]")
        return 1

    if args.reload and args.workers > 1:
        logger.warning("--reload 与 --workers>1 冲突，workers 已置为 1")
        args.workers = 1

    config = uvicorn.Config(
        "meta_core.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=args.workers if not args.reload else 1,
        log_level=args.log_level,
        access_log=False,  # 由我们的 logger 统一处理
        timeout_graceful_shutdown=10,
    )
    server = uvicorn.Server(config)

    logger.info("服务器启动中…  访问 http://%s:%d", args.host, args.port)
    logger.info("API 文档:        http://%s:%d/docs", args.host, args.port)
    logger.info("按 Ctrl+C 停止")
    t0 = time.time()
    try:
        server.run()
    except KeyboardInterrupt:
        logger.info("收到中断信号，关闭中…")
    finally:
        logger.info("服务器已停止 (运行了 %.1fs)", time.time() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
