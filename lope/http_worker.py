"""Isolated stdlib HTTP request worker.

DNS/connect/read can block below Python's cooperative control. Running this
module through :mod:`lope.supervisor` gives the caller a true total wall
deadline and a killable boundary.
"""

from __future__ import annotations

import base64
import json
import sys
import urllib.error
import urllib.request


def _read_capped(response, limit: int) -> bytes:
    declared = response.headers.get("Content-Length")
    if declared:
        try:
            if int(declared) > limit:
                raise ValueError(f"response Content-Length exceeds {limit} bytes")
        except ValueError as exc:
            if "exceeds" in str(exc):
                raise
    chunks = bytearray()
    while True:
        part = response.read(min(64 * 1024, limit + 1 - len(chunks)))
        if not part:
            break
        chunks.extend(part)
        if len(chunks) > limit:
            raise ValueError(f"response body exceeds {limit} bytes")
    return bytes(chunks)


def main() -> int:
    try:
        spec = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        body = base64.b64decode(spec.get("body_b64") or "")
        request = urllib.request.Request(
            str(spec["url"]),
            data=body,
            headers={str(k): str(v) for k, v in (spec.get("headers") or {}).items()},
            method=str(spec.get("method") or "POST"),
        )
        timeout = float(spec.get("socket_timeout") or 30.0)
        limit = int(spec.get("response_limit") or 2 * 1024 * 1024)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = _read_capped(response, limit)
                status = int(getattr(response, "status", 200))
                headers = dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            raw = _read_capped(exc, limit)
            status = int(exc.code)
            headers = dict(exc.headers.items()) if exc.headers else {}
        print(json.dumps({
            "ok": 200 <= status < 300,
            "status": status,
            "headers": headers,
            "body_b64": base64.b64encode(raw).decode("ascii"),
        }, separators=(",", ":")))
        return 0
    except BaseException as exc:
        print(json.dumps({
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}"[:500],
        }, separators=(",", ":")))
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
