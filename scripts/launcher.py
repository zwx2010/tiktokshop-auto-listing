#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一键启动器 —— 服务器 / 飞书隧道 / 依赖 / 健康检查。

用法（在项目根目录）：
    python scripts/launcher.py            # 交互菜单（回车默认启动服务器）
    python scripts/launcher.py --server   # 直接启动服务器
    python scripts/launcher.py --tunnel   # 直接启动飞书隧道
    python scripts/launcher.py --all      # 服务器 + 隧道
    python scripts/launcher.py --deps     # 安装依赖
    python scripts/launcher.py --check    # 健康检查
    python scripts/launcher.py --port 8000

启动服务器流程：先探活 8000，已在跑则直接开浏览器；否则拉起 uvicorn 子进程，
轮询 /dashboard/ 到 200 后自动打开浏览器。日志写 data/launcher.log。
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.request import urlopen

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_FILE = BASE_DIR / "data" / "launcher.log"
TUNNEL_STATE = BASE_DIR / "data" / "tunnel_url.txt"
DEFAULT_PORT = 8000
CPOLAR_ALT = Path(r"D:\develop\cpolar\cpolar")

# cpolar 免费版每次启动 URL 会随机变化。启动时用 -log stdout 把公网 URL
# 打到 stdout 解析出来；和上次记录的 URL 对比，变了就提示更新飞书后台回调。
_TUNNEL_URL_RE = re.compile(
    r"(https?://[a-zA-Z0-9._-]+\.cpolar(?:\.[a-z]{2,})?)"
)

_PKG_CANDIDATES = [
    BASE_DIR.parent / "RoseSeek_TikTokShop_AI_Localized_20260809",
    Path(os.environ.get("ROSEEK_PKG_DIR", "")) if os.environ.get("ROSEEK_PKG_DIR") else None,
]


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    print(line)


def _roseek_pkg_dir():
    for p in _PKG_CANDIDATES:
        if p and p.is_dir():
            return p
    return None


def _server_env(port: int) -> dict:
    env = os.environ.copy()
    env["PLATFORM_DIR"] = str(BASE_DIR)
    pkg = _roseek_pkg_dir()
    if pkg:
        env["ROSEEK_PKG_DIR"] = str(pkg)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _http_ok(port: int, path="/dashboard/", timeout=2.0) -> bool:
    url = f"http://127.0.0.1:{port}{path}"
    try:
        with urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _check_port_free(port: int) -> bool:
    """端口是否空闲。被占返回 False（服务可能在跑）。"""
    if _http_ok(port):
        return False
    import socket
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def start_server(port: int) -> int:
    if _http_ok(port):
        log(f"端口 {port} 服务已在运行 → 直接打开浏览器")
        webbrowser.open(f"http://127.0.0.1:{port}/dashboard/")
        return 0
    cmd = [sys.executable, "-m", "uvicorn", "app.main:app",
           "--host", "127.0.0.1", "--port", str(port)]
    log("启动 uvicorn 服务器 ...")
    proc = subprocess.Popen(cmd, cwd=str(BASE_DIR), env=_server_env(port))
    deadline = time.time() + 30
    while time.time() < deadline:
        if _http_ok(port):
            log(f"服务已就绪 http://127.0.0.1:{port}/dashboard/  → 打开浏览器")
            webbrowser.open(f"http://127.0.0.1:{port}/dashboard/")
            return 0
        if proc.poll() is not None:
            log(f"[失败] uvicorn 进程退出，退出码 {proc.returncode}。见上方日志。")
            return 1
        time.sleep(0.8)
    log("[失败] 30 秒内未就绪，请查看上方 uvicorn 报错。")
    return 1


def find_cpolar() -> str | None:
    p = shutil.which("cpolar")
    if p:
        return p
    if CPOLAR_ALT.exists():
        return str(CPOLAR_ALT)
    return None


def _read_last_tunnel_url() -> str:
    try:
        return TUNNEL_STATE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _write_tunnel_url(url: str) -> None:
    try:
        TUNNEL_STATE.parent.mkdir(parents=True, exist_ok=True)
        TUNNEL_STATE.write_text(url, encoding="utf-8")
    except OSError:
        pass


def _print_callback_hint(url: str, changed: bool) -> None:
    """公网 URL 定了之后,打印飞书后台配置提示(免费版每次 URL 随机)。"""
    print("=" * 62)
    print(f"  公网地址: {url}")
    if changed:
        print("  !! cpolar 免费版 URL 每次启动会变,本次与上次不同 !!")
        print("  → 需要去飞书开放平台更新这两个回调地址:")
    else:
        print("  URL 与上次相同,飞书后台无需改动。")
    print(f"    事件订阅回调 : {url}/api/feishu/webhook")
    print(f"    交互卡片回调 : {url}/api/feishu/card")
    print("  若回调地址没配过,先到飞书后台『事件订阅』把地址配上,并发布新版本。")
    print("=" * 62)


