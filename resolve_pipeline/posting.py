"""Instagram and Facebook reel publishers with a network-free dry run."""

from dataclasses import dataclass
import json
import re
from time import sleep
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .retry import retry_read

_RATE_LIMIT_CODES = {4, 17, 32, 613, 80004}


@dataclass(frozen=True)
class Post:
    reel_id: str
    video_url: str
    caption: str
    authorized: bool = False


@dataclass(frozen=True)
class Receipt:
    platform: str
    remote_id: str
    dry_run: bool


class Publisher(Protocol):
    def publish(self, post: Post) -> Receipt: ...


class PostingError(RuntimeError):
    pass


class RateLimited(PostingError):
    pass


class TemporaryGraphError(PostingError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class MetaPublisher:
    def __init__(self, platform, *, version, account_id="", token="", dry_run=True,
                 graph_url="https://graph.facebook.com",
                 upload_url="https://rupload.facebook.com", poll_interval=20, poll_attempts=15):
        if platform not in {"instagram", "facebook"}:
            raise ValueError("Unsupported Meta platform")
        if not re.fullmatch(r"v[0-9]+\.[0-9]+", version):
            raise ValueError("Specify a Meta Graph API version")
        if not dry_run and (not account_id.isascii() or not account_id.isdigit() or not token):
            raise ValueError("Live publishing requires a Meta account ID and token")
        if poll_interval < 0 or poll_attempts < 1:
            raise ValueError("Invalid Meta polling bounds")
        self.platform, self.dry_run = platform, dry_run
        self.token, self.account_id = token, account_id
        self.graph = f"{graph_url.rstrip('/')}/{version}"
        self.upload_origin = urlsplit(upload_url)
        self.poll_interval, self.poll_attempts = poll_interval, poll_attempts
        self.transport = build_opener(_NoRedirect())

    def _request(self, method, url, fields=None, *, upload=False):
        fields = fields or {}
        headers = {"Authorization": f"{'OAuth' if upload else 'Bearer'} {self.token}"}
        data = None
        if upload:
            headers["file_url"] = fields["file_url"]
            data = b""
        elif method == "POST":
            data = urlencode(fields).encode()
        elif fields:
            url += "?" + urlencode(fields)
        try:
            with self.transport.open(Request(url, data=data, headers=headers, method=method), timeout=20) as response:
                payload = json.loads(response.read(1_000_000))
        except HTTPError as error:
            try:
                payload = json.loads(error.read(1_000_000))
            except ValueError:
                payload = {}
            finally:
                error.close()
            self._check(payload, status=error.code)
            if error.code in (408, 500, 502, 503, 504):
                raise TemporaryGraphError(f"Meta returned HTTP {error.code}") from None
            raise PostingError(f"Meta returned HTTP {error.code}") from None
        except (OSError, URLError):
            raise TemporaryGraphError("Meta transport failed") from None
        except ValueError:
            raise PostingError("Meta returned an invalid response") from None
        self._check(payload)
        return payload

    @staticmethod
    def _check(payload, status=200):
        if not isinstance(payload, dict):
            raise PostingError("Meta returned an invalid response")
        error = payload.get("error")
        code = error.get("code") if isinstance(error, dict) else None
        if status == 429 or (isinstance(code, int) and code in _RATE_LIMIT_CODES):
            raise RateLimited("Meta rate limit reached")
        if error:
            raise PostingError("Meta rejected the request")

    def _poll(self, remote_id, *, field):
        for index in range(self.poll_attempts):
            if index:
                sleep(self.poll_interval)
            payload = retry_read(
                lambda: self._request("GET", f"{self.graph}/{remote_id}", {"fields": field}),
                transient=(TemporaryGraphError, RateLimited),
            )
            if field == "status_code":
                state = payload.get(field)
                if state == "FINISHED":
                    return
                if state != "IN_PROGRESS":
                    raise PostingError("Instagram reel container is not publishable")
            else:
                status = payload.get("status", {})
                state = status.get("video_status")
                phase = status.get("uploading_phase", {}).get("status")
                if state != "error" and phase == "complete":
                    return
                if state == "error" or phase == "error":
                    raise PostingError("Facebook video upload failed")
        raise PostingError("Meta reel processing timed out")

    @staticmethod
    def _id(payload, key):
        value = payload.get(key)
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]+(?:_[0-9]+)?", value):
            raise PostingError(f"Meta did not return a valid {key}")
        return value

    def _instagram(self, post):
        account = f"{self.graph}/{self.account_id}"
        container = self._id(self._request("POST", account + "/media", {
            "media_type": "REELS", "video_url": post.video_url, "caption": post.caption,
            "share_to_feed": "true",
        }), "id")
        self._poll(container, field="status_code")
        return self._id(self._request("POST", account + "/media_publish",
                                      {"creation_id": container}), "id")

    def _facebook(self, post):
        endpoint = f"{self.graph}/{self.account_id}/video_reels"
        session = self._request("POST", endpoint, {"upload_phase": "start"})
        video_id = self._id(session, "video_id")
        address = session.get("upload_url")
        if not isinstance(address, str):
            raise PostingError("Meta did not provide an upload URL")
        origin = urlsplit(address)
        if ((origin.scheme, origin.netloc) != (self.upload_origin.scheme, self.upload_origin.netloc)
                or not origin.path.startswith("/video-upload/")):
            raise PostingError("Meta upload URL did not match the configured upload origin")
        uploaded = self._request("POST", address, {"file_url": post.video_url}, upload=True)
        if uploaded.get("success") is not True:
            raise PostingError("Meta did not accept the uploaded video")
        self._poll(video_id, field="status")
        result = self._request("POST", endpoint, {
            "upload_phase": "finish", "video_id": video_id,
            "video_state": "PUBLISHED", "description": post.caption,
        })
        if result.get("success") is not True:
            raise PostingError("Facebook did not confirm publishing")
        return video_id

    def publish(self, post: Post) -> Receipt:
        if not post.reel_id:
            raise ValueError("Reel ID is required")
        if self.dry_run:
            return Receipt(self.platform, f"dry-run:{post.reel_id}", True)
        if post.authorized is not True:
            raise PostingError("Live posting requires a pipeline authorization")
        media = urlsplit(post.video_url)
        if media.scheme != "https" or not media.hostname or media.username or media.password:
            raise ValueError("Live posting requires a public HTTPS video URL")
        remote_id = self._instagram(post) if self.platform == "instagram" else self._facebook(post)
        return Receipt(self.platform, remote_id, False)
