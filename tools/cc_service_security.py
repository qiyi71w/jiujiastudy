"""Security controls for Canvas account service and transport.

Provides:
- ServiceSecrets: Restricted configuration and secrets loader enforcing origin binding,
  permission restrictions, and secure secret exposure.
- SecureCanvas: Restricted Canvas API client ensuring requests only target the bound
  HTTPS origin and /api/v1/ endpoints, strictly refusing redirects and cross-origin leakage.
"""
import json
import os
import posixpath
import re
import stat
import time
import urllib.error
import urllib.parse
import urllib.request

import brand
import canvas_api

__all__ = ["ServiceSecrets", "SecureCanvas"]

TELEGRAM_BOT_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]+$")


class SecretPermissionError(PermissionError, ValueError):
    """Raised when secrets file permissions allow group or other access."""
    pass


class _StrictNoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Strict redirect policy rejecting all HTTP redirects.

    Canvas API endpoints are direct REST endpoints that do not require redirects.
    Rejecting all redirects guarantees that credentials (Authorization headers)
    are never transmitted across origins, downgraded to unencrypted HTTP, or
    forwarded to unexpected endpoints before the caller can verify the destination.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Refuse to produce any redirected Request object.
        return None

    def http_error_301(self, req, fp, code, msg, headers):
        raise ValueError("HTTP redirect rejected by security policy")

    http_error_302 = http_error_301
    http_error_303 = http_error_301
    http_error_307 = http_error_301
    http_error_308 = http_error_301


class ServiceSecrets:
    """Loads and validates service configuration and restricted secrets."""

    def __init__(self, config, secrets_path):
        if not isinstance(config, dict):
            raise ValueError("configuration must be an object")

        canvas_host = config.get("canvas_host")
        if not isinstance(canvas_host, str) or not canvas_host.strip():
            raise ValueError("missing or invalid canvas_host in config")

        # Canonical origin for config canvas_host:
        # must be HTTPS, no userinfo/query/fragment/path except slash
        config_origin = self._canonical_origin(canvas_host)

        service = config.get("service")
        if not isinstance(service, dict):
            raise ValueError("service configuration must be an object")
        identities = {}
        for channel in ("telegram", "discord"):
            user_id = service.get(channel + "_user_id")
            if user_id is not None:
                if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
                    raise ValueError("invalid service." + channel + "_user_id")
                identities[channel] = user_id
        if not identities:
            raise ValueError("at least one private channel identity is required")

        # Open secrets file with restricted permissions (O_NOFOLLOW, regular file, no group/other access)
        if not isinstance(secrets_path, (str, bytes, os.PathLike)):
            raise ValueError("invalid secrets_path: must be a file path")

        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC

        fd = None
        try:
            try:
                fd = os.open(secrets_path, flags)
            except OSError:
                raise ValueError("cannot open secrets file") from None

            try:
                st = os.fstat(fd)
            except OSError:
                raise ValueError("cannot stat secrets file") from None

            if not stat.S_ISREG(st.st_mode):
                raise ValueError("secrets file must be a regular file")

            if os.name != "nt" and (st.st_mode & 0o077) != 0:
                raise SecretPermissionError(
                    "secrets file permissions too permissive: group or other access not allowed"
                )

            f = os.fdopen(fd, "r", encoding="utf-8")
            fd = None
            with f:
                content = f.read()
        except (ValueError, PermissionError):
            raise
        except Exception:
            raise ValueError("failed reading secrets file") from None
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass

        # Parse JSON without leaking secrets in diagnostics
        try:
            secret_data = json.loads(content)
        except Exception:
            raise ValueError("malformed secrets JSON") from None

        if not isinstance(secret_data, dict):
            raise ValueError("secrets JSON must be an object")

        secret_canvas_origin = secret_data.get("canvas_origin")
        if not isinstance(secret_canvas_origin, str) or not secret_canvas_origin.strip():
            raise ValueError("missing or invalid canvas_origin in secrets")

        # Bind credential to explicit origin: config host changes rejected until updated
        if secret_canvas_origin != config_origin:
            raise ValueError("canvas_origin in secrets does not match config canvas_host")

        canvas_token = secret_data.get("canvas_token")
        if not isinstance(canvas_token, str) or not canvas_token.strip():
            raise ValueError("invalid canvas_token in secrets: must be a non-empty string")

        telegram_bot_token = secret_data.get("telegram_bot_token")
        discord_bot_token = secret_data.get("discord_bot_token")
        if "telegram" in identities and not self._is_bot_token(telegram_bot_token):
            raise ValueError("invalid telegram_bot_token in secrets")
        if "discord" in identities and (not isinstance(discord_bot_token, str) or not discord_bot_token.strip()):
            raise ValueError("invalid discord_bot_token in secrets")
        self.canvas_origin = config_origin
        self.canvas_token = canvas_token.strip()
        self.telegram_bot_token = telegram_bot_token.strip() if "telegram" in identities else None
        self.discord_bot_token = discord_bot_token.strip() if "discord" in identities else None
        self.identities = identities
        self.user_id = identities.get("telegram")
        self.discord_user_id = identities.get("discord")

    @classmethod
    def _is_bot_token(cls, token):
        return bool(isinstance(token, str) and TELEGRAM_BOT_TOKEN_RE.match(token.strip()))

    @classmethod
    def _canonical_origin(cls, url_or_host):
        if not isinstance(url_or_host, str) or not url_or_host.strip():
            raise ValueError("invalid URL or host")
        parsed = urllib.parse.urlsplit(url_or_host.strip())
        if parsed.scheme.lower() != "https":
            raise ValueError("origin scheme must be https")
        if not parsed.netloc:
            raise ValueError("missing origin netloc")
        if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
            raise ValueError("userinfo not allowed in origin")
        if parsed.query:
            raise ValueError("query not allowed in origin")
        if parsed.fragment:
            raise ValueError("fragment not allowed in origin")
        if parsed.path not in ("", "/"):
            raise ValueError("path not allowed in origin")
        if not parsed.hostname:
            raise ValueError("missing hostname in origin")
        hostname = parsed.hostname.lower()
        host_part = f"[{hostname}]" if ":" in hostname else hostname
        port = parsed.port
        if port is not None and port != 443:
            return f"https://{host_part}:{port}"
        return f"https://{host_part}"


