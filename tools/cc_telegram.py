#!/usr/bin/env python3
"""Telegram Bot adapter for the account service.

Bounded, authenticated long-polling Telegram Bot adapter for Canvas Account Service.
Restricted to a single pre-configured private user, rejecting all unauthorized messages,
groups, and arbitrary routes or recipients.
"""
import argparse
import html
import json
import os
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import brand
from cc_account import COMMANDS
from cc_service_security import _StrictNoRedirectHandler
from cc_store import FileLock
REFRESH_ACK = "正在刷新 Canvas 数据，请稍候…"
REFRESH_FAIL = "刷新失败，本次未生成完整日报；上次成功快照保留。"
GENERIC_OP_FAIL = "操作失败，请稍后重试。"
MAX_UTF16_CHUNK = 3000




def _utf16_units(s: str) -> int:
    """Returns the number of UTF-16 code units in a string."""
    return len(s.encode("utf-16-le")) // 2


def _neutralize(text: str) -> str:
    """Neutralizes @ mentions to fullwidth ＠ and removes control and bidi characters."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("@", "＠")
    cleaned = []
    for ch in text:
        if ch in ("\n", "\t"):
            cleaned.append(ch)
        elif unicodedata.category(ch) in ("Cc", "Cf"):
            continue
        else:
            cleaned.append(ch)
    return "".join(cleaned)


def _split_utf16_units(text: str, max_units: int = MAX_UTF16_CHUNK) -> list[str]:
    """Splits plain text into chunks of at most max_units UTF-16 code units."""
    if not text:
        return []
    lines = text.splitlines(keepends=True)
    chunks: list[str] = []
    curr: list[str] = []
    curr_units = 0

    for line in lines:
        line_units = _utf16_units(line)
        if line_units > max_units:
            for ch in line:
                ch_units = 1 if ord(ch) <= 0xFFFF else 2
                if curr_units + ch_units > max_units and curr:
                    chunks.append("".join(curr))
                    curr = []
                    curr_units = 0
                curr.append(ch)
                curr_units += ch_units
        else:
            if curr_units + line_units > max_units and curr:
                chunks.append("".join(curr))
                curr = []
                curr_units = 0
            curr.append(line)
            curr_units += line_units

    if curr:
        chunks.append("".join(curr))
    return chunks


class TelegramBot:
    """Long-polling Telegram Bot adapter bound to a specific AccountService and user."""

    def __init__(self, service: Any, token: str, bot_username: str | None = None, retry_delay: float = 3.0):
        if not token or not isinstance(token, str) or not token.strip():
            raise ValueError("Invalid Telegram bot token")
        if not hasattr(service, "user_id") or not hasattr(service, "execute"):
            raise ValueError("Invalid AccountService instance")

        self.service = service
        self.token = token.strip()
        self.bot_username = bot_username
        self.retry_delay = retry_delay
        self.bot_id: int | None = None
        self._stopped = False
        self._opener = urllib.request.build_opener(_StrictNoRedirectHandler())

    def request(self, method: str, payload: dict | None = None) -> Any:
        """Internal method: POST JSON to https://api.telegram.org/botTOKEN/METHOD.

        Strictly rejects redirects and never logs or exposes raw exceptions, URLs,
        or Telegram API error descriptions.
        """
        method = method.strip().lstrip("/")
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        body = json.dumps(payload or {}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )

        timeout = 35.0
        if isinstance(payload, dict) and "timeout" in payload:
            try:
                timeout = max(35.0, float(payload["timeout"]) + 15.0)
            except (TypeError, ValueError):
                timeout = 35.0

        failed = False
        try:
            with self._opener.open(req, timeout=timeout) as resp:
                data = resp.read()
        except Exception:
            failed = True

        if failed:
            raise ValueError("Telegram API request failed")

        parse_failed = False
        try:
            res = json.loads(data.decode("utf-8"))
        except Exception:
            parse_failed = True

        if parse_failed:
            raise ValueError("Telegram API response was not valid JSON")

        if not isinstance(res, dict) or not res.get("ok"):
            raise ValueError("Telegram API call returned error")

        return res.get("result")

    def send(self, text: str, actions=None) -> None:
        """Sends sanitized text to the fixed bound chat (self.service.user_id).

        Neutralizes @ and control/bidi characters, splits BEFORE escaping into chunks
        of <=3000 UTF-16 units, wraps chunks in HTML <pre> to keep content inert,
        and disables link previews without reply_markup.
        """
        if not text:
            return

        cleaned = _neutralize(text)
        chunks = _split_utf16_units(cleaned, max_units=MAX_UTF16_CHUNK)
        for index, chunk in enumerate(chunks):
            if not chunk:
                continue
            escaped = html.escape(chunk)
            formatted = f"<pre>{escaped}</pre>"
            payload = {
                "chat_id": int(self.service.user_id),
                "text": formatted,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            if actions and index == len(chunks) - 1:
                payload["reply_markup"] = {"inline_keyboard": [[{"text": _neutralize(button["text"]), "callback_data": button["data"]}
                                                                for button in row] for row in actions]}
            self.request("sendMessage", payload)

    def parse_command(self, text: str) -> tuple[str | None, list[str], str | None]:
        parts = text.strip().split()
        if not parts:
            return None, [], None
        first, marker, suffix = parts[0].partition("@")
        if marker and (not self.bot_username or suffix.lower() != self.bot_username.lower()):
            return None, [], None
        if first.lower() == "/canvas":
            parts = parts[1:]
            if not parts:
                return "help", [], None
            command = parts[0].lower()
        else:
            command = first.removeprefix("/").lower()
        if command not in COMMANDS:
            return None, [], "未知命令；使用 /canvas help 查看可用操作。"
        params = parts[1:]
        if params and not (command == "tasks" and params == ["stopped"] or command == "schedule" and len(params) == 2):
            return None, [], "该命令不接受这些参数。"
        return command, params, None

    def handle_update(self, update: dict, executor=None) -> None:
        """Handles an incoming Telegram update.

        Authenticates that message from.id == service.user_id, chat.id == service.user_id,
        chat.type == 'private', and sender is not a bot.
        Unauthorized or group messages are silently ignored with NO outbound requests.
        """
        if not isinstance(update, dict):
            return
        callback = update.get("callback_query")
        if isinstance(callback, dict):
            message = callback.get("message")
            chat = message.get("chat", {}) if isinstance(message, dict) else {}
            sender = callback.get("from", {})
            try:
                authorized = (chat.get("type") == "private" and int(chat.get("id")) == int(self.service.user_id)
                              and int(sender.get("id")) == int(self.service.user_id) and not sender.get("is_bot"))
            except (TypeError, ValueError, AttributeError):
                authorized = False
            if authorized and isinstance(callback.get("id"), str):
                try:
                    result = (self.service.execute(int(self.service.user_id), "schedule", [], "telegram", str(update.get("update_id", "")))
                              if callback.get("data") == "schedule:help" else
                              self.service.task_action(int(self.service.user_id), callback.get("data")))
                    self.request("answerCallbackQuery", {"callback_query_id": callback["id"]})
                    self.send(result["text"], result.get("actions"))
                except Exception:
                    sys.stderr.write("Telegram 按钮处理失败。\n")
            return

        message = update.get("message")
        if not isinstance(message, dict):
            return

        chat = message.get("chat")
        from_user = message.get("from")
        if not isinstance(chat, dict) or not isinstance(from_user, dict):
            return

        # Sender and chat verification
        if chat.get("type") != "private":
            return
        if from_user.get("is_bot", False):
            return

        try:
            from_id = int(from_user.get("id"))
            chat_id = int(chat.get("id"))
            configured_uid = int(self.service.user_id)
        except (TypeError, ValueError):
            return

        if from_id != configured_uid or chat_id != configured_uid:
            return

        text = message.get("text")
        if not isinstance(text, str):
            return

        update_id = update.get("update_id", "")

        cmd, params, err = self.parse_command(text)
        if err:
            self.send(err)
            return
        if not cmd:
            return

        if cmd == "refresh":
            self.send(REFRESH_ACK)
        if cmd == "refresh" and executor is not None:
            executor.submit(self._execute_command, configured_uid, cmd, params, str(update_id))
        else:
            self._execute_command(configured_uid, cmd, params, str(update_id))

    def _execute_command(self, identity, command, params, correlation_id):
        try:
            result = self.service.execute(
                identity=identity, command=command, params=params,
                channel="telegram", correlation_id=correlation_id,
            )
        except Exception:
            self.send(REFRESH_FAIL if command == "refresh" else GENERIC_OP_FAIL)
            return
        try:
            # Serialize report delivery, not collection. Re-read under the delivery
            # lock so overlapping commands cannot repeat acknowledged changes.
            with FileLock(os.path.join(self.service.home, "service-telegram-delivery.lock")):
                if command in ("refresh", "report"):
                    result = self.service.execute(identity, "report", [], "telegram", correlation_id)
                self.send(result["text"], result.get("actions"))
                self.service.acknowledge_delivery(result)
        except Exception:
            sys.stderr.write("Telegram 命令处理或投递失败；可重新请求快照。\n")

    def _scheduled_once(self):
        try:
            with FileLock(os.path.join(self.service.home, "service-telegram-scheduled.lock")):
                due = self.service.scheduled_tick()
                if due:
                    try:
                        self.send(due["text"])
                    except Exception:
                        self.service.scheduled_delivery(due["day"], False)
                        return
                    self.service.scheduled_delivery(due["day"], True)
        except Exception:
            sys.stderr.write("Telegram 每日扫描或投递失败；将继续重试。\n")

    def stop(self) -> None:
        """Signals the long polling loop to stop."""
        self._stopped = True

    def run(self) -> None:
        """Executes long polling loop against the Telegram Bot API.

        Verifies bot identity via getMe, verifies no active webhook via getWebhookInfo
        (rejecting without deleting), registers the bound chat's command menu,
        and polls getUpdates with timeout=30 for messages and callback queries.
        """
        # 1. getMe verifies bot
        me = self.request("getMe")
        if not isinstance(me, dict) or not me.get("is_bot"):
            raise ValueError("Bot verification failed")
        self.bot_id = me.get("id")
        self.bot_username = me.get("username")

        # 2. getWebhookInfo reject existing webhook (do not delete it)
        webhook_info = self.request("getWebhookInfo")
        if isinstance(webhook_info, dict) and webhook_info.get("url"):
            raise ValueError("Webhook is active; long polling rejected")

        self.request("setMyCommands", {
            "commands": [{"command": name, "description": description}
                         for name, description in COMMANDS.items()],
            "scope": {"type": "chat", "chat_id": int(self.service.user_id)},
        })
        self.request("setChatMenuButton", {
            "chat_id": int(self.service.user_id), "menu_button": {"type": "commands"},
        })

        # Polling and scheduled delivery use separate workers; neither blocks the other.
        with ThreadPoolExecutor(max_workers=4) as executor:
            offset: int | None = None
            scheduled = None
            while not self._stopped:
                if scheduled is None or scheduled.done():
                    scheduled = executor.submit(self._scheduled_once)
                payload: dict[str, Any] = {"timeout": 30, "allowed_updates": ["message", "callback_query"]}
                if offset is not None:
                    payload["offset"] = offset
                try:
                    updates = self.request("getUpdates", payload)
                except ValueError:
                    time.sleep(self.retry_delay)
                    continue
                except KeyboardInterrupt:
                    break
                if isinstance(updates, list):
                    for update in updates:
                        if isinstance(update, dict):
                            try:
                                self.handle_update(update, executor)
                            except Exception:
                                sys.stderr.write("Telegram 命令处理或投递失败；可重新请求快照。\n")
                            uid = update.get("update_id")
                            if uid is not None:
                                offset = uid + 1


def main(argv: list[str] | None = None) -> None:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=f"{brand.NAME} Telegram Adapter")
    parser.add_argument("--home", required=True, help="Absolute path to home directory")
    parser.add_argument("--secrets", required=True, help="Absolute path to secrets file")

    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        sys.exit(e.code if e.code is not None else 2)

    if not os.path.isabs(args.home) or not os.path.isabs(args.secrets):
        sys.stderr.write("参数路径必须为绝对路径。\n")
        sys.exit(2)

    try:
        from cc_account import AccountService

        service = AccountService(args.home, args.secrets)
        token = getattr(service.secrets, "telegram_bot_token", None)
        if not token or not isinstance(token, str) or not token.strip():
            sys.stderr.write("未找到有效的 Telegram Bot token。\n")
            sys.exit(2)

        bot = TelegramBot(service, token.strip())
        bot.run()
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception:
        sys.stderr.write("服务启动失败。\n")
        sys.exit(2)


if __name__ == "__main__":
    main()
