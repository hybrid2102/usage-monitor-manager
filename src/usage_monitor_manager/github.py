"""GitHub releases client: ETag cache, rate limit handling, asset selection.

Implements the ``github.py`` half of PLAN.md section 6.3. Deliberately built on
``urllib`` alone (PLAN.md section 4.2): one less dependency inside the
PyInstaller executable, and the only two things ``requests`` would buy us here
- redirect following and streaming - ``urllib`` already does.

Three behaviours are worth knowing about before reading the code:

* **ETag cache.** Every successful response is stored under
  ``cache_dir/<owner>__<repo>.json`` together with its ``ETag``. The next call
  sends ``If-None-Match`` and a ``304`` costs nothing against the anonymous
  rate limit of 60 requests/hour, which four monitors plus the manager's own
  self-update would otherwise burn through quickly.
* **No bare exceptions.** Everything that can go wrong on the wire - DNS,
  timeouts, TLS, 5xx, malformed JSON - surfaces as :class:`GitHubError` with a
  message meant for a human, and the specific case of an exhausted rate limit
  as :class:`RateLimitedError` carrying the reset time.
* **Asset selection** is a port of ``Select-ReleaseAsset`` from the PowerShell
  updater, including its fallback of preferring an asset that does not
  advertise a *foreign* architecture when no name carries an architecture hint.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from . import __version__
from .model import JsonDict, ReleaseAsset, ReleaseInfo

__all__ = [
    "API_ROOT",
    "DEFAULT_TIMEOUT",
    "USER_AGENT",
    "GitHubError",
    "ProgressCallback",
    "RateLimitState",
    "RateLimitedError",
    "default_cache_dir",
    "download",
    "fetch_text",
    "fetch_latest_release",
    "parse_release",
    "rate_limit_state",
    "resolve_token",
    "select_asset",
]

API_ROOT = "https://api.github.com"
USER_AGENT = f"usage-monitor-manager/{__version__}"
ACCEPT_JSON = "application/vnd.github+json"
API_VERSION = "2022-11-28"
DEFAULT_TIMEOUT = 20.0
_CHUNK = 64 * 1024

ProgressCallback = Callable[[int, int], None]
"""``on_progress(bytes_done, total_bytes)``; ``total_bytes`` is 0 when unknown."""


class GitHubError(RuntimeError):
    """Any failure talking to GitHub, already phrased for a human reader."""


class RateLimitedError(GitHubError):
    """The API rate limit is exhausted; ``reset_at`` says when it frees up."""

    def __init__(self, message: str, reset_at: datetime | None) -> None:
        super().__init__(message)
        self.reset_at = reset_at


@dataclass(frozen=True, slots=True)
class RateLimitState:
    """The ``X-RateLimit-*`` trio from the most recent response."""

    limit: int | None
    remaining: int | None
    reset_at: datetime | None
    authenticated: bool = False

    def to_dict(self) -> JsonDict:
        return {
            "limit": self.limit,
            "remaining": self.remaining,
            "reset_at": None if self.reset_at is None else self.reset_at.isoformat(),
            "authenticated": self.authenticated,
        }


_last_rate_limit: RateLimitState | None = None


def rate_limit_state() -> RateLimitState | None:
    """Rate limit seen in the last response, or ``None`` before any call."""
    return _last_rate_limit


# --------------------------------------------------------------------------- #
# Token resolution
# --------------------------------------------------------------------------- #


def resolve_token(token: str | None = None) -> str | None:
    """Resolve the API token: parameter, then ``GITHUB_TOKEN``/``GH_TOKEN``.

    Phase 6 inserts the Windows Credential Manager (``secrets.py``) at the head
    of this chain; the order documented in PLAN.md section 6.5-bis is otherwise
    already the one implemented here.
    """
    if token and token.strip():
        return token.strip()
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return None


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #


def default_cache_dir() -> Path:
    """``%LOCALAPPDATA%\\UsageMonitorManager\\cache`` (``~/.cache`` elsewhere)."""
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "UsageMonitorManager" / "cache"


def _cache_path(cache_dir: Path, repo: str) -> Path:
    owner, _, name = repo.partition("/")
    safe = f"{owner}__{name}" if name else owner
    return cache_dir / f"{safe}.json"


def _read_cache(path: Path) -> JsonDict | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        entry: object = json.loads(raw)
    except ValueError:
        return None
    return cast(JsonDict, entry) if isinstance(entry, dict) else None


def _write_cache(path: Path, *, url: str, etag: str | None, body: Any) -> None:
    entry = {
        "url": url,
        "etag": etag,
        "fetched_at": datetime.now(tz=UTC).isoformat(),
        "body": body,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(entry, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:  # a cache we cannot write is a slow path, never an error
        return


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #


def _open(request: urllib.request.Request, timeout: float) -> Any:
    """The single seam through which every network call of this module goes.

    Tests monkeypatch this function; nothing else in the module (or in
    ``updater.py``) touches ``urllib`` directly.
    """
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 - fixed https API root


def _headers(token: str | None, *, etag: str | None = None, accept: str = ACCEPT_JSON) -> dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": accept,
        "X-GitHub-Api-Version": API_VERSION,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if etag:
        headers["If-None-Match"] = etag
    return headers


def _header_int(headers: Any, name: str) -> int | None:
    try:
        raw = headers.get(name)
    except AttributeError:  # pragma: no cover - defensive
        return None
    if raw is None:
        return None
    try:
        return int(str(raw).strip())
    except ValueError:
        return None


def _reset_at(headers: Any) -> datetime | None:
    epoch = _header_int(headers, "X-RateLimit-Reset")
    if epoch is None:
        return None
    try:
        return datetime.fromtimestamp(epoch, tz=UTC)
    except (OverflowError, OSError, ValueError):  # pragma: no cover - absurd header
        return None


def _record_rate_limit(headers: Any, *, authenticated: bool) -> RateLimitState:
    global _last_rate_limit
    state = RateLimitState(
        limit=_header_int(headers, "X-RateLimit-Limit"),
        remaining=_header_int(headers, "X-RateLimit-Remaining"),
        reset_at=_reset_at(headers),
        authenticated=authenticated,
    )
    _last_rate_limit = state
    return state


def _rate_limit_message(state: RateLimitState, *, authenticated: bool) -> str:
    when = "unknown time" if state.reset_at is None else state.reset_at.astimezone().strftime("%H:%M:%S")
    hint = "" if authenticated else " (set GITHUB_TOKEN to raise the anonymous limit of 60 requests/hour)"
    return f"GitHub API rate limit exhausted; it resets at {when}{hint}"


def _get_json(url: str, *, token: str | None, etag: str | None, timeout: float) -> tuple[Any, str | None]:
    """GET a JSON document (object or array). ``(None, etag)`` on a 304."""
    request = urllib.request.Request(url, headers=_headers(token, etag=etag))  # noqa: S310 - https only
    authenticated = token is not None
    try:
        with _open(request, timeout) as response:
            _record_rate_limit(response.headers, authenticated=authenticated)
            payload = response.read()
            new_etag = response.headers.get("ETag")
    except urllib.error.HTTPError as error:
        state = _record_rate_limit(error.headers, authenticated=authenticated)
        if error.code == 304:
            return (None, etag)
        if error.code in (403, 429) and state.remaining == 0:
            raise RateLimitedError(_rate_limit_message(state, authenticated=authenticated), state.reset_at) from error
        if error.code == 404:
            raise GitHubError(f"GitHub says {url} does not exist (404): wrong repo, or no release published") from error
        raise GitHubError(f"GitHub answered {error.code} {error.reason} for {url}") from error
    except urllib.error.URLError as error:
        raise GitHubError(f"cannot reach GitHub ({url}): {error.reason}") from error
    except TimeoutError as error:
        raise GitHubError(f"timed out after {timeout:g}s waiting for GitHub ({url})") from error
    except OSError as error:
        raise GitHubError(f"network error talking to GitHub ({url}): {error}") from error

    try:
        body: object = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as error:
        raise GitHubError(f"GitHub returned a response that is not valid JSON ({url})") from error
    if not isinstance(body, (dict, list)):  # pragma: no cover - defensive
        raise GitHubError(f"GitHub returned an unexpected JSON shape ({url})")
    return (cast(Any, body), cast("str | None", new_etag or etag))


# --------------------------------------------------------------------------- #
# Asset selection (port of Select-ReleaseAsset)
# --------------------------------------------------------------------------- #

_ARCH_PATTERNS: dict[str, tuple[str, ...]] = {
    "ARM64": ("arm64", "aarch64"),
    "AMD64": ("x64", "amd64", "win64"),
    "X86": ("x86", "win32", "ia32"),
}
_FOREIGN_FOR_ARM64 = ("x86", "x64", "amd64", "win32", "win64", "ia32")
_FOREIGN_FOR_INTEL = ("arm64", "aarch64")


def _processor_architecture() -> str:
    return (os.environ.get("PROCESSOR_ARCHITECTURE") or "").strip().upper()


def select_asset(assets: Sequence[JsonDict], *, architecture: str | None = None) -> JsonDict | None:
    """Pick the ``.exe`` asset that fits this machine, or ``None`` if there is none.

    Port of ``Select-ReleaseAsset``: a single ``.exe`` wins unconditionally;
    with several, the architecture hints of ``PROCESSOR_ARCHITECTURE`` are tried
    in order; failing that, an asset whose name does not name a foreign
    architecture is preferred, and only then the first one.
    """
    exe_assets = [asset for asset in assets if str(asset.get("name", "")).casefold().endswith(".exe")]
    if not exe_assets:
        return None
    if len(exe_assets) == 1:
        return exe_assets[0]

    arch = (architecture or _processor_architecture()).upper()
    for pattern in _ARCH_PATTERNS.get(arch, ()):
        for asset in exe_assets:
            if pattern in str(asset.get("name", "")).casefold():
                return asset

    foreign = _FOREIGN_FOR_ARM64 if arch == "ARM64" else _FOREIGN_FOR_INTEL
    for asset in exe_assets:
        name = str(asset.get("name", "")).casefold()
        if not any(marker in name for marker in foreign):
            return asset
    return exe_assets[0]


def _sidecar_url(assets: Sequence[JsonDict], asset_name: str) -> str | None:
    wanted = f"{asset_name}.sha256".casefold()
    for asset in assets:
        if str(asset.get("name", "")).casefold() == wanted:
            url = asset.get("browser_download_url")
            return str(url) if url else None
    return None


def _parse_datetime(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_release(payload: JsonDict, *, architecture: str | None = None) -> ReleaseInfo:
    """Turn one GitHub release document into a :class:`ReleaseInfo`."""
    assets = _as_dicts(payload.get("assets"))
    chosen = select_asset(assets, architecture=architecture)

    asset: ReleaseAsset | None = None
    sidecar: str | None = None
    if chosen is not None:
        name = str(chosen.get("name", ""))
        digest = chosen.get("digest")
        asset = ReleaseAsset(
            name=name,
            url=str(chosen.get("browser_download_url", "")),
            size=int(chosen.get("size") or 0),
            digest=str(digest) if digest else None,
        )
        sidecar = _sidecar_url(assets, name)

    tag = str(payload.get("tag_name") or "")
    from .versions import format_version  # local import: versions imports the platform layer

    return ReleaseInfo(
        tag=tag,
        version=format_version(tag),
        published_at=_parse_datetime(payload.get("published_at")),
        html_url=str(payload.get("html_url") or ""),
        body_markdown=str(payload.get("body") or ""),
        asset=asset,
        sidecar_sha_url=sidecar,
        is_prerelease=bool(payload.get("prerelease")),
    )


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def _as_dicts(value: Any) -> list[JsonDict]:
    """Keep the JSON objects out of an arbitrary decoded payload."""
    if not isinstance(value, list):
        return []
    items = cast("list[object]", value)
    return [cast(JsonDict, item) for item in items if isinstance(item, dict)]


def _newest_published(payload: Any, repo: str) -> JsonDict:
    """First non-draft release of a ``/releases`` array (newest first)."""
    for release in _as_dicts(payload):
        if not release.get("draft"):
            return release
    raise GitHubError(f"{repo} has published no release yet")


def fetch_latest_release(
    repo: str,
    *,
    include_prerelease: bool = False,
    token: str | None = None,
    cache_dir: Path | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    architecture: str | None = None,
) -> ReleaseInfo:
    """Latest release of ``owner/name``, through the on-disk ETag cache.

    With ``include_prerelease`` the newest non-draft release is taken from
    ``/releases`` instead of ``/releases/latest`` (which skips prereleases by
    definition). A ``304 Not Modified`` reuses the cached body, so repeated
    checks are free.

    Raises:
        RateLimitedError: the API limit is exhausted (carries ``reset_at``).
        GitHubError: any other network, HTTP or payload problem.
    """
    resolved_token = resolve_token(token)
    directory = cache_dir if cache_dir is not None else default_cache_dir()
    path = _cache_path(directory, repo)
    url = f"{API_ROOT}/repos/{repo}/releases" + ("?per_page=20" if include_prerelease else "/latest")

    cached = _read_cache(path)
    if cached is not None and cached.get("url") != url:
        cached = None  # the cache holds the answer to a different question
    etag = str(cached["etag"]) if cached and cached.get("etag") else None

    body, new_etag = _get_json(url, token=resolved_token, etag=etag, timeout=timeout)
    if body is None:
        if cached is None or "body" not in cached:  # pragma: no cover - 304 without a cache
            raise GitHubError(f"GitHub answered 304 for {url} but no cached body is available")
        payload: Any = cached["body"]
    else:
        payload = body
        _write_cache(path, url=url, etag=new_etag, body=payload)

    if include_prerelease:
        return parse_release(_newest_published(payload, repo), architecture=architecture)
    if not isinstance(payload, dict):  # pragma: no cover - defensive
        raise GitHubError(f"unexpected release payload for {repo}")
    return parse_release(cast(JsonDict, payload), architecture=architecture)


def download(
    asset: ReleaseAsset,
    dest_path: Path,
    on_progress: ProgressCallback | None = None,
    *,
    token: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Path:
    """Stream ``asset`` into ``dest_path``, reporting progress as it goes.

    The caller decides the path (the updater downloads next to the executable
    it is about to replace, so the final swap is a rename on one volume). The
    size announced by the API is checked afterwards: a truncated download is an
    error, not a silently broken executable.

    Raises:
        GitHubError: on any network error, or when the size does not match.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(  # noqa: S310 - https release URL from the API
        asset.url,
        headers=_headers(resolve_token(token) if token else None, accept="application/octet-stream"),
    )
    done = 0
    try:
        with _open(request, timeout) as response:
            total = _header_int(response.headers, "Content-Length") or asset.size or 0
            if on_progress is not None:
                on_progress(0, total)
            with dest_path.open("wb") as handle:
                while True:
                    chunk = response.read(_CHUNK)
                    if not chunk:
                        break
                    handle.write(chunk)
                    done += len(chunk)
                    if on_progress is not None:
                        on_progress(done, total)
    except urllib.error.HTTPError as error:
        _discard(dest_path)
        raise GitHubError(f"GitHub answered {error.code} {error.reason} downloading {asset.name}") from error
    except urllib.error.URLError as error:
        _discard(dest_path)
        raise GitHubError(f"cannot download {asset.name}: {error.reason}") from error
    except TimeoutError as error:
        _discard(dest_path)
        raise GitHubError(f"timed out downloading {asset.name} after {timeout:g}s") from error
    except OSError as error:
        _discard(dest_path)
        raise GitHubError(f"error writing {dest_path}: {error}") from error

    if asset.size and done != asset.size:
        _discard(dest_path)
        raise GitHubError(f"{asset.name} is {done} bytes, expected {asset.size}: download incomplete, file removed")
    return dest_path


def fetch_text(url: str, *, token: str | None = None, timeout: float = DEFAULT_TIMEOUT) -> str:
    """Fetch a small text file (a ``.sha256`` sidecar) as a decoded string.

    Raises:
        GitHubError: on any network error.
    """
    request = urllib.request.Request(  # noqa: S310 - https release URL from the API
        url,
        headers=_headers(resolve_token(token) if token else None, accept="text/plain"),
    )
    try:
        with _open(request, timeout) as response:
            payload = response.read(64 * 1024)
    except urllib.error.HTTPError as error:
        raise GitHubError(f"GitHub answered {error.code} {error.reason} fetching {url}") from error
    except urllib.error.URLError as error:
        raise GitHubError(f"cannot fetch {url}: {error.reason}") from error
    except TimeoutError as error:
        raise GitHubError(f"timed out fetching {url} after {timeout:g}s") from error
    except OSError as error:
        raise GitHubError(f"network error fetching {url}: {error}") from error
    if isinstance(payload, str):  # pragma: no cover - only a fake would do this
        return payload
    return payload.decode("utf-8", errors="replace")


def _discard(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:  # pragma: no cover - locked by an antivirus scan
        pass
