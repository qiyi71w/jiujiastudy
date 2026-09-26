#!/usr/bin/env python3
"""Root-only host administration. Secrets arrive on stdin, never in argv/env."""
import argparse
import json
import http.cookiejar
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from zoneinfo import ZoneInfo

from cc_service_security import SecureCanvas, ServiceSecrets
from cc_store import FileLock
from cc_web_auth import USERNAME_RE


class AdminError(Exception):
    pass


def run(*args, input=None, timeout=120):
    try:
        result = subprocess.run(args, input=input, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise AdminError(f"{args[0]} 无法执行或超时；未输出可能含凭据的诊断。") from None
    if result.returncode:
        raise AdminError(f"{args[0]} 操作失败；请检查服务和配置，原始输出未公开。")
    return result.stdout


def private_json(path):
    path = Path(path)
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode) or path.stat().st_mode & 0o077:
        raise AdminError("账号文件必须是非符号链接的受限普通文件。")
    with path.open() as stream:
        return json.load(stream)


def atomic(path, content, uid=10001, gid=10001, mode=0o600):
    path = Path(path)
    if path.is_symlink():
        raise AdminError("拒绝替换符号链接。")
    fd, tmp = tempfile.mkstemp(prefix=".admin-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            os.fchown(stream.fileno(), uid, gid)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def save(path, data):
    atomic(path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode())


def identity(origin, token):
    try:
        who = SecureCanvas(origin, token, timeout=30, retries=0).get("/api/v1/users/self")
        if not isinstance(who, dict) or not who.get("id"):
            raise ValueError()
        return str(who["id"])
    except Exception:
        raise AdminError("Canvas 身份校验失败；检查站点、Token、网络和证书。未修改账号。") from None


def inspect(name):
    return json.loads(run("docker", "inspect", name))[0]


def account(spec):
    mounts = {m["Destination"]: m for m in spec["Mounts"] if m["Type"] == "bind"}
    if "/data" not in mounts or "/run/secrets/service.json" not in mounts:
        raise AdminError("不是可管理的独立账号容器。")
    home = Path(mounts["/data"]["Source"])
    secret = Path(mounts["/run/secrets/service.json"]["Source"])
    cfg = private_json(home / "config.json")
    if not cfg.get("service", {}).get("account_id") or not cfg["service"].get("web_origin"):
        raise AdminError("账号缺少稳定标识或网站地址。")
    if spec["Config"]["User"] != "10001:10001":
        raise AdminError("该容器未使用标准 UID/GID 10001，拒绝自动修改。")
    return home, secret, cfg


REGISTRY = Path("/srv/jiujiastudy/gateway/registry.json")
HOME_ROOT = Path("/srv/jiujiastudy")
PRIVATE_ROOT = Path("/srv/jiujiastudy-private")


def registry():
    path = REGISTRY
    if path.is_symlink() or not path.is_file():
        raise AdminError("共享入口注册表尚未初始化；请先迁移已有账号。")
    meta = path.stat()
    if meta.st_uid != 0 or stat.S_IMODE(meta.st_mode) != 0o644:
        raise AdminError("共享入口注册表须由 root 拥有且权限为 0644。")
    try:
        data = json.loads(path.read_text())
        if set(data) != {"origin", "accounts"} or data["origin"] != "https://study.qiyi71w.com":
            raise ValueError()
        entries = data["accounts"]
        if not isinstance(entries, list):
            raise ValueError()
        names, ids, ports = set(), set(), set()
        for entry in entries:
            if set(entry) != {"username", "account_id", "port"}:
                raise ValueError()
            username, account_id, port = entry["username"], entry["account_id"], entry["port"]
            if (not isinstance(username, str) or not USERNAME_RE.fullmatch(username)
                    or not isinstance(account_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", account_id)
                    or type(port) is not int or not 1 <= port <= 65535
                    or username in names or account_id in ids or port in ports):
                raise ValueError()
            names.add(username); ids.add(account_id); ports.add(port)
        return data
    except (OSError, ValueError, TypeError, KeyError):
        raise AdminError("共享入口注册表格式无效或存在重复账号。") from None


def save_registry(data):
    atomic(REGISTRY, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode(), 0, 0, 0o644)


def accounts():
    data = registry()
    entries = {entry["account_id"]: entry for entry in data["accounts"]}
    names = run("docker", "ps", "-a", "--format", "{{.Names}}").splitlines()
    result = []
    for name in names:
        if not name.startswith("jiujiastudy-account-") or "-before-" in name:
            continue
        spec = inspect(name)
        try:
            home, secret, cfg = account(spec)
            account_id = cfg["service"]["account_id"]
            entry = entries.get(account_id)
            auth = private_json(home / "web-auth.json")
            if (entry is None or cfg["service"]["web_origin"] != data["origin"]
                    or auth["username"] != entry["username"] or port_of(spec) != entry["port"]):
                continue
        except (AdminError, OSError, ValueError, KeyError):
            continue
        result.append((name, spec, home, secret, cfg))
    return result


def status():
    origin = registry()["origin"]
    rows = accounts()
    for name, spec, home, _, cfg in rows:
        auth = private_json(home / "web-auth.json")
        print(f"{name} | {'运行中' if spec['State']['Running'] else '已停止'} | "
              f"{origin} | 用户 {auth['username']} | "
              f"每日 AI 上限 {cfg['service'].get('ai_daily_limit', 0)}")
    if not rows:
        print("尚未发现已注册的标准账号容器；新增时需要通过 --image 指定已构建的应用镜像。")


def selected(name):
    rows = accounts()
    row = next((r for r in rows if r[0] == name), None)
    if not row:
        raise AdminError("未找到已注册的可管理账号。请复制状态列表中的完整容器名称。")
    if sum(r[4]["service"]["account_id"] == row[4]["service"]["account_id"] for r in rows) != 1:
        raise AdminError("发现多个容器绑定同一账号，先停止并移除重复部署。")
    return row


def ready(port, origin):
    request = urllib.request.Request(f"http://127.0.0.1:{port}/health",
                                     headers={"Host": origin.removeprefix("https://")})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(30):
        try:
            with opener.open(request, timeout=2) as response:
                if response.status == 200 and json.load(response).get("status") == "ready":
                    return
        except Exception:
            pass
        time.sleep(1)
    raise AdminError("账号健康检查失败。")


def port_of(spec):
    bindings = spec["HostConfig"].get("PortBindings", {}).get("8080/tcp") or []
    if len(bindings) != 1 or bindings[0]["HostIp"] != "127.0.0.1":
        raise AdminError("需要标准的宿主回环端口映射。")
    return int(bindings[0]["HostPort"])


def rotate(name, token):
    _, spec, home, secret, cfg = selected(name)
    if not spec["State"]["Running"]:
        raise AdminError("账号容器已停止，请先排查或启动后再轮换 Token。")
    port = port_of(spec)
    old = private_json(secret)
    ServiceSecrets(cfg, secret)
    new_id = identity(cfg["canvas_host"], token)
    report = private_json(home / "service-report.json")
    old_id = report.get("canvas_user_id")
    if old_id is None:
        old_id = identity(cfg["canvas_host"], old["canvas_token"])
    if str(old_id) != new_id:
        raise AdminError("新 Token 属于另一个 Canvas 身份；请新增账号，不能覆盖原账号。")
    original = secret.read_bytes()
    owner = secret.stat()
    run("docker", "stop", "--time", "30", name)
    try:
        # Recheck after stopping the only writer. Never move a different user's data.
        current = private_json(home / "service-report.json").get("canvas_user_id")
        if current is not None and str(current) != new_id:
            raise AdminError("账号身份在校验期间发生变化。")
        updated = dict(old, canvas_token=token)
        atomic(secret, (json.dumps(updated) + "\n").encode(), owner.st_uid, owner.st_gid)
        # stop/start remounts an atomic-replaced file bind; no stale secret inode.
        run("docker", "start", name)
        ready(port, cfg["service"]["web_origin"])
    except BaseException:
        run("docker", "stop", "--time", "30", name)
        atomic(secret, original, owner.st_uid, owner.st_gid)
        run("docker", "start", name)
        ready(port, cfg["service"]["web_origin"])
        raise
    print("Token 已更新；账号记录、密码和额度未重置。")


def reset_password(name, password):
    _, spec, home, _, cfg = selected(name)
    if not spec["State"]["Running"]:
        raise AdminError("账号容器已停止；请先启动后再重设密码。")
    username = private_json(home / "web-auth.json")["username"]
    run("docker", "exec", "-i", name, "python", "-B", "/app/tools/cc_web_auth.py",
        "--home", "/data", "--account-id", cfg["service"]["account_id"],
        "--username", username, "--password-stdin", input=password + "\n")
    print("网页密码已重设，旧会话已撤销；学习记录未改变。")




def available_port(reserved=()):
    for port in range(18081, 19081):
        if port in reserved:
            continue
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                pass
    raise AdminError("没有可用的账号回环端口。")


def create(values, image=None):
    slug, origin, token, username, password, zone, ai_source = values
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,31}", slug):
        raise AdminError("账号名称须为 2–32 位小写字母、数字、下划线，以字母开头。")
    origin = ServiceSecrets._canonical_origin(origin)
    if not USERNAME_RE.fullmatch(username) or not 12 <= len(password) <= 256:
        raise AdminError("用户名须为 3–64 位字母、数字、点、下划线或连字符；密码须为 12–256 字符。")
    ZoneInfo(zone)
    data = registry()
    if any(entry["username"] == username for entry in data["accounts"]):
        raise AdminError("网页用户名已经注册，未创建账号。")
    if not shutil.which("docker"):
        raise AdminError("服务器缺少 docker。")
    rows = accounts()
    if not image:
        images = {r[1]["Config"]["Image"] for r in rows if r[1]["State"]["Running"]}
        if len(images) != 1:
            raise AdminError("无法唯一确定应用镜像，请用 --image 指定已构建镜像。")
        image = images.pop()
    run("docker", "image", "inspect", image)
    name = "jiujiastudy-account-" + slug
    network = name + "-web"
    if name in run("docker", "ps", "-a", "--format", "{{.Names}}").splitlines():
        raise AdminError("容器名称已存在，未覆盖。")
    if network in run("docker", "network", "ls", "--format", "{{.Name}}").splitlines():
        raise AdminError("账号网络已存在，未覆盖。")
    home = HOME_ROOT / slug
    private = PRIVATE_ROOT / slug
    for path in (home, private):
        if path.exists() or path.is_symlink():
            raise AdminError("目标目录或网站配置已存在，未覆盖。")
        if not path.parent.is_dir() or path.parent.is_symlink():
            raise AdminError("部署根目录缺失或为符号链接；请按 README 完成首次准备。")
    canvas_id = identity(origin, token)
    for _, _, existing_home, _, existing_cfg in rows:
        if ServiceSecrets._canonical_origin(existing_cfg["canvas_host"]) == origin:
            previous = private_json(existing_home / "service-report.json").get("canvas_user_id")
            if previous is not None and str(previous) == canvas_id:
                raise AdminError("该 Canvas 身份已经有独立网站账号，未重复创建。")
    secret_data = {"canvas_origin": origin, "canvas_token": token}
    extra_hosts = []
    if ai_source:
        source = selected(ai_source)
        shared = ServiceSecrets(source[4], source[3])
        if not shared.ai_origin:
            raise AdminError("选中的来源账号没有完整 AI 服务配置。")
        secret_data.update(ai_origin=shared.ai_origin, ai_api_key=shared.ai_api_key, ai_model=shared.ai_model)
        extra_hosts = source[1]["HostConfig"].get("ExtraHosts") or []
        if not isinstance(extra_hosts, list) or any(not isinstance(host, str) for host in extra_hosts):
            raise AdminError("来源账号的供应商主机映射无效。")
    port = available_port({entry["port"] for entry in data["accounts"]})
    account_id = uuid.uuid4().hex
    if any(entry["account_id"] == account_id for entry in data["accounts"]):
        raise AdminError("账号标识冲突，未创建账号。")
    cfg = {"schema_version": 3, "canvas_host": origin, "course_tz": zone, "user_tz": zone,
           "courses": [], "service": {"account_id": account_id, "web_origin": data["origin"],
           "ai_daily_limit": 20, "daily_timezone": zone, "daily_time": "09:00"}}
    # Keep scheduling off until the complete public login endpoint passes acceptance.
    report = {"binding": {"origin": origin, "account_id": account_id},
              "canvas_user_id": canvas_id, "service_enabled": False}
    made_home = made_private = made_network = made_container = made_entry = False
    try:
        home.mkdir(mode=0o700); made_home = True
        private.mkdir(mode=0o700); made_private = True
        os.chown(home, 10001, 10001); os.chown(private, 10001, 10001)
        save(home / "config.json", cfg)
        save(home / "service-report.json", report)
        save(private / "service.json", secret_data)
        ServiceSecrets(cfg, private / "service.json")
        run("docker", "run", "--rm", "-i", "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--user", "10001:10001",
            "--mount", f"type=bind,src={home},dst=/data", "--entrypoint", "python", image,
            "-B", "/app/tools/cc_web_auth.py", "--home", "/data", "--account-id", account_id,
            "--username", username, "--password-stdin", input=password + "\n")
        run("docker", "network", "create", network); made_network = True
        create_args = ["docker", "create", "--name", name, "--label", "jiujiastudy.managed=wizard",
            "--user", "10001:10001", "--init", "--restart", "unless-stopped", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "128",
            "--memory", "256m", "--cpus", "1", "--network", network,
            "--publish", f"127.0.0.1:{port}:8080"]
        for host in extra_hosts:
            create_args.extend(("--add-host", host))
        create_args.extend(("--mount", f"type=bind,src={home},dst=/data",
            "--mount", f"type=bind,src={private / 'service.json'},dst=/run/secrets/service.json,readonly", image))
        run(*create_args)
        made_container = True
        run("docker", "start", name)
        ready(port, cfg["service"]["web_origin"])
        entry = {"username": username, "account_id": account_id, "port": port}
        data["accounts"].append(entry)
        made_entry = True
        save_registry(data)
        public_origin = data["origin"]
        browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        login_request = urllib.request.Request(public_origin + "/api/login",
            data=json.dumps({"username": username, "password": password}).encode(),
            headers={"Origin": public_origin, "Content-Type": "application/json"})
        with browser.open(login_request, timeout=20) as response:
            if response.status != 200:
                raise AdminError("公网登录检查失败。")
        with browser.open(public_origin + "/api/state", timeout=20) as response:
            if json.load(response).get("account", {}).get("id") != account_id:
                raise AdminError("公网网站指向了另一个账号，拒绝启用扫描。")
        # Public login must resolve this exact account before enabling scheduled work.
        with FileLock(str(home / "service-state.lock")):
            report = private_json(home / "service-report.json")
            report["service_enabled"] = True
            save(home / "service-report.json", report)
        print(f"创建完成：{cfg['service']['web_origin']}\n网页用户：{username}\n每日 AI 上限：20；AI 授权默认关闭。")
        if not ai_source:
            print("未配置 AI 供应商；基础采集可用，模型调用需管理员另行配置。")
    except BaseException:
        # Restore only this account's route; never discard other registered accounts.
        if made_entry:
            current = registry()
            current["accounts"] = [item for item in current["accounts"] if item != entry]
            save_registry(current)
        if made_container:
            run("docker", "rm", "-f", name)
        if made_network:
            run("docker", "network", "rm", network)
        if made_private:
            shutil.rmtree(private)
        if made_home:
            shutil.rmtree(home)
        print("创建未完成，已撤销本次账号资源。", file=sys.stderr)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description="救驾服务器账号管理后端；请使用 account-wizard.sh")
    parser.add_argument("action", choices=("status", "create", "rotate", "password"))
    parser.add_argument("--image", help="新增账号使用的本地应用镜像；默认沿用唯一运行中的账号镜像")
    args = parser.parse_args(argv)
    os.umask(0o077)
    if os.geteuid() != 0:
        raise AdminError("请通过 sudo 在 Docker 所在服务器运行向导。")
    # Serialize host lifecycle operations, distinct from per-account application state locks.
    with FileLock("/run/lock/jiujiastudy-admin.lock"):
        if args.action == "status":
            status()
            return
        raw = sys.stdin.buffer.read(32769)
        if len(raw) > 32768:
            raise AdminError("输入过长。")
        fields = raw.decode().split("\0")
        if fields[-1] == "":
            fields.pop()
        expected = 7 if args.action == "create" else 2
        if len(fields) != expected or any("\n" in x or "\r" in x for x in fields):
            raise AdminError("输入格式不正确。")
        if args.action == "create":
            create(fields, args.image)
        elif args.action == "rotate":
            rotate(*fields)
        else:
            reset_password(*fields)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("已取消。", file=sys.stderr)
        sys.exit(130)
    except AdminError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except Exception:
        print("操作未完成；请检查输入、受限文件权限和部署依赖。未输出凭据或原始异常。", file=sys.stderr)
        sys.exit(1)