class SecureCanvas(canvas_api.Canvas):
    """Restricted Canvas API client subclass enforcing same-origin HTTPS /api/v1/ requests."""

    def __init__(self, host, tok, timeout=120, max_pages=50, retries=2, permit=None):
        self.origin = ServiceSecrets._canonical_origin(host)
        if not isinstance(tok, str) or not tok.strip():
            raise ValueError("Canvas token is required")
        super().__init__(self.origin, tok.strip(), timeout, max_pages, retries)
        self.opener = urllib.request.build_opener(_StrictNoRedirectHandler())
        self.permit = permit
    def _decode_payload(self, url, body):
        payload = super()._decode_payload(url, body)
        path = urllib.parse.urlsplit(url).path
        if path == "/api/v1/courses" or re.fullmatch(r"/api/v1/courses/\d+/assignments", path):
            if not isinstance(payload, list):
                raise ValueError("Canvas collection response must be a list")
        return payload


    def _validate_url(self, url):
        if not isinstance(url, str) or not url.strip():
            raise ValueError("invalid request URL")
        url = url.strip()
        if "\\" in url:
            raise ValueError("invalid request URL: backslash not allowed")
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme.lower() != "https":
            raise ValueError("invalid request scheme: must be https")
        if not parsed.netloc:
            raise ValueError("missing netloc in request URL")
        if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
            raise ValueError("userinfo is not allowed in request URL")
        if not parsed.hostname:
            raise ValueError("missing hostname in request URL")

        hostname = parsed.hostname.lower()
        host_part = f"[{hostname}]" if ":" in hostname else hostname
        port = parsed.port
        url_origin = f"https://{host_part}:{port}" if (port is not None and port != 443) else f"https://{host_part}"
        if url_origin != self.origin:
            raise ValueError("cross-origin request rejected")

        path = parsed.path
        if not (path == "/api/v1" or path.startswith("/api/v1/")):
            raise ValueError("request path must be under /api/v1/")
        norm_path = posixpath.normpath(path)
        if not (norm_path == "/api/v1" or norm_path.startswith("/api/v1/")):
            raise ValueError("path traversal rejected")

        unquoted = urllib.parse.unquote(path)
        if "\x00" in unquoted or "\\" in unquoted:
            raise ValueError("invalid characters in request path")
        norm_unquoted = posixpath.normpath(unquoted)
        if not (norm_unquoted == "/api/v1" or norm_unquoted.startswith("/api/v1/")):
            raise ValueError("encoded path traversal rejected")

        unquoted2 = urllib.parse.unquote(unquoted)
        if "\x00" in unquoted2 or "\\" in unquoted2:
            raise ValueError("invalid characters in request path")
        norm_unquoted2 = posixpath.normpath(unquoted2)
        if not (norm_unquoted2 == "/api/v1" or norm_unquoted2.startswith("/api/v1/")):
            raise ValueError("encoded path traversal rejected")

    def url_of(self, path):
        if not isinstance(path, str) or not path:
            raise ValueError("invalid API path")
        if not path.startswith("http") and "api/v1/" in path:
            path = "/" + path[path.index("api/v1/"):]
        if path.startswith("http://") or path.startswith("https://"):
            url = path
        else:
            if not path.startswith("/"):
                path = "/" + path
            url = self.host + path
        self._validate_url(url)
        return url

    def fetch(self, url, accept="application/json"):
        self._validate_url(url)
        headers = {
            "Accept": accept,
            "User-Agent": f"{brand.SLUG}/2 (read-only)",
            "Authorization": f"Bearer {self.tok}",
        }
        delay = 2
        for attempt in range(self.retries + 1):
            try:
                if self.permit is not None:
                    self.permit()
                req = urllib.request.Request(url, headers=headers)
                with self.opener.open(req, timeout=self.timeout) as r:
                    return r.headers, r.read()
            except ValueError:
                raise
            except urllib.error.HTTPError as e:
                e.close()
                if e.code not in canvas_api.RETRY_CODES or attempt == self.retries:
                    raise ValueError(f"Canvas API request failed with HTTP {e.code}") from None
                ra = e.headers.get("Retry-After") if e.headers else None
                wait = min(float(ra), 30) if ra and str(ra).replace(".", "", 1).isdigit() else delay
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt == self.retries:
                    raise ValueError("Canvas API network failure") from None
                wait = delay
            time.sleep(wait)
            delay *= 3
        raise ValueError("Canvas API request failed: retries exhausted") from None
