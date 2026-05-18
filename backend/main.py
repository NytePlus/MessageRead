from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import mimetypes
import os
import posixpath
import secrets
import ssl
import time
from decimal import Decimal, InvalidOperation
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlencode, urlparse
from urllib.request import Request, urlopen

import redis
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


MESSAGE_TTL_SECONDS = 90 * 24 * 60 * 60
VISITOR_COOKIE = "read_receipt_visitor"
ADMIN_COOKIE = "paywall_admin"
PAY_SESSION_COOKIE = "paywall_session"
ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_-"
ROOT = Path(__file__).resolve().parent.parent
ALIPAY_CHARSET = "utf-8"
ALIPAY_SIGN_TYPE = "RSA2"
ADMIN_SESSION_SECONDS = 12 * 60 * 60

# 待授权服务商 OAuth 入口链接（仅 pending 时使用，授权成功后删除）。
PROVIDER_PENDING_AUTH_URL_TTL_SECONDS = 7 * 24 * 60 * 60

MARK_OPEN_SCRIPT = """
local key = KEYS[1]
local visitor = ARGV[1]
local now = ARGV[2]

if redis.call("EXISTS", key) == 0 then
  return {0, 0}
end

local owner = redis.call("HGET", key, "ownerVisitorID")
local first = 0

if not owner or owner == "" then
  redis.call("HSET", key, "ownerVisitorID", visitor)
  first = 1
elseif owner ~= visitor then
  local readAt = redis.call("HGET", key, "readAt")
  if not readAt or readAt == "" then
    redis.call("HSET", key, "readAt", now)
  end
end

return {1, first}
"""


def trim_str(value: str | None) -> str:
    return (value or "").strip()


def nano_id(length: int) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def listen_port() -> int:
    raw = trim_str(os.getenv("PORT")).removeprefix(":")
    return int(raw or "4000")


def configured_public_base_url() -> str | None:
    raw = trim_str(os.getenv("PUBLIC_BASE_URL")).rstrip("/")
    return raw or None


def first_header_value(value: str | None) -> str:
    return trim_str((value or "").split(",", 1)[0])


def now_ms() -> int:
    return int(time.time() * 1000)


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def read_env_secret(name: str) -> str:
    raw = trim_str(os.getenv(name))
    if not raw:
        return ""
    candidate = Path(raw)
    if candidate.is_file():
        return candidate.read_text(encoding="utf-8").strip()
    return raw.replace("\\n", "\n")


def cookie_value(headers: Any, name: str) -> str | None:
    raw_cookie = headers.get("Cookie") if headers is not None else None
    if not raw_cookie:
        return None
    cookie = SimpleCookie(raw_cookie)
    morsel = cookie.get(name)
    if morsel and trim_str(morsel.value):
        return trim_str(morsel.value)
    return None


def password_hash(password: str, salt: str | None = None) -> str:
    actual_salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), actual_salt.encode("utf-8"), 200_000)
    return f"{actual_salt}${base64.b64encode(digest).decode('ascii')}"


def verify_password(password: str, stored: str) -> bool:
    salt, _, expected = stored.partition("$")
    if not salt or not expected:
        return False
    return hmac.compare_digest(password_hash(password, salt), stored)


def ensure_admin_password_seeded() -> None:
    if rdb.exists("admin:password_hash"):
        return
    password = trim_str(os.getenv("ADMIN_PASSWORD"))
    if password:
        rdb.set("admin:password_hash", password_hash(password))


def tls_config() -> tuple[str, str] | None:
    cert_file = trim_str(os.getenv("TLS_CERT_FILE"))
    key_file = trim_str(os.getenv("TLS_KEY_FILE"))
    if not cert_file and not key_file:
        return None
    if not cert_file or not key_file:
        raise RuntimeError("启用 HTTPS 需要同时设置 TLS_CERT_FILE 和 TLS_KEY_FILE。")
    if not Path(cert_file).is_file():
        raise RuntimeError(f"TLS_CERT_FILE 不存在: {cert_file}")
    if not Path(key_file).is_file():
        raise RuntimeError(f"TLS_KEY_FILE 不存在: {key_file}")
    return cert_file, key_file


def redis_client() -> redis.Redis:
    addr = trim_str(os.getenv("REDIS_ADDR")) or "localhost:6379"
    host, _, port = addr.partition(":")
    db_raw = trim_str(os.getenv("REDIS_DB"))
    db = int(db_raw or "0")
    client = redis.Redis(
        host=host or "localhost",
        port=int(port or "6379"),
        password=os.getenv("REDIS_PASSWORD") or None,
        db=db,
        decode_responses=True,
    )
    client.ping()
    print(f"已连接 Redis: {addr}", flush=True)
    return client


