"""Tests for the GitHub client.

Safety rule for this file: **no test touches the network**. Every test replaces
``github._open`` - the single seam through which the module reaches urllib - so
the suite runs identically on CI and offline. The release payloads are the real
documents of the three repos, fetched once with
``gh api repos/<repo>/releases/latest`` into ``tests/fixtures/releases/``.
"""

from __future__ import annotations

import email.message
import json
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from usage_monitor_manager import __version__, github
from usage_monitor_manager.github import GitHubError, RateLimitedError

FIXTURES = Path(__file__).parent / "fixtures" / "releases"
CLAUDE_FIXTURE = "jens-duttke_usage-monitor-for-claude"
COPILOT_FIXTURE = "hybrid2102_usage-monitor-for-copilot"
CODEX_FIXTURE = "hybrid2102_usage-monitor-for-codex"


def load_fixture(name: str) -> dict[str, Any]:
    """One of the three real release documents, as a dict."""
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class FakeResponse:
    """The tiny slice of an ``http.client.HTTPResponse`` the client uses."""

    def __init__(self, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.body = body
        self.headers = headers or {}
        self._offset = 0

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            chunk, self._offset = self.body[self._offset :], len(self.body)
            return chunk
        chunk = self.body[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


def http_error(code: int, headers: dict[str, str] | None = None, reason: str = "boom") -> urllib.error.HTTPError:
    message = email.message.Message()
    for key, value in (headers or {}).items():
        message[key] = value
    return urllib.error.HTTPError("https://api.github.com/x", code, reason, message, None)  # type: ignore[arg-type]


class Recorder:
    """A fake ``_open`` that records requests and replays canned answers."""

    def __init__(self, *answers: object) -> None:
        self.answers = list(answers)
        self.requests: list[Any] = []

    def __call__(self, request: Any, timeout: float) -> Any:
        self.requests.append(request)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, BaseException):
            raise answer
        return answer

    @property
    def last_headers(self) -> dict[str, str]:
        return {key.lower(): value for key, value in self.requests[-1].header_items()}


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    return tmp_path / "cache"


@pytest.fixture(autouse=True)
def no_ambient_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's ``GITHUB_TOKEN`` must not change what the tests assert."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)


def install(monkeypatch: pytest.MonkeyPatch, recorder: Recorder) -> Recorder:
    monkeypatch.setattr(github, "_open", recorder)
    return recorder


def json_response(payload: object, headers: dict[str, str] | None = None) -> FakeResponse:
    return FakeResponse(json.dumps(payload).encode("utf-8"), headers)


# --------------------------------------------------------------------------- #
# Parsing the real releases
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("fixture", "tag", "version", "asset_name", "has_sidecar"),
    [
        (CLAUDE_FIXTURE, "v1.22.0", "1.22.0", "UsageMonitorForClaude.exe", False),
        (COPILOT_FIXTURE, "v0.3.2", "0.3.2", "UsageMonitorForCopilot.exe", True),
        (CODEX_FIXTURE, "v0.1.0", "0.1.0", "UsageMonitorForCodex.exe", True),
    ],
)
def test_parse_release_of_every_real_repo(
    fixture: str, tag: str, version: str, asset_name: str, has_sidecar: bool
) -> None:
    release = github.parse_release(load_fixture(fixture))
    assert (release.tag, release.version) == (tag, version)
    assert release.is_prerelease is False
    assert release.published_at is not None
    assert release.body_markdown.strip()
    assert release.asset is not None
    assert release.asset.name == asset_name
    assert release.asset.size > 1_000_000
    assert release.asset.digest is not None and release.asset.digest.startswith("sha256:")
    if has_sidecar:
        assert release.sidecar_sha_url == f"{release.asset.url}.sha256"
    else:
        assert release.sidecar_sha_url is None


def test_fetch_latest_release_sends_the_documented_headers(monkeypatch: pytest.MonkeyPatch, cache_dir: Path) -> None:
    recorder = install(monkeypatch, Recorder(json_response(load_fixture(COPILOT_FIXTURE))))
    release = github.fetch_latest_release("hybrid2102/usage-monitor-for-copilot", cache_dir=cache_dir, token="t0ken")

    assert release.version == "0.3.2"
    headers = recorder.last_headers
    assert headers["user-agent"] == f"usage-monitor-manager/{__version__}"
    assert headers["accept"] == "application/vnd.github+json"
    assert headers["authorization"] == "Bearer t0ken"
    assert recorder.requests[-1].full_url.endswith("/repos/hybrid2102/usage-monitor-for-copilot/releases/latest")


