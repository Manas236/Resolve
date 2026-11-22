"""Telegram review callback parsing, independent of network transport."""

import re

from .retry import retry_read

_CALLBACK = re.compile(r"(approve|reject|regenerate|muted):([A-Za-z0-9_-]{1,48})")


def review_callback(update, *, chat_id, reviewer_id):
    """Return an action and reel ID only for the configured reviewer and chat."""
    callback = update.get("callback_query") or {}
    sender = callback.get("from") or {}
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    if sender.get("id") != reviewer_id or chat.get("id") != chat_id:
        return None
    data = callback.get("data")
    match = _CALLBACK.fullmatch(data) if isinstance(data, str) else None
    return match.groups() if match is not None else None


class TemporaryTelegramError(RuntimeError):
    pass


class TelegramAPI:
    """Minimal Bot API client for review previews, polling, and acknowledgements."""

    def __init__(self, token, *, base_url="https://api.telegram.org", timeout=35):
        if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
            raise ValueError("Invalid Telegram token format")
        self.endpoint = f"{base_url.rstrip('/')}/bot{token}"
        self.timeout = timeout

    def _request(self, method, fields, video=None):
        import json
        from pathlib import Path
        from urllib.error import HTTPError, URLError
        from urllib.request import Request, urlopen
        from uuid import uuid4

        if video is None:
            body = json.dumps(fields).encode()
            content_type = "application/json"
        else:
            path = Path(video)
            if not 0 < path.stat().st_size <= 50_000_000:
                raise ValueError("Telegram preview must be nonempty and at most 50 MB")
            boundary = uuid4().hex
            parts = []
            for key, value in fields.items():
                parts.append(
                    (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n"
                     f"{json.dumps(value) if isinstance(value, dict) else value}\r\n").encode()
                )
            parts.extend([
                (f"--{boundary}\r\nContent-Disposition: form-data; name=\"video\"; "
                 "filename=\"reel.mp4\"\r\nContent-Type: video/mp4\r\n\r\n").encode(),
                path.read_bytes(), f"\r\n--{boundary}--\r\n".encode(),
            ])
            body = b"".join(parts)
            content_type = f"multipart/form-data; boundary={boundary}"
        request = Request(f"{self.endpoint}/{method}", data=body,
                          headers={"Content-Type": content_type})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read(2_000_000))
        except HTTPError as error:
            retry_after = error.headers.get("Retry-After", "")
            error.close()
            if error.code in (408, 429, 500, 502, 503, 504):
                temporary = TemporaryTelegramError(f"Telegram {method} temporarily unavailable")
                if retry_after.isascii() and retry_after.isdigit() and len(retry_after) <= 6:
                    temporary.retry_after = int(retry_after)
                raise temporary from None
            raise RuntimeError(f"Telegram {method} returned HTTP {error.code}") from None
        except (URLError, OSError):
            raise TemporaryTelegramError(f"Telegram {method} transport failed") from None
        except ValueError:
            raise RuntimeError(f"Telegram {method} request failed") from None
        if not isinstance(result, dict) or not result.get("ok") or "result" not in result:
            raise RuntimeError(f"Telegram {method} returned an unsuccessful response")
        return result["result"]

    def preview(self, chat_id, reel_id, video, caption=""):
        buttons = [
            {"text": name.title(), "callback_data": f"{name}:{reel_id}"}
            for name in ("approve", "reject", "regenerate", "muted")
        ]
        return self._request("sendVideo", {
            "chat_id": chat_id, "caption": caption,
            "reply_markup": {"inline_keyboard": [buttons[:2], buttons[2:]]},
        }, video=video)

    def updates(self, offset):
        return retry_read(
            lambda: self._request("getUpdates", {
                "offset": offset, "timeout": 20, "allowed_updates": ["callback_query"],
            }),
            transient=TemporaryTelegramError,
        )

    def failure_alert(self, chat_id, summary):
        return self._request("sendMessage", {"chat_id": chat_id, "text": summary[:1000]})

    def acknowledge(self, callback_id, message):
        return self._request("answerCallbackQuery", {
            "callback_query_id": callback_id, "text": message,
        })


class ReviewBot:
    def __init__(self, api, pipeline, *, chat_id, reviewer_id):
        self.api = api
        self.pipeline = pipeline
        self.chat_id = chat_id
        self.reviewer_id = reviewer_id

    def send_preview(self, reel_id, caption=""):
        return self.api.preview(self.chat_id, reel_id,
                                self.pipeline.directory / f"{reel_id}.mp4", caption)

    def poll(self, offset=0):
        for update in self.api.updates(offset):
            callback = update.get("callback_query") or {}
            callback_id = callback.get("id")
            action = review_callback(update, chat_id=self.chat_id, reviewer_id=self.reviewer_id)
            if callback_id and action and action[1].isascii() and action[1].isdigit() and len(action[1]) <= 18:
                changed = self.pipeline.telegram_review(callback_id, int(action[1]), action[0])
                self.api.acknowledge(callback_id, "Saved" if changed else "Already handled or unavailable")
            elif callback_id:
                self.api.acknowledge(callback_id, "This action is unavailable")
            offset = max(offset, update["update_id"] + 1)
        return offset