rdb = redis_client()
mark_open = rdb.register_script(MARK_OPEN_SCRIPT)


def alipay_gateway() -> str:
    return trim_str(os.getenv("ALIPAY_GATEWAY")) or "https://openapi.alipay.com/gateway.do"


def alipay_app_id() -> str:
    return trim_str(os.getenv("ALIPAY_APP_ID"))


def load_private_key():
    private_key = read_env_secret("ALIPAY_PRIVATE_KEY")
    if not private_key:
        raise RuntimeError("缺少 ALIPAY_PRIVATE_KEY。")
    return serialization.load_pem_private_key(private_key.encode("utf-8"), password=None)


def load_public_key():
    public_key = read_env_secret("ALIPAY_PUBLIC_KEY")
    if not public_key:
        raise RuntimeError("缺少 ALIPAY_PUBLIC_KEY。")
    return serialization.load_pem_public_key(public_key.encode("utf-8"))


def alipay_sign_content(params: dict[str, Any]) -> str:
    pairs = []
    for key in sorted(params):
        if key in {"sign", "sign_type"}:
            continue
        value = params[key]
        if value is None or value == "":
            continue
        pairs.append(f"{key}={value}")
    return "&".join(pairs)


def alipay_sign(params: dict[str, Any]) -> str:
    signature = load_private_key().sign(
        alipay_sign_content(params).encode(ALIPAY_CHARSET),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("ascii")


def alipay_verify(params: dict[str, Any]) -> bool:
    sign = trim_str(params.get("sign"))
    if not sign:
        return False
    try:
        load_public_key().verify(
            base64.b64decode(sign),
            alipay_sign_content(params).encode(ALIPAY_CHARSET),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return True
    except (InvalidSignature, ValueError):
        return False


def alipay_common_params(method: str) -> dict[str, str]:
    app_id = alipay_app_id()
    if not app_id:
        raise RuntimeError("缺少 ALIPAY_APP_ID。")
    return {
        "app_id": app_id,
        "method": method,
        "format": "JSON",
        "charset": "UTF-8",
        "sign_type": ALIPAY_SIGN_TYPE,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "version": "1.0",
    }


def alipay_api_call(method: str, biz_content: dict[str, Any]) -> dict[str, Any]:
    params = alipay_common_params(method)
    params["biz_content"] = json_dumps(biz_content)
    params["sign"] = alipay_sign(params)
    data = urlencode(params).encode("utf-8")
    request = Request(
        alipay_gateway(),
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
        method="POST",
    )
    with urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    key = method.replace(".", "_") + "_response"
    result = payload.get(key) or {}
    if result.get("code") != "10000":
        msg = result.get("sub_msg") or result.get("msg") or "支付宝接口调用失败。"
        raise RuntimeError(msg)
    return result


def alipay_exchange_auth_token(app_auth_code: str) -> dict[str, Any]:
    return alipay_api_call(
        "alipay.open.auth.token.app",
        {"grant_type": "authorization_code", "code": app_auth_code},
    )


def alipay_auth_url(base_url: str, provider_uuid: str) -> str:
    app_id = alipay_app_id()
    if not app_id:
        raise RuntimeError("缺少 ALIPAY_APP_ID。")
    redirect_uri = f"{base_url}/api/alipay/auth/callback"
    return (
        "https://openauth.alipay.com/oauth2/appToAppAuth.htm?"
        + urlencode({"app_id": app_id, "redirect_uri": redirect_uri, "state": provider_uuid})
    )


def alipay_wap_pay_url(
    *,
    base_url: str,
    provider: dict[str, str],
    out_trade_no: str,
    amount: str,
    session_id: str,
) -> str:
    params = alipay_common_params("alipay.trade.wap.pay")
    params["return_url"] = f"{base_url}/api/alipay/pay/return"
    params["notify_url"] = f"{base_url}/api/alipay/pay/notify"
    params["app_auth_token"] = provider["appAuthToken"]
    params["biz_content"] = json_dumps(
        {
            "out_trade_no": out_trade_no,
            "total_amount": amount,
            "subject": "APK 下载服务",
            "product_code": "QUICK_WAP_WAY",
            "passback_params": quote_plus(session_id),
        }
    )
    params["sign"] = alipay_sign(params)
    return alipay_gateway() + "?" + urlencode(params)


def message_key(message_id: str) -> str:
    return f"message:{message_id}"


def resolve_web_dist() -> Path:
    raw = trim_str(os.getenv("WEB_DIST"))
    if raw:
        return Path(raw).resolve()
    return ROOT / "frontend" / "dist"


WEB_DIST = resolve_web_dist()


def save_message(message: dict[str, str]) -> None:
    key = message_key(message["id"])
    rdb.hset(key, mapping=message)
    rdb.expire(key, MESSAGE_TTL_SECONDS)


def load_message(message_id: str) -> dict[str, str] | None:
    values = rdb.hgetall(message_key(message_id))
    if not values:
        return None
    values.setdefault("id", message_id)
    return values


def provider_key(uuid: str) -> str:
    return f"provider:uuid:{uuid}"


def provider_pending_auth_url_key(uuid: str) -> str:
    return f"provider:pending_auth_url:{uuid}"


def store_provider_pending_auth_url(provider_uuid: str, auth_url: str) -> None:
    rdb.setex(provider_pending_auth_url_key(provider_uuid), PROVIDER_PENDING_AUTH_URL_TTL_SECONDS, auth_url)


def provider_pending_auth_url(provider_uuid: str) -> str | None:
    return rdb.get(provider_pending_auth_url_key(provider_uuid))


def delete_provider_pending_auth_url(provider_uuid: str) -> None:
    rdb.delete(provider_pending_auth_url_key(provider_uuid))


def provider_id_key(provider_id: str) -> str:
    return f"provider:id:{provider_id}"


def create_provider() -> dict[str, str]:
    provider_id = str(rdb.incr("provider:id:seq"))
    provider_uuid = nano_id(20)
    provider = {
        "id": provider_id,
        "uuid": provider_uuid,
        "createdAt": str(now_ms()),
        "authorizedAt": "",
        "status": "pending",
        "appAuthToken": "",
        "appRefreshToken": "",
        "expiresIn": "",
        "authAppId": "",
        "userId": "",
        "rawAuthResponse": "{}",
    }
    rdb.hset(provider_key(provider_uuid), mapping=provider)
    rdb.set(provider_id_key(provider_id), provider_uuid)
    rdb.sadd("providers", provider_uuid)
    return provider


def save_authorized_provider(provider_uuid: str, token: dict[str, Any]) -> dict[str, str]:
    provider = get_provider(provider_uuid)
    if provider is None:
        raise RuntimeError("服务商不存在或授权已过期。")
    provider.update(
        {
            "authorizedAt": str(now_ms()),
            "status": "authorized",
            "appAuthToken": trim_str(token.get("app_auth_token")),
            "appRefreshToken": trim_str(token.get("app_refresh_token")),
            "expiresIn": str(token.get("expires_in") or ""),
            "authAppId": trim_str(token.get("auth_app_id")),
            "userId": trim_str(token.get("user_id")),
            "rawAuthResponse": json.dumps(token, ensure_ascii=False),
        }
    )
    rdb.hset(provider_key(provider_uuid), mapping=provider)
    delete_provider_pending_auth_url(provider_uuid)
    return provider


def get_provider(provider_uuid: str) -> dict[str, str] | None:
    values = rdb.hgetall(provider_key(provider_uuid))
    return values or None


def list_providers() -> list[dict[str, str]]:
    providers = [get_provider(uuid) for uuid in rdb.smembers("providers")]
    return sorted([item for item in providers if item], key=lambda item: int(item.get("id") or "0"))


def public_provider(provider: dict[str, str]) -> dict[str, Any]:
    return {
        "id": int(provider.get("id") or "0"),
        "uuid": provider.get("uuid", ""),
        "createdAt": parse_int(provider.get("createdAt", ""), 0),
        "authorizedAt": parse_int(provider.get("authorizedAt", "")),
        "status": provider.get("status", ""),
        "authAppId": provider.get("authAppId", ""),
        "userId": provider.get("userId", ""),
        "hasToken": bool(provider.get("appAuthToken")),
    }


def validate_price(value: Any) -> str:
    try:
        price = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise ValueError("收费标准必须是数字。")
    if price <= 0:
        raise ValueError("收费标准必须大于 0。")
    return format(price, "f")


def save_pricing(price: str) -> dict[str, str]:
    pricing_id = str(rdb.incr("pricing:id:seq"))
    pricing = {"id": pricing_id, "price": price, "createdAt": str(now_ms())}
    rdb.hset(f"pricing:{pricing_id}", mapping=pricing)
    rdb.set("pricing:current", pricing_id)
    return pricing


def current_pricing() -> dict[str, str] | None:
    pricing_id = rdb.get("pricing:current")
    if not pricing_id:
        default_price = trim_str(os.getenv("DEFAULT_PRICE"))
        if default_price:
            return save_pricing(validate_price(default_price))
        return None
    values = rdb.hgetall(f"pricing:{pricing_id}")
    return values or None


def public_pricing(pricing: dict[str, str]) -> dict[str, Any]:
    return {
        "id": int(pricing.get("id") or "0"),
        "price": pricing.get("price", ""),
        "createdAt": parse_int(pricing.get("createdAt", ""), 0),
    }


def order_key(out_trade_no: str) -> str:
    return f"order:{out_trade_no}"


def save_order(order: dict[str, str]) -> None:
    rdb.hset(order_key(order["outTradeNo"]), mapping=order)


def get_order(out_trade_no: str) -> dict[str, str] | None:
    values = rdb.hgetall(order_key(out_trade_no))
    return values or None


def mark_order_paid(out_trade_no: str, trade_no: str = "") -> dict[str, str] | None:
    order = get_order(out_trade_no)
    if order is None:
        return None
    order.update({"status": "paid", "paidAt": str(now_ms()), "tradeNo": trade_no})
    save_order(order)
    if order.get("sessionId") and order.get("providerUuid"):
        mark_session_paid(order["sessionId"], order["providerUuid"])
    return order


def paid_session_key(session_id: str, provider_uuid: str) -> str:
    return f"paid_session:{session_id}:{provider_uuid}"


def mark_session_paid(session_id: str, provider_uuid: str) -> None:
    rdb.set(paid_session_key(session_id, provider_uuid), str(now_ms()))


def is_session_paid(session_id: str, provider_uuid: str) -> bool:
    return bool(session_id and provider_uuid and rdb.exists(paid_session_key(session_id, provider_uuid)))


def parse_int(value: str, fallback: int | None = None) -> int | None:
    if value == "":
        return fallback
    try:
        return int(value)
    except ValueError:
        return fallback


def open_page_html(message: dict[str, str]) -> bytes:
    name = html.escape(message.get("toName", ""))
    text = html.escape(message.get("body", "")).replace("\n", "<br/>")
    page = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>发送到{name} 的聊天消息</title>
  <style>
    body {{ margin: 0; min-height: 100vh; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; background: #ededed; color: #111; padding: 24px 16px; box-sizing: border-box; }}
    .chat {{ max-width: 720px; margin: 0 auto; }}
    .bubble {{ display: inline-block; max-width: min(78vw, 520px); background: #fff; color: #111; border-radius: 6px; padding: 11px 13px; line-height: 1.55; font-size: 16px; box-shadow: 0 1px 1px rgba(0,0,0,.04); position: relative; word-break: break-word; }}
    .bubble::before {{ content: ""; position: absolute; left: -7px; top: 12px; border-top: 6px solid transparent; border-bottom: 6px solid transparent; border-right: 8px solid #fff; }}
  </style>
</head>
<body>
  <main class="chat">
    <div class="bubble">{text}</div>
  </main>
</body>
</html>"""
    return page.encode("utf-8")


def page_404_html() -> bytes:
    return (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"/>'
        '<meta name="viewport" content="width=device-width, initial-scale=1"/>'
        '<title>未找到</title></head><body style="font-family:system-ui;padding:24px;">'
        "<p>链接无效或消息已过期。</p></body></html>"
    ).encode("utf-8")


class Handler(SimpleHTTPRequestHandler):
    server_version = "ReadReceiptPython/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        print("%s - - [%s] %s" % (self.address_string(), self.log_date_time_string(), format % args), flush=True)

    def end_headers(self) -> None:
        headers = getattr(self, "headers", None)
        origin = headers.get("Origin") if headers is not None else None
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
        else:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def do_POST(self) -> None:
        if self.path_only() == "/api/messages":
            self.handle_create()
            return
        if self.path_only() == "/api/admin/login":
            self.handle_admin_login()
            return
        if self.path_only() == "/api/admin/providers/auth-url":
            self.handle_admin_provider_auth_url()
            return
        if self.path_only() == "/api/admin/pricing":
            self.handle_admin_pricing_update()
            return
        if self.path_only() == "/api/pay/orders":
            self.handle_pay_order()
            return
        if self.path_only() == "/api/alipay/pay/notify":
            self.handle_alipay_notify()
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_GET(self) -> None:
        if self.route_api_or_open(head_only=False):
            return
        self.serve_spa_or_static(head_only=False)

    def do_HEAD(self) -> None:
        if self.route_api_or_open(head_only=True):
            return
        self.serve_spa_or_static(head_only=True)

    def path_only(self) -> str:
        return urlparse(self.path).path

    def query_params(self) -> dict[str, str]:
        parsed = parse_qs(urlparse(self.path).query, keep_blank_values=True)
        return {key: values[-1] if values else "" for key, values in parsed.items()}

    def request_base_url(self) -> str:
        configured = configured_public_base_url()
        if configured:
            return configured

        forwarded_proto = first_header_value(self.headers.get("X-Forwarded-Proto")).lower()
        if forwarded_proto in {"http", "https"}:
            scheme = forwarded_proto
        else:
            scheme = "https" if isinstance(self.connection, ssl.SSLSocket) else "http"

        host = first_header_value(self.headers.get("X-Forwarded-Host")) or first_header_value(self.headers.get("Host"))
        if not host:
            host = f"localhost:{listen_port()}"

        forwarded_port = first_header_value(self.headers.get("X-Forwarded-Port"))
        if forwarded_port and ":" not in host and forwarded_port not in {"80", "443"}:
            host = f"{host}:{forwarded_port}"

        return f"{scheme}://{host}".rstrip("/")

    def frontend_url(self, params: dict[str, str] | None = None) -> str:
        base = self.request_base_url()
        if not params:
            return base + "/"
        return base + "/?" + urlencode(params)

    def route_api_or_open(self, head_only: bool) -> bool:
        path = self.path_only()
        if path == "/api/admin/me":
            self.handle_admin_me(head_only)
            return True
        if path == "/api/admin/providers/auth-url":
            self.handle_admin_provider_auth_url_get(head_only)
            return True
        if path == "/api/admin/providers":
            self.handle_admin_providers(head_only)
            return True
        if path == "/api/admin/pricing":
            self.handle_admin_pricing(head_only)
            return True
        if path == "/api/pay/config":
            self.handle_pay_config(head_only)
            return True
        if path == "/api/pay/orders":
            self.handle_order_status(head_only)
            return True
        if path == "/api/download/apk":
            self.handle_download_apk(head_only)
            return True
        if path == "/api/alipay/auth/callback":
            self.handle_alipay_auth_callback(head_only)
            return True
        if path == "/api/alipay/pay/return":
            self.handle_alipay_return(head_only)
            return True
        if path.startswith("/api/messages/") and path.endswith("/status"):
            message_id = path.removeprefix("/api/messages/").removesuffix("/status").strip("/")
            self.handle_status(message_id, head_only)
            return True
        if path.startswith("/open/"):
            message_id = path.removeprefix("/open/").strip("/")
            self.handle_open(message_id, head_only)
            return True
        if path.startswith("/api/"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return True
        return False

    def read_json(self) -> dict[str, Any] | None:
        length = int(self.headers.get("Content-Length") or "0")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    def read_form(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length).decode("utf-8")
        parsed = parse_qs(raw, keep_blank_values=True)
        return {key: values[-1] if values else "" for key, values in parsed.items()}

    def write_json(
        self,
        status: HTTPStatus,
        value: dict[str, Any],
        head_only: bool = False,
        cookies: list[tuple[str, str, int | None]] | None = None,
    ) -> None:
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for cookie in cookies or []:
            self.send_cookie_header(*cookie)
        self.end_headers()
        if not head_only:
            self.wfile.write(data)

    def write_text(self, status: HTTPStatus, value: str, head_only: bool = False, content_type: str = "text/plain; charset=utf-8") -> None:
        data = value.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if not head_only:
            self.wfile.write(data)

    def redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.end_headers()

    def send_cookie_header(self, name: str, value: str, max_age: int | None = None) -> None:
        secure = "; Secure" if self.request_base_url().startswith("https://") else ""
        max_age_part = f"; Max-Age={max_age}" if max_age is not None else ""
        self.send_header("Set-Cookie", f"{name}={value}; Path=/; HttpOnly; SameSite=Lax{secure}{max_age_part}")

    def ensure_pay_session(self) -> str:
        existing = cookie_value(self.headers, PAY_SESSION_COOKIE)
        if existing:
            return existing
        return nano_id(32)

    def admin_session(self) -> str | None:
        token = cookie_value(self.headers, ADMIN_COOKIE)
        if token and rdb.exists(f"admin_session:{token}"):
            return token
        return None

    def require_admin(self) -> bool:
        if self.admin_session():
            return True
        self.write_json(HTTPStatus.UNAUTHORIZED, {"error": "需要管理员登录。"})
        return False

    def handle_admin_login(self) -> None:
        ensure_admin_password_seeded()
        payload = self.read_json()
        if payload is None:
            self.write_json(HTTPStatus.BAD_REQUEST, {"error": "请求体无效。"})
            return
        stored = rdb.get("admin:password_hash")
        password = trim_str(payload.get("password"))
        if not stored:
            self.write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "未配置 ADMIN_PASSWORD。"})
            return
        if not password or not verify_password(password, stored):
            self.write_json(HTTPStatus.UNAUTHORIZED, {"error": "管理员密码错误。"})
            return
        token = nano_id(32)
        rdb.setex(f"admin_session:{token}", ADMIN_SESSION_SECONDS, "1")
        self.write_json(
            HTTPStatus.OK,
            {"ok": True},
            cookies=[(ADMIN_COOKIE, token, ADMIN_SESSION_SECONDS)],
        )

    def handle_admin_me(self, head_only: bool) -> None:
        self.write_json(HTTPStatus.OK, {"authenticated": bool(self.admin_session())}, head_only)

    def handle_admin_providers(self, head_only: bool) -> None:
        if not self.require_admin():
            return
        self.write_json(
            HTTPStatus.OK,
            {"providers": [public_provider(provider) for provider in list_providers()]},
            head_only,
        )

    def handle_admin_provider_auth_url(self) -> None:
        if not self.require_admin():
            return
        try:
            provider = create_provider()
            auth_url = alipay_auth_url(self.request_base_url(), provider["uuid"])
            store_provider_pending_auth_url(provider["uuid"], auth_url)
        except RuntimeError as exc:
            self.write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self.write_json(
            HTTPStatus.OK,
            {"provider": public_provider(provider), "authUrl": auth_url},
        )

    def handle_admin_provider_auth_url_get(self, head_only: bool) -> None:
        if not self.require_admin():
            return
        provider_uuid = trim_str(self.query_params().get("uuid"))
        if not provider_uuid:
            self.write_json(HTTPStatus.BAD_REQUEST, {"error": "缺少 uuid。"}, head_only)
            return
        provider = get_provider(provider_uuid)
        if provider is None:
            self.write_json(HTTPStatus.NOT_FOUND, {"error": "服务商不存在。"}, head_only)
            return
        if provider.get("status") != "pending":
            self.write_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "该服务商已授权或不在待授权状态，无法获取授权链接。"},
                head_only,
            )
            return
        auth_url = provider_pending_auth_url(provider_uuid)
        if not auth_url:
            try:
                auth_url = alipay_auth_url(self.request_base_url(), provider_uuid)
            except RuntimeError as exc:
                self.write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)}, head_only)
                return
            store_provider_pending_auth_url(provider_uuid, auth_url)
        self.write_json(HTTPStatus.OK, {"authUrl": auth_url}, head_only)

    def handle_alipay_auth_callback(self, head_only: bool) -> None:
        params = self.query_params()
        provider_uuid = trim_str(params.get("state"))
        auth_code = trim_str(params.get("app_auth_code"))
        if not provider_uuid or not auth_code:
            self.write_text(HTTPStatus.BAD_REQUEST, "授权回调缺少 state 或 app_auth_code。", head_only)
            return
        try:
            token = alipay_exchange_auth_token(auth_code)
            save_authorized_provider(provider_uuid, token)
        except RuntimeError as exc:
            self.write_text(HTTPStatus.BAD_REQUEST, f"授权失败：{exc}", head_only, "text/html; charset=utf-8")
            return
        self.write_text(
            HTTPStatus.OK,
            "<!DOCTYPE html><html lang=\"zh-CN\"><body><p>授权成功，您可以关闭此页面。</p></body></html>",
            head_only,
            "text/html; charset=utf-8",
        )

    def handle_admin_pricing(self, head_only: bool) -> None:
        if not self.require_admin():
            return
        pricing = current_pricing()
        self.write_json(HTTPStatus.OK, {"pricing": public_pricing(pricing) if pricing else None}, head_only)

    def handle_admin_pricing_update(self) -> None:
        if not self.require_admin():
            return
        payload = self.read_json()
        if payload is None:
            self.write_json(HTTPStatus.BAD_REQUEST, {"error": "请求体无效。"})
            return
        try:
            pricing = save_pricing(validate_price(payload.get("price")))
        except ValueError as exc:
            self.write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self.write_json(HTTPStatus.OK, {"pricing": public_pricing(pricing)})

    def handle_pay_config(self, head_only: bool) -> None:
        params = self.query_params()
        provider_uuid = trim_str(params.get("providerUuid"))
        session_id = self.ensure_pay_session()
        provider = get_provider(provider_uuid) if provider_uuid else None
        pricing = current_pricing()
        cookies = [] if cookie_value(self.headers, PAY_SESSION_COOKIE) else [(PAY_SESSION_COOKIE, session_id, None)]
        self.write_json(
            HTTPStatus.OK,
            {
                "providerExists": provider is not None and provider.get("status") == "authorized",
                "providerUuid": provider_uuid,
                "pricing": public_pricing(pricing) if pricing else None,
                "paid": is_session_paid(session_id, provider_uuid),
            },
            head_only,
            cookies=cookies,
        )

    def handle_pay_order(self) -> None:
        payload = self.read_json()
        if payload is None:
            self.write_json(HTTPStatus.BAD_REQUEST, {"error": "请求体无效。"})
            return
        provider_uuid = trim_str(payload.get("providerUuid"))
        provider = get_provider(provider_uuid)
        if provider is None or provider.get("status") != "authorized" or not provider.get("appAuthToken"):
            self.write_json(HTTPStatus.NOT_FOUND, {"error": "服务商不存在或尚未授权。"})
            return
        pricing = current_pricing()
        if pricing is None:
            self.write_json(HTTPStatus.BAD_REQUEST, {"error": "尚未配置收费标准。"})
            return
        session_id = self.ensure_pay_session()
        if is_session_paid(session_id, provider_uuid):
            self.write_json(HTTPStatus.OK, {"paid": True}, cookies=[] if cookie_value(self.headers, PAY_SESSION_COOKIE) else [(PAY_SESSION_COOKIE, session_id, None)])
            return
        out_trade_no = "MR" + time.strftime("%Y%m%d%H%M%S") + nano_id(8)
        order = {
            "outTradeNo": out_trade_no,
            "providerUuid": provider_uuid,
            "price": pricing["price"],
            "status": "created",
            "createdAt": str(now_ms()),
            "paidAt": "",
            "tradeNo": "",
            "sessionId": session_id,
        }
        save_order(order)
        try:
            pay_url = alipay_wap_pay_url(
                base_url=self.request_base_url(),
                provider=provider,
                out_trade_no=out_trade_no,
                amount=pricing["price"],
                session_id=session_id,
            )
        except RuntimeError as exc:
            self.write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self.write_json(
            HTTPStatus.OK,
            {"outTradeNo": out_trade_no, "payUrl": pay_url, "paid": False},
            cookies=[] if cookie_value(self.headers, PAY_SESSION_COOKIE) else [(PAY_SESSION_COOKIE, session_id, None)],
        )

    def handle_order_status(self, head_only: bool) -> None:
        out_trade_no = trim_str(self.query_params().get("outTradeNo"))
        order = get_order(out_trade_no) if out_trade_no else None
        if order is None:
            self.write_json(HTTPStatus.NOT_FOUND, {"error": "订单不存在。"}, head_only)
            return
        session_id = cookie_value(self.headers, PAY_SESSION_COOKIE) or ""
        paid = order.get("status") == "paid" or is_session_paid(session_id, order.get("providerUuid", ""))
        self.write_json(HTTPStatus.OK, {"outTradeNo": out_trade_no, "status": order.get("status"), "paid": paid}, head_only)

    def handle_alipay_notify(self) -> None:
        params = self.read_form()
        if not alipay_verify(params):
            self.write_text(HTTPStatus.OK, "failure")
            return
        if params.get("app_id") != alipay_app_id():
            self.write_text(HTTPStatus.OK, "failure")
            return
        out_trade_no = trim_str(params.get("out_trade_no"))
        order = get_order(out_trade_no)
        if order is None:
            self.write_text(HTTPStatus.OK, "failure")
            return
        try:
            paid_amount = Decimal(str(params.get("total_amount")))
            order_amount = Decimal(order["price"])
        except (InvalidOperation, KeyError):
            self.write_text(HTTPStatus.OK, "failure")
            return
        if paid_amount != order_amount:
            self.write_text(HTTPStatus.OK, "failure")
            return
        if params.get("trade_status") in {"TRADE_SUCCESS", "TRADE_FINISHED"}:
            mark_order_paid(out_trade_no, trim_str(params.get("trade_no")))
            self.write_text(HTTPStatus.OK, "success")
            return
        self.write_text(HTTPStatus.OK, "success")

    def handle_alipay_return(self, head_only: bool) -> None:
        params = self.query_params()
        out_trade_no = trim_str(params.get("out_trade_no"))
        order = get_order(out_trade_no) if out_trade_no else None
        if order and alipay_verify(params) and params.get("app_id") == alipay_app_id():
            self.redirect(self.frontend_url({"provider": order.get("providerUuid", ""), "order": out_trade_no}))
            return
        if head_only:
            self.write_text(HTTPStatus.OK, "", True)
            return
        self.redirect(self.frontend_url({"order": out_trade_no}))

    def handle_download_apk(self, head_only: bool) -> None:
        provider_uuid = trim_str(self.query_params().get("providerUuid"))
        session_id = cookie_value(self.headers, PAY_SESSION_COOKIE) or ""
        if not is_session_paid(session_id, provider_uuid):
            self.write_json(HTTPStatus.PAYMENT_REQUIRED, {"error": "请先完成支付。"}, head_only)
            return
        apk_file = Path(trim_str(os.getenv("APK_FILE"))).resolve()
        if not apk_file.is_file():
            self.write_json(HTTPStatus.NOT_FOUND, {"error": "APK 文件不存在。"}, head_only)
            return
        file_name = apk_file.name
        content_type = mimetypes.guess_type(file_name)[0] or "application/vnd.android.package-archive"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(apk_file.stat().st_size))
        self.send_header("Content-Disposition", f'attachment; filename="{file_name}"')
        self.end_headers()
        if not head_only:
            with apk_file.open("rb") as handle:
                while chunk := handle.read(1024 * 256):
                    self.wfile.write(chunk)

    def handle_create(self) -> None:
        payload = self.read_json()
        if payload is None:
            self.write_json(HTTPStatus.BAD_REQUEST, {"error": "请求体无效。"})
            return
        to_name = trim_str(payload.get("toName"))
        body = trim_str(payload.get("body"))
        if not to_name or not body:
            self.write_json(HTTPStatus.BAD_REQUEST, {"error": "需要提供对方名字和要说的内容。"})
            return

        message_id = nano_id(12)
        created_at = str(int(time.time() * 1000))
        save_message(
            {
                "id": message_id,
                "toName": to_name,
                "body": body,
                "createdAt": created_at,
                "readAt": "",
                "ownerVisitorID": "",
            }
        )
        base = self.request_base_url()
        self.write_json(
            HTTPStatus.OK,
            {
                "id": message_id,
                "openUrl": f"{base}/open/{message_id}",
                "statusUrl": f"{base}/api/messages/{message_id}/status",
            },
        )

    def handle_status(self, message_id: str, head_only: bool) -> None:
        message = load_message(message_id)
        if message is None:
            self.write_json(HTTPStatus.NOT_FOUND, {"error": "消息不存在。"}, head_only)
            return
        read_at = parse_int(message.get("readAt", ""))
        self.write_json(
            HTTPStatus.OK,
            {
                "read": read_at is not None,
                "readAt": read_at,
                "createdAt": parse_int(message.get("createdAt", ""), 0),
            },
            head_only,
        )

    def visitor_id(self) -> tuple[str, str | None]:
        raw_cookie = self.headers.get("Cookie")
        if raw_cookie:
            cookie = SimpleCookie(raw_cookie)
            morsel = cookie.get(VISITOR_COOKIE)
            if morsel and trim_str(morsel.value):
                return trim_str(morsel.value), None

        visitor = nano_id(24)
        secure = "; Secure" if self.request_base_url().startswith("https://") else ""
        cookie_header = f"{VISITOR_COOKIE}={visitor}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax{secure}"
        return visitor, cookie_header

    def handle_open(self, message_id: str, head_only: bool) -> None:
        visitor, cookie_header = self.visitor_id()
        exists, _first = mark_open(keys=[message_key(message_id)], args=[visitor, str(int(time.time() * 1000))])
        if int(exists) != 1:
            body = page_404_html()
            self.send_response(HTTPStatus.NOT_FOUND)
            if cookie_header:
                self.send_header("Set-Cookie", cookie_header)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head_only:
                self.wfile.write(body)
            return

        message = load_message(message_id)
        if message is None:
            body = page_404_html()
            self.send_response(HTTPStatus.NOT_FOUND)
            if cookie_header:
                self.send_header("Set-Cookie", cookie_header)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head_only:
                self.wfile.write(body)
            return

        body = open_page_html(message)
        self.send_response(HTTPStatus.OK)
        if cookie_header:
            self.send_header("Set-Cookie", cookie_header)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def serve_spa_or_static(self, head_only: bool) -> None:
        if not WEB_DIST.is_dir():
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        path = self.path_only()
        rel = posixpath.normpath(unquote(path).lstrip("/"))
        if rel == ".":
            rel = ""
        if rel.startswith("../") or rel == "..":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        candidate = (WEB_DIST / rel).resolve()
        try:
            candidate.relative_to(WEB_DIST.resolve())
        except ValueError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        if candidate.is_file():
            self.path = "/" + rel
            self.directory = str(WEB_DIST)
            if head_only:
                super().do_HEAD()
            else:
                super().do_GET()
            return

        index = WEB_DIST / "index.html"
        if not index.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = index.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only:
            self.wfile.write(body)


def main() -> None:
    port = listen_port()
    tls = tls_config()
    if WEB_DIST.is_dir():
        print(f"已托管前端静态资源: {WEB_DIST}", flush=True)
    else:
        print(f"未找到 WEB_DIST/frontend/dist（仅 API）: {WEB_DIST}", flush=True)
    server = ThreadingHTTPServer(("", port), Handler)
    scheme = "http"
    if tls is not None:
        cert_file, key_file = tls
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=cert_file, keyfile=key_file)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        scheme = "https"
    base = configured_public_base_url() or "由请求 Host / X-Forwarded-* 头推断"
    print(f"监听 {scheme}://0.0.0.0:{port}  —  PUBLIC_BASE_URL={base}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