def test_anonymous_request_has_no_authorization_header(monkeypatch: pytest.MonkeyPatch, cache_dir: Path) -> None:
    recorder = install(monkeypatch, Recorder(json_response(load_fixture(CODEX_FIXTURE))))
    github.fetch_latest_release("hybrid2102/usage-monitor-for-codex", cache_dir=cache_dir)
    assert "authorization" not in recorder.last_headers


def test_include_prerelease_reads_the_releases_list(monkeypatch: pytest.MonkeyPatch, cache_dir: Path) -> None:
    latest = load_fixture(COPILOT_FIXTURE)
    draft = {**latest, "draft": True, "tag_name": "v0.4.0-draft"}
    recorder = install(monkeypatch, Recorder(json_response([draft, latest])))

    release = github.fetch_latest_release(
        "hybrid2102/usage-monitor-for-copilot", include_prerelease=True, cache_dir=cache_dir
    )

    assert release.tag == "v0.3.2"  # the draft is skipped
    assert "/releases?per_page=20" in recorder.requests[-1].full_url


def test_an_empty_releases_list_is_a_clear_error(monkeypatch: pytest.MonkeyPatch, cache_dir: Path) -> None:
    install(monkeypatch, Recorder(json_response([])))
    with pytest.raises(GitHubError, match="published no release"):
        github.fetch_latest_release(
            "hybrid2102/usage-monitor-for-copilot", include_prerelease=True, cache_dir=cache_dir
        )


# --------------------------------------------------------------------------- #
# ETag cache
# --------------------------------------------------------------------------- #


def test_etag_round_trip_reuses_the_cached_body(monkeypatch: pytest.MonkeyPatch, cache_dir: Path) -> None:
    payload = load_fixture(COPILOT_FIXTURE)
    recorder = install(
        monkeypatch,
        Recorder(
            json_response(payload, {"ETag": 'W/"abc123"', "X-RateLimit-Remaining": "58"}),
            http_error(304, {"X-RateLimit-Remaining": "58"}, "Not Modified"),
        ),
    )

    first = github.fetch_latest_release("hybrid2102/usage-monitor-for-copilot", cache_dir=cache_dir)
    cache_file = cache_dir / "hybrid2102__usage-monitor-for-copilot.json"
    assert cache_file.exists()
    stored = json.loads(cache_file.read_text(encoding="utf-8"))
    assert stored["etag"] == 'W/"abc123"'
    assert stored["fetched_at"]
    assert stored["body"]["tag_name"] == "v0.3.2"
    assert "if-none-match" not in recorder.last_headers

    second = github.fetch_latest_release("hybrid2102/usage-monitor-for-copilot", cache_dir=cache_dir)
    assert recorder.last_headers["if-none-match"] == 'W/"abc123"'
    assert second == first  # the 304 produced the very same ReleaseInfo


def test_a_cache_written_for_another_url_is_ignored(monkeypatch: pytest.MonkeyPatch, cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True)
    (cache_dir / "hybrid2102__usage-monitor-for-copilot.json").write_text(
        json.dumps({"url": "https://api.github.com/other", "etag": 'W/"stale"', "body": {}}), encoding="utf-8"
    )
    recorder = install(monkeypatch, Recorder(json_response(load_fixture(COPILOT_FIXTURE))))

    github.fetch_latest_release("hybrid2102/usage-monitor-for-copilot", cache_dir=cache_dir)
    assert "if-none-match" not in recorder.last_headers


def test_a_corrupt_cache_file_is_ignored(monkeypatch: pytest.MonkeyPatch, cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True)
    (cache_dir / "hybrid2102__usage-monitor-for-codex.json").write_text("{not json", encoding="utf-8")
    install(monkeypatch, Recorder(json_response(load_fixture(CODEX_FIXTURE))))
    assert github.fetch_latest_release("hybrid2102/usage-monitor-for-codex", cache_dir=cache_dir).version == "0.1.0"


