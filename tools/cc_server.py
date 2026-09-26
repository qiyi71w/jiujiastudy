#!/usr/bin/env python3
"""One account runtime: independent scheduling, optional Telegram, authenticated Web."""
import argparse
import os
import signal
import sys
import threading

from cc_account import AccountService
from cc_store import FileLock, jload
from cc_telegram import TelegramBot


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="救驾账号服务")
    parser.add_argument("--home", required=True)
    parser.add_argument("--secrets", required=True)
    parser.add_argument("--listen", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    if not os.path.isabs(args.home) or not os.path.isabs(args.secrets):
        parser.error("档案与秘密路径必须为绝对路径")
    service = AccountService(args.home, args.secrets)
    stop = threading.Event()
    bot = TelegramBot(service, service.secrets.telegram_bot_token) if service.secrets.telegram_bot_token else None
    origin = jload(os.path.join(args.home, "config.json")).get("service", {}).get("web_origin")
    server = None
    if origin:
        from waitress import create_server
        from cc_web import create_app
        server = create_server(create_app(service, origin), host=args.listen, port=args.port,
                               threads=8, connection_limit=64, channel_timeout=180,
                               max_request_body_size=32768, clear_untrusted_proxy_headers=True)
    elif not bot:
        raise ValueError("需要配置网站地址或 Telegram")

    def schedule():
        while not stop.is_set():
            try:
                if bot:
                    bot._scheduled_once()
                else:
                    with FileLock(os.path.join(args.home, "service-telegram-scheduled.lock")):
                        service.scheduled_tick()
            except Exception:
                sys.stderr.write("计划扫描未完成；保留已有数据，稍后重试。\n")
            stop.wait(15)

    def telegram():
        while not stop.is_set():
            try:
                bot.run(scheduled=False)
            except Exception:
                sys.stderr.write("Telegram 暂不可用；网站与计划扫描继续运行。\n")
            if not stop.is_set():
                stop.wait(30)

    def shutdown(signum, frame):
        stop.set()
        if bot:
            bot.stop()
        if server:
            server.close()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, shutdown)
    threading.Thread(target=schedule, name="account-schedule", daemon=True).start()
    if bot:
        threading.Thread(target=telegram, name="account-telegram", daemon=True).start()
    print("账号服务已启动", flush=True)
    try:
        if server:
            server.run()
        else:
            stop.wait()
    finally:
        shutdown(None, None)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.stderr.write("账号服务启动失败；请检查受限配置与网站地址。\n")
        sys.exit(2)
