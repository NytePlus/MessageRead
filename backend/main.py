from __future__ import annotations

import html
import json
import os
import posixpath
import secrets
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import redis


MESSAGE_TTL_SECONDS = 90 * 24 * 60 * 60
VISITOR_COOKIE = "read_receipt_visitor"
ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_-"
ROOT = Path(__file__).resolve().parent.parent

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


def public_base_url() -> str:
    raw = trim_str(os.getenv("PUBLIC_BASE_URL")).rstrip("/")
    if raw:
        return raw
    return f"http://localhost:{listen_port()}"


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
        origin = self.headers.get("Origin")
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

    def route_api_or_open(self, head_only: bool) -> bool:
        path = self.path_only()
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

    def write_json(self, status: HTTPStatus, value: dict[str, Any], head_only: bool = False) -> None:
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if not head_only:
            self.wfile.write(data)

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
        base = public_base_url()
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
        cookie_header = f"{VISITOR_COOKIE}={visitor}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax"
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
    if WEB_DIST.is_dir():
        print(f"已托管前端静态资源: {WEB_DIST}", flush=True)
    else:
        print(f"未找到 WEB_DIST/frontend/dist（仅 API）: {WEB_DIST}", flush=True)
    print(f"监听 :{port}  —  PUBLIC_BASE_URL={public_base_url()}", flush=True)
    ThreadingHTTPServer(("", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
