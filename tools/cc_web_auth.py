"""Web 认证模块：单账号用户名密码管理与服务端会话生命周期。

提供唯一的用户名密码凭据存储、基于 Argon2id 的安全哈希、会话管理以及持久化登录限速保护。
"""
from __future__ import annotations

import argparse
import datetime as dt
import getpass
import hashlib
import json
import os
import re
import secrets
import stat
import sys
import time
from typing import Any

from cc_store import FileLock, jsave

# Argon2id 密码哈希支持
try:
    import argon2
    from argon2 import PasswordHasher, Type
    from argon2.exceptions import VerificationError
except ImportError:  # pragma: no cover
    argon2 = None  # type: ignore[assignment]
    PasswordHasher = None  # type: ignore[assignment]
    Type = None  # type: ignore[assignment]
    VerificationError = Exception  # type: ignore[assignment]


# 用户名规则：3..64 字符，仅允许字母、数字、点、下划线、连字符
USERNAME_RE = re.compile(r"^[a-zA-Z0-9._-]{3,64}$")

# 会话配置
MAX_SESSIONS = 20
SESSION_TTL_HOURS = 12

# 限速配置：五次失败后，对后续密码验证串行限频，不拒绝正确凭据。
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_FAILURES = 5
RATE_LIMIT_DELAY_SECONDS = 5