# --------------------------------------------------------------------------- #
# Failure modes
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("code", [403, 429])
def test_rate_limit_raises_with_the_reset_time(monkeypatch: pytest.MonkeyPatch, cache_dir: Path, code: int) -> None:
    install(
        monkeypatch,
        Recorder(
            http_error(
                code,
                {"X-RateLimit-Limit": "60", "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1789999200"},
                "rate limit exceeded",
            )
        ),
    )
    with pytest.raises(RateLimitedError) as exc:
        github.fetch_latest_release("hybrid2102/usage-monitor-for-copilot", cache_dir=cache_dir)

    assert exc.value.reset_at is not None
    assert exc.value.reset_at.timestamp() == 1789999200
    assert "rate limit exhausted" in str(exc.value)
    assert "GITHUB_TOKEN" in str(exc.value)  # the anonymous hint
    state = github.rate_limit_state()
    assert state is not None
    assert (state.limit, state.remaining, state.authenticated) == (60, 0, False)


def test_a_403_with_requests_left_is_not_a_rate_limit(monkeypatch: pytest.MonkeyPatch, cache_dir: Path) -> None:
    install(monkeypatch, Recorder(http_error(403, {"X-RateLimit-Remaining": "12"}, "Forbidden")))
    with pytest.raises(GitHubError) as exc:
        github.fetch_latest_release("hybrid2102/usage-monitor-for-copilot", cache_dir=cache_dir)
    assert not isinstance(exc.value, RateLimitedError)
    assert "403" in str(exc.value)


def test_a_404_says_what_it_means(monkeypatch: pytest.MonkeyPatch, cache_dir: Path) -> None:
    install(monkeypatch, Recorder(http_error(404, {}, "Not Found")))
    with pytest.raises(GitHubError, match="does not exist"):
        github.fetch_latest_release("hybrid2102/nope", cache_dir=cache_dir)


def test_a_500_becomes_a_github_error(monkeypatch: pytest.MonkeyPatch, cache_dir: Path) -> None:
    install(monkeypatch, Recorder(http_error(500, {}, "Internal Server Error")))
    with pytest.raises(GitHubError, match="500"):
        github.fetch_latest_release("hybrid2102/usage-monitor-for-copilot", cache_dir=cache_dir)


def test_a_timeout_becomes_a_github_error(monkeypatch: pytest.MonkeyPatch, cache_dir: Path) -> None:
    install(monkeypatch, Recorder(TimeoutError("timed out")))
    with pytest.raises(GitHubError, match="timed out"):
        github.fetch_latest_release("hybrid2102/usage-monitor-for-copilot", cache_dir=cache_dir)


def test_an_unreachable_host_becomes_a_github_error(monkeypatch: pytest.MonkeyPatch, cache_dir: Path) -> None:
    install(monkeypatch, Recorder(urllib.error.URLError("getaddrinfo failed")))
    with pytest.raises(GitHubError, match="cannot reach GitHub"):
        github.fetch_latest_release("hybrid2102/usage-monitor-for-copilot", cache_dir=cache_dir)


def test_a_non_json_body_becomes_a_github_error(monkeypatch: pytest.MonkeyPatch, cache_dir: Path) -> None:
    install(monkeypatch, Recorder(FakeResponse(b"<html>maintenance</html>")))
    with pytest.raises(GitHubError, match="not valid JSON"):
        github.fetch_latest_release("hybrid2102/usage-monitor-for-copilot", cache_dir=cache_dir)


# --------------------------------------------------------------------------- #
# Asset selection (port of Select-ReleaseAsset)
# --------------------------------------------------------------------------- #


def _assets(*names: str) -> list[dict[str, Any]]:
    return [{"name": name, "browser_download_url": f"https://example/{name}", "size": 1} for name in names]


@pytest.mark.parametrize(
    ("architecture", "expected"),
    [
        ("AMD64", "Monitor-x64.exe"),
        ("ARM64", "Monitor-arm64.exe"),
        ("X86", "Monitor-x86.exe"),
    ],
)
def test_asset_selection_follows_the_architecture(architecture: str, expected: str) -> None:
    assets = _assets("Monitor-arm64.exe", "Monitor-x86.exe", "Monitor-x64.exe", "notes.txt")
    chosen = github.select_asset(assets, architecture=architecture)
    assert chosen is not None and chosen["name"] == expected


def test_a_single_exe_wins_regardless_of_architecture() -> None:
    assets = _assets("UsageMonitorForCopilot.exe", "UsageMonitorForCopilot.exe.sha256")
    chosen = github.select_asset(assets, architecture="ARM64")
    assert chosen is not None and chosen["name"] == "UsageMonitorForCopilot.exe"