def start_tunnel(port: int) -> int:
    cpolar = find_cpolar()
    if not cpolar:
        log("[失败] 未找到 cpolar（PATH 或 D:\\develop\\cpolar\\cpolar），无法启动飞书隧道。")
        return 1
    if not _http_ok(port):
        log("注意：服务器未就绪，隧道会建立但回调 502。先启动服务器（选项 1）。")
    log("启动 cpolar http 隧道（公网 → 本机飞书回调）...")
    proc = subprocess.Popen([cpolar, "http", str(port), "-log", "stdout"],
                            cwd=str(BASE_DIR), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace")
    last_url = _read_last_tunnel_url()
    found_url = ""
    print("  cpolar 日志（按 Ctrl+C 停止隧道）：")
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            print("   " + line)
            m = _TUNNEL_URL_RE.search(line)
            if m:
                # cpolar 隧道同时提供 http/https 指向同一主机;统一用 https,
                # 避免先到 http 后到 https 造成提示与落盘不一致
                url = m.group(1).replace("http://", "https://", 1)
                if url != found_url:
                    found_url = url
                    _write_tunnel_url(url)
                    log(f"公网地址(回调填这个): {url}")
                    _print_callback_hint(url, changed=(url != last_url))
    except KeyboardInterrupt:
        proc.kill()
        log("隧道已停止。")
        return 0
    return 0


def install_deps() -> int:
    req = BASE_DIR / "requirements.txt"
    if not req.exists():
        log("[失败] 找不到 requirements.txt")
        return 1
    log("安装依赖 ...")
    code = subprocess.call([sys.executable, "-m", "pip", "install", "-r", str(req)])
    log("依赖安装完成" if code == 0 else f"依赖安装失败，pip 退出码 {code}")
    return code


def health_check() -> int:
    ok = True
    log("== 健康检查 ==")
    if _http_ok(DEFAULT_PORT):
        log("  服务器  : OK  (http://127.0.0.1:%d/dashboard/)" % DEFAULT_PORT)
    else:
        log("  服务器  : 未运行（先选 1 启动）")
        ok = False
    for name, p in [("feishu.local.json", BASE_DIR / "config" / "feishu.local.json"),
                    ("market_pricing.json", BASE_DIR / "config" / "market_pricing.json"),
                    ("listing_defaults.json", BASE_DIR / "config" / "listing_defaults.json"),
                    ("platform.db", BASE_DIR / "data" / "platform.db"),
                    ("rag_corpus.db", BASE_DIR / "data" / "rag_corpus.db")]:
        if p.exists():
            log(f"  {name:<22}: OK")
        else:
            log(f"  {name:<22}: 缺失")
            ok = False
    pkg = _roseek_pkg_dir()
    log(f"  ROSEEK_PKG_DIR : {pkg if pkg else '未找到（同级的 RoseSeek_TikTokShop_AI_Localized_20260809）'}")
    cpolar = find_cpolar()
    log(f"  cpolar         : {cpolar if cpolar else '未找到'}")
    log("== 健康检查结束 ==" + ("（全部就绪）" if ok else "（有问题需处理）"))
    return 0 if ok else 1


def menu() -> None:
    while True:
        print()
        print("=" * 46)
        print("  TikTokShop 多账号自动化运营平台  — 一键启动")
        print("=" * 46)
        print("  1) 启动服务器（自动开浏览器）")
        print("  2) 启动飞书隧道 (cpolar http 8000)")
        print("  3) 一键全部（服务器 + 隧道）")
        print("  4) 安装依赖")
        print("  5) 健康检查")
        print("  0) 退出")
        choice = input("  请选择 [回车=1] : ").strip() or "1"
        if choice == "0":
            log("退出。")
            return
        if choice == "1":
            start_server(DEFAULT_PORT)
        elif choice == "2":
            start_tunnel(DEFAULT_PORT)
        elif choice == "3":
            start_server(DEFAULT_PORT)
            if input("  隧道是否也启动？[y/N]: ").strip().lower() in ("y", "yes"):
                start_tunnel(DEFAULT_PORT)
        elif choice == "4":
            install_deps()
        elif choice == "5":
            health_check()
        else:
            print("  无效选项。")
        print()


def main() -> int:
    ap = argparse.ArgumentParser(description="一键启动器")
    ap.add_argument("--server", action="store_true", help="直接启动服务器")
    ap.add_argument("--tunnel", action="store_true", help="直接启动飞书隧道")
    ap.add_argument("--all", action="store_true", help="服务器 + 隧道")
    ap.add_argument("--deps", action="store_true", help="安装依赖")
    ap.add_argument("--check", action="store_true", help="健康检查")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"端口（默认 {DEFAULT_PORT}）")
    args = ap.parse_args()

    if args.all:
        return start_server(args.port) or start_tunnel(args.port)
    if args.server:
        return start_server(args.port)
    if args.tunnel:
        return start_tunnel(args.port)
    if args.deps:
        return install_deps()
    if args.check:
        return health_check()
    menu()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("已中断。")
        sys.exit(130)