class WebAuth:
    """管理单账号凭据与 Web 服务端会话。"""

    def __init__(self, home: str | os.PathLike, account_id: str) -> None:
        if not isinstance(account_id, str) or not account_id.strip():
            raise ValueError("账号标识必须为非空字符串")
        if not isinstance(home, (str, bytes, os.PathLike)) or not str(home).strip():
            raise ValueError("主目录路径必须为非空路径")

        self.home = os.path.abspath(home)
        self.account_id = account_id.strip()
        self.auth_path = os.path.join(self.home, "web-auth.json")
        self.lock_path = os.path.join(self.home, ".web-auth.lock")
        self.verify_lock_path = os.path.join(self.home, ".web-auth-verify.lock")

    @staticmethod
    def _now_utc() -> dt.datetime:
        """获取当前 UTC 时间，若有 cc_time 钉住时间则优先遵循。"""
        try:
            from cc_time import Clock
            return Clock({}).now_utc()
        except Exception:
            return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)

    @staticmethod
    def _parse_iso_utc(val: Any) -> dt.datetime:
        """解析 ISO-8601 UTC 字符串或时间戳。"""
        if isinstance(val, (int, float)):
            return dt.datetime.fromtimestamp(val, tz=dt.timezone.utc)
        if isinstance(val, str):
            s = val.replace("Z", "+00:00")
            t = dt.datetime.fromisoformat(s)
            if t.tzinfo is None:
                t = t.replace(tzinfo=dt.timezone.utc)
            return t.astimezone(dt.timezone.utc)
        raise ValueError("无效的时间格式")

    @classmethod
    def _get_hasher(cls) -> Any:
        """构建标准 Argon2id hasher (64MiB memory, time=3, parallelism=1)。"""
        if PasswordHasher is None:
            raise RuntimeError("argon2-cffi 依赖缺失，请先安装 argon2-cffi")
        return PasswordHasher(
            time_cost=3,
            memory_cost=65536,  # 64 MiB
            parallelism=1,
            hash_len=32,
            salt_len=16,
            type=Type.ID if Type is not None else 2,
        )

    def _check_file_security(self, path: str) -> None:
        """验证文件安全性：禁止符号链接、必须为常规文件且权限受限 (0600)。"""
        if not os.path.lexists(path):
            return

        if os.path.islink(path):
            raise PermissionError("存储文件不安全：禁止使用符号链接")

        try:
            st = os.lstat(path)
        except OSError:
            raise PermissionError("无法读取存储文件属性")

        if stat.S_ISLNK(st.st_mode):
            raise PermissionError("存储文件不安全：禁止使用符号链接")

        if not stat.S_ISREG(st.st_mode):
            raise ValueError("存储文件必须是常规文件")

        if os.name != "nt" and (st.st_mode & 0o077) != 0:
            raise PermissionError("存储文件权限过宽：仅允许所有者访问 (0600)")

    def _load_data(self, must_exist: bool = True) -> dict[str, Any] | None:
        """安全读取并校验 web-auth.json。"""
        if not os.path.lexists(self.auth_path):
            if must_exist:
                raise PermissionError("存储文件不存在")
            return None

        self._check_file_security(self.auth_path)

        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC

        try:
            fd = os.open(self.auth_path, flags)
        except OSError:
            if os.path.islink(self.auth_path):
                raise PermissionError("存储文件不安全：禁止使用符号链接")
            raise

        with os.fdopen(fd, "r", encoding="utf-8") as f:
            st = os.fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode):
                raise ValueError("存储文件必须是常规文件")
            if os.name != "nt" and (st.st_mode & 0o077) != 0:
                raise PermissionError("存储文件权限过宽：仅允许所有者访问 (0600)")
            content = f.read()

        try:
            data = json.loads(content)
        except Exception:
            raise ValueError("存储文件已损坏：无效的 JSON 格式")

        if not isinstance(data, dict):
            raise ValueError("存储文件已损坏：格式必须为 JSON 对象")

        for key in ("account_id", "username", "password_hash", "sessions"):
            if key not in data:
                raise ValueError("存储文件已损坏：缺少必要字段")

        if not isinstance(data["account_id"], str) or not data["account_id"].strip():
            raise ValueError("存储文件已损坏：account_id 字段无效")
        if not isinstance(data["username"], str) or not data["username"].strip():
            raise ValueError("存储文件已损坏：username 字段无效")
        if not isinstance(data["password_hash"], str) or not data["password_hash"].strip():
            raise ValueError("存储文件已损坏：password_hash 字段无效")
        if not isinstance(data["sessions"], dict):
            raise ValueError("存储文件已损坏：sessions 字段无效")

        if data["account_id"] != self.account_id:
            raise PermissionError("认证失败：账号标识不匹配")

        return data

    def _save_data(self, data: dict[str, Any]) -> None:
        """原子且受限写回 web-auth.json (0600 权限)。"""
        if os.path.lexists(self.auth_path):
            if os.path.islink(self.auth_path):
                raise PermissionError("存储文件不安全：禁止使用符号链接")
            st = os.lstat(self.auth_path)
            if os.name != "nt" and (st.st_mode & 0o077) != 0:
                raise PermissionError("存储文件权限过宽：仅允许所有者访问 (0600)")

        jsave(self.auth_path, data)
        if os.name != "nt" and os.path.exists(self.auth_path):
            try:
                os.chmod(self.auth_path, 0o600)
            except OSError:
                pass

    def _check_rate_limit(self, limit: dict[str, Any], now: dt.datetime) -> dt.datetime:
        """在失败密集时串行延迟验证；正确凭据始终有机会通过。"""
        # 旧版全局锁不得继续阻止合法登录。
        limit.pop("locked_until", None)
        next_at = limit.get("next_attempt_at")
        if next_at:
            try:
                next_dt = self._parse_iso_utc(next_at)
                delay = (next_dt - now).total_seconds()
                if 0 < delay <= RATE_LIMIT_DELAY_SECONDS:
                    time.sleep(delay)
                    now = max(self._now_utc(), next_dt)
            except (TypeError, ValueError, OverflowError):
                pass
        limit.pop("next_attempt_at", None)
        return now

    def _record_failure(self, limit: dict[str, Any], now: dt.datetime) -> None:
        """记录有界失败窗口；达到阈值后每次验证至少间隔五秒。"""
        cutoff = now - dt.timedelta(seconds=RATE_LIMIT_WINDOW_SECONDS)
        recent: list[str] = []
        for ts_str in limit.get("failures", [])[-RATE_LIMIT_MAX_FAILURES:]:
            try:
                if self._parse_iso_utc(ts_str) >= cutoff:
                    recent.append(ts_str)
            except (TypeError, ValueError, OverflowError):
                pass
        recent.append(now.strftime("%Y-%m-%dT%H:%M:%SZ"))
        limit["failures"] = recent[-RATE_LIMIT_MAX_FAILURES:]
        if len(recent) >= RATE_LIMIT_MAX_FAILURES:
            limit["next_attempt_at"] = (now + dt.timedelta(seconds=RATE_LIMIT_DELAY_SECONDS)).strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _reset_failures(data: dict[str, Any]) -> None:
        """认证成功后清理匿名登录失败状态。"""
        data["rate_limit"] = {"failures": []}

    def provision(self, username: str, password: str) -> None:
        """新建或重置账号凭据，清空全部会话及限速。"""
        if not isinstance(username, str) or not isinstance(password, str):
            raise ValueError("用户名和密码必须为字符串")
        if not USERNAME_RE.match(username):
            raise ValueError("用户名格式不合法：须为3至64位字母、数字、点、下划线或连字符")
        if not (12 <= len(password) <= 256):
            raise ValueError("密码长度须在12至256个字符之间")

        with FileLock(self.lock_path):
            if os.path.lexists(self.auth_path):
                existing = self._load_data(must_exist=True)
                if existing and existing.get("account_id") != self.account_id:
                    raise PermissionError("认证失败：账号标识不匹配")

            hasher = self._get_hasher()
            password_hash = hasher.hash(password)
            new_data = {
                "account_id": self.account_id,
                "username": username,
                "password_hash": password_hash,
                "sessions": {},
                "rate_limit": {"failures": []},
            }
            self._save_data(new_data)

    def login(self, username: str, password: str) -> dict[str, str]:
        """验证用户名密码并生成新会话。"""
        if not isinstance(username, str) or not isinstance(password, str):
            raise ValueError("用户名和密码必须为字符串")
        if not 1 <= len(username) <= 64 or not 1 <= len(password) <= 256:
            raise PermissionError("用户名或密码错误")

        # 独立串行化密码验证；等待期间不占会话状态锁。
        verify_lock = FileLock(self.verify_lock_path)
        if not verify_lock.acquire(blocking=False):
            raise PermissionError("登录尝试过于频繁，请稍后再试")
        try:
            with FileLock(self.lock_path):
                if not os.path.lexists(self.auth_path):
                    raise PermissionError("用户名或密码错误")
                data = self._load_data(must_exist=True)
                if not data:
                    raise PermissionError("用户名或密码错误")
                pending = dict(data.get("rate_limit") or {})

            now = self._check_rate_limit(pending, self._now_utc())
            with FileLock(self.lock_path):
                # 等待期间凭据可能变化；重新读取并验证最新状态。
                if not os.path.lexists(self.auth_path):
                    raise PermissionError("用户名或密码错误")
                data = self._load_data(must_exist=True)
                if not data:
                    raise PermissionError("用户名或密码错误")
                now = max(now, self._now_utc())
                limit = data.setdefault("rate_limit", {"failures": []})
                limit.pop("locked_until", None)
                limit.pop("next_attempt_at", None)

                hasher = self._get_hasher()
                try:
                    valid_password = hasher.verify(data["password_hash"], password)
                except VerificationError:
                    valid_password = False
                if not valid_password or not secrets.compare_digest(username.encode(), data["username"].encode()):
                    self._record_failure(limit, now)
                    self._save_data(data)
                    raise PermissionError("用户名或密码错误")

                self._reset_failures(data)
                sessions = data.get("sessions", {})
                valid_sessions: dict[str, Any] = {}
                for k, s in sessions.items():
                    exp_str = s.get("expires_at")
                    if exp_str:
                        try:
                            exp_dt = self._parse_iso_utc(exp_str)
                            if exp_dt > now:
                                valid_sessions[k] = s
                        except Exception:
                            pass

                if len(valid_sessions) >= MAX_SESSIONS:
                    sorted_keys = sorted(
                        valid_sessions.keys(),
                        key=lambda k: str(valid_sessions[k].get("created_at") or valid_sessions[k].get("expires_at") or "")
                    )
                    while len(valid_sessions) >= MAX_SESSIONS:
                        oldest_k = sorted_keys.pop(0)
                        valid_sessions.pop(oldest_k, None)

                token = secrets.token_urlsafe(32)
                token_key = hashlib.sha256(token.encode("utf-8")).hexdigest()
                csrf = secrets.token_urlsafe(32)
                expires_dt = now + dt.timedelta(hours=SESSION_TTL_HOURS)
                expires_at = expires_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

                valid_sessions[token_key] = {
                    "username": username,
                    "csrf": csrf,
                    "expires_at": expires_at,
                    "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
                data["sessions"] = valid_sessions
                self._save_data(data)

                return {
                    "token": token,
                    "csrf": csrf,
                    "username": username,
                    "expires_at": expires_at,
                }
        finally:
            verify_lock.release()

    def authenticate(self, token: str) -> dict[str, str]:
        """验证会话令牌合法性并返回会话详情（不含哈希）。"""
        if not isinstance(token, str) or not token.strip():
            raise ValueError("会话令牌无效")

        with FileLock(self.lock_path):
            if not os.path.lexists(self.auth_path):
                raise PermissionError("会话无效或已过期")

            data = self._load_data(must_exist=True)
            if not data:
                raise PermissionError("会话无效或已过期")

            token_key = hashlib.sha256(token.encode("utf-8")).hexdigest()
            sessions = data.get("sessions", {})
            if token_key not in sessions:
                raise PermissionError("会话无效或已过期")

            session_info = sessions[token_key]
            now = self._now_utc()
            try:
                exp_dt = self._parse_iso_utc(session_info.get("expires_at"))
            except Exception:
                del sessions[token_key]
                self._save_data(data)
                raise PermissionError("会话无效或已过期")

            if now >= exp_dt:
                del sessions[token_key]
                self._save_data(data)
                raise PermissionError("会话无效或已过期")

            return {
                "username": str(session_info.get("username", "")),
                "csrf": str(session_info.get("csrf", "")),
                "expires_at": str(session_info.get("expires_at", "")),
            }

    def logout(self, token: str) -> None:
        """撤销指定会话令牌。"""
        if not isinstance(token, str) or not token.strip():
            raise ValueError("会话令牌无效")

        with FileLock(self.lock_path):
            if not os.path.lexists(self.auth_path):
                return

            data = self._load_data(must_exist=True)
            if not data:
                return

            token_key = hashlib.sha256(token.encode("utf-8")).hexdigest()
            sessions = data.get("sessions", {})
            if token_key in sessions:
                del sessions[token_key]
                self._save_data(data)

    def change_password(self, token: str, current_password: str, new_password: str) -> None:
        """验证当前密码并修改密码，成功后撤销全部现有会话。"""
        if not isinstance(token, str) or not token.strip():
            raise ValueError("会话令牌无效")
        if not isinstance(current_password, str) or not current_password:
            raise ValueError("当前密码不能为空")
        if not isinstance(new_password, str):
            raise ValueError("新密码必须为字符串")
        if not (12 <= len(new_password) <= 256):
            raise ValueError("新密码长度须在12至256个字符之间")

        with FileLock(self.lock_path):
            if not os.path.lexists(self.auth_path):
                raise PermissionError("会话无效或已过期")

            data = self._load_data(must_exist=True)
            if not data:
                raise PermissionError("会话无效或已过期")

            token_key = hashlib.sha256(token.encode("utf-8")).hexdigest()
            sessions = data.get("sessions", {})
            if token_key not in sessions:
                raise PermissionError("会话无效或已过期")

            session_info = sessions[token_key]
            now = self._now_utc()
            try:
                exp_dt = self._parse_iso_utc(session_info.get("expires_at"))
            except Exception:
                del sessions[token_key]
                self._save_data(data)
                raise PermissionError("会话无效或已过期")

            if now >= exp_dt:
                del sessions[token_key]
                self._save_data(data)
                raise PermissionError("会话无效或已过期")
            limit = session_info.setdefault("password_limit", {"failures": []})
            next_at = limit.pop("next_attempt_at", None)
            if next_at:
                try:
                    if now < self._parse_iso_utc(next_at):
                        raise PermissionError("登录尝试过于频繁，请稍后再试")
                except (TypeError, ValueError, OverflowError):
                    pass

            hasher = self._get_hasher()
            try:
                hasher.verify(data["password_hash"], current_password)
            except VerificationError:
                self._record_failure(limit, now)
                self._save_data(data)
                raise PermissionError("用户名或密码错误")

            new_hash = hasher.hash(new_password)
            data["password_hash"] = new_hash
            data["sessions"] = {}
            self._reset_failures(data)
            self._save_data(data)


def main(argv: list[str] | None = None) -> int:
    """CLI 管理入口：支持通过 getpass 或 --password-stdin 初始化/重置账号密码。"""
    parser = argparse.ArgumentParser(prog="cc_web_auth", description="WebAuth CLI 管理工具")
    parser.add_argument("--home", required=True, help="账号主目录")
    parser.add_argument("--account-id", required=True, help="账号标识")
    parser.add_argument("--username", required=True, help="用户名")
    parser.add_argument("--password-stdin", action="store_true", help="从标准输入读取单行密码")

    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1

    try:
        if args.password_stdin:
            line = sys.stdin.readline()
            if not line:
                sys.stderr.write("未提供密码\n")
                return 1
            password = line.rstrip("\r\n")
        else:
            p1 = getpass.getpass("输入密码: ")
            p2 = getpass.getpass("再次输入密码确认: ")
            if p1 != p2:
                sys.stderr.write("两次输入的密码不一致\n")
                return 1
            password = p1

        auth = WebAuth(args.home, args.account_id)
        auth.provision(args.username, password)
        sys.stdout.write("账号配置成功\n")
        return 0
    except ValueError as e:
        sys.stderr.write(f"参数错误：{e}\n")
        return 1
    except PermissionError as e:
        sys.stderr.write(f"安全或权限错误：{e}\n")
        return 1
    except Exception:
        sys.stderr.write("操作失败：发生错误\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