def test_without_an_architecture_hint_a_foreign_one_is_avoided() -> None:
    assets = _assets("Monitor-arm64.exe", "Monitor.exe")
    chosen = github.select_asset(assets, architecture="AMD64")
    assert chosen is not None and chosen["name"] == "Monitor.exe"

    chosen_arm = github.select_asset(_assets("Monitor-x64.exe", "Monitor.exe"), architecture="ARM64")
    assert chosen_arm is not None and chosen_arm["name"] == "Monitor.exe"


def test_an_unknown_architecture_falls_back_to_the_first_exe() -> None:
    chosen = github.select_asset(_assets("Monitor-x64.exe", "Monitor-arm64.exe"), architecture="IA64")
    assert chosen is not None and chosen["name"] == "Monitor-x64.exe"


def test_a_release_without_an_exe_has_no_asset() -> None:
    assert github.select_asset(_assets("source.zip", "notes.txt")) is None
    release = github.parse_release({"tag_name": "v1.0.0", "assets": _assets("source.zip")})
    assert release.asset is None and release.sidecar_sha_url is None


def test_select_asset_uses_the_machine_architecture_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROCESSOR_ARCHITECTURE", "arm64")
    chosen = github.select_asset(_assets("Monitor-x64.exe", "Monitor-arm64.exe"))
    assert chosen is not None and chosen["name"] == "Monitor-arm64.exe"


# --------------------------------------------------------------------------- #
# Token resolution
# --------------------------------------------------------------------------- #


def test_token_resolution_order(monkeypatch: pytest.MonkeyPatch) -> None:
    assert github.resolve_token() is None
    monkeypatch.setenv("GH_TOKEN", "from-gh")
    assert github.resolve_token() == "from-gh"
    monkeypatch.setenv("GITHUB_TOKEN", "from-github")
    assert github.resolve_token() == "from-github"
    assert github.resolve_token("explicit") == "explicit"
    monkeypatch.setenv("GITHUB_TOKEN", "   ")
    assert github.resolve_token() == "from-gh"


# --------------------------------------------------------------------------- #
# download
# --------------------------------------------------------------------------- #


def test_download_streams_and_reports_progress(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = b"x" * 200_000
    install(monkeypatch, Recorder(FakeResponse(payload, {"Content-Length": str(len(payload))})))
    asset = github.parse_release(
        {
            "tag_name": "v1",
            "assets": [{"name": "m.exe", "browser_download_url": "https://e/m.exe", "size": len(payload)}],
        }
    ).asset
    assert asset is not None

    seen: list[tuple[int, int]] = []
    dest = tmp_path / "sub" / "m.exe.download"
    github.download(asset, dest, lambda done, total: seen.append((done, total)))

    assert dest.read_bytes() == payload
    assert seen[0] == (0, len(payload))
    assert seen[-1] == (len(payload), len(payload))
    assert len(seen) > 2  # chunked, not one shot


def test_download_rejects_a_truncated_file_and_removes_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    install(monkeypatch, Recorder(FakeResponse(b"short")))
    asset = github.parse_release(
        {"tag_name": "v1", "assets": [{"name": "m.exe", "browser_download_url": "https://e/m.exe", "size": 999}]}
    ).asset
    assert asset is not None
    dest = tmp_path / "m.exe.download"

    with pytest.raises(GitHubError, match="download incomplete"):
        github.download(asset, dest)
    assert not dest.exists()


def test_download_failure_leaves_no_partial_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    install(monkeypatch, Recorder(http_error(502, {}, "Bad Gateway")))
    asset = github.parse_release(
        {"tag_name": "v1", "assets": [{"name": "m.exe", "browser_download_url": "https://e/m.exe", "size": 10}]}
    ).asset
    assert asset is not None
    dest = tmp_path / "m.exe.download"

    with pytest.raises(GitHubError, match="502"):
        github.download(asset, dest)
    assert not dest.exists()


def test_fetch_text_reads_a_sidecar(monkeypatch: pytest.MonkeyPatch) -> None:
    body = b"1f" * 32 + b"  UsageMonitorForCopilot.exe\n"
    recorder = install(monkeypatch, Recorder(FakeResponse(body)))
    assert github.fetch_text("https://e/m.exe.sha256").startswith("1f1f")
    assert recorder.last_headers["accept"] == "text/plain"


def test_fetch_text_maps_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, Recorder(urllib.error.URLError("no route")))
    with pytest.raises(GitHubError, match="cannot fetch"):
        github.fetch_text("https://e/m.exe.sha256")
