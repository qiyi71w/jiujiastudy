#!/usr/bin/env python3
"""Run the configured account-private messaging channels against one account service."""
import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from cc_account import AccountService
from cc_telegram import TelegramBot


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Canvas account private-channel service")
    parser.add_argument("--home", required=True)
    parser.add_argument("--secrets", required=True)
    args = parser.parse_args(argv)
    if not os.path.isabs(args.home) or not os.path.isabs(args.secrets):
        parser.error("参数路径必须为绝对路径")
    try:
        service = AccountService(args.home, args.secrets)
        channels = service.secrets.identities
        if "discord" in channels:
            from cc_discord import DiscordBot
            discord_bot = DiscordBot(service)
        if "telegram" not in channels:
            discord_bot.run(service.secrets.discord_bot_token, log_handler=None)
        elif "discord" not in channels:
            TelegramBot(service, service.secrets.telegram_bot_token).run()
        else:
            telegram_bot = TelegramBot(service, service.secrets.telegram_bot_token)
            with ThreadPoolExecutor(max_workers=1) as executor:
                running = executor.submit(telegram_bot.run)
                try:
                    discord_bot.run(service.secrets.discord_bot_token, log_handler=None)
                finally:
                    telegram_bot.stop()
                running.result()
    except KeyboardInterrupt:
        return
    except Exception:
        sys.stderr.write("账号渠道启动失败；检查绑定、Bot 凭据和私信安装。\n")
        sys.exit(2)


if __name__ == "__main__":
    main()
