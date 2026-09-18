"""Tests for the manager's own update (PLAN.md section 6.5).

Nothing real happens here: the "manager executable" is a file in ``tmp_path``,
the download is a fake ``github.download`` that writes bytes, and the relaunch
is a fake spawner that records ``argv`` and the environment instead of starting
a process. The one thing that must be proven is the *order*: swap first,
relaunch second, exit third - and never on a source checkout.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from usage_monitor_manager import __version__, github, self_update
from usage_monitor_manager.integrity import sha256_file
from usage_monitor_manager.model import ReleaseAsset, ReleaseInfo
from usage_monitor_manager.self_update import (
    MANAGER_REPO,
    POST_UPDATE_FLAG,
    SelfUpdateCheck,
    apply_self,
    check_self,
    describe_self,
    finish_post_update,
)
from usage_monitor_manager.versions import VersionStatus

NEW_BYTES = b"the new manager executable"


def _release(tag: str, version: str, *, digest: str | None = None, sidecar: str | None = None) -> ReleaseInfo:
    return ReleaseInfo(
        tag=tag,
        version=version,
        published_at=datetime(2026, 10, 1, tzinfo=UTC),
        html_url=f"https://github.com/{MANAGER_REPO}/releases/tag/{tag}",
        body_markdown="## Novita'\n\n- self-update",
        asset=ReleaseAsset(
            name="UsageMonitorManager.exe",
            url="https://example.invalid/UsageMonitorManager.exe",
            size=len(NEW_BYTES),
            digest=digest,
        ),
        sidecar_sha_url=sidecar,
    )


# --------------------------------------------------------------------------- #
# check_self
# --------------------------------------------------------------------------- #


def test_no_release_is_unknown_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """The repo has published nothing yet: that is a state, not a failure."""

    def fetch(repo: str, **kwargs: object) -> ReleaseInfo:
        raise github.GitHubError(f"{repo} has published no release yet")

    monkeypatch.setattr(github, "fetch_latest_release", fetch)
    state = check_self()
    assert state.status is VersionStatus.UNKNOWN
    assert state.available is False
    assert state.error is None  # nothing went wrong
    assert "nessuna release pubblicata" in state.detail
    assert state.to_dict()["repo"] == MANAGER_REPO


def test_a_network_failure_keeps_the_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        github,
        "fetch_latest_release",
        lambda repo, **kwargs: (_ for _ in ()).throw(github.GitHubError("cannot reach GitHub (dns)")),
    )
    state = check_self()
    assert state.status is VersionStatus.UNKNOWN
    assert state.error == "cannot reach GitHub (dns)"


def test_a_rate_limit_carries_its_own_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        github,
        "fetch_latest_release",
        lambda repo, **kwargs: (_ for _ in ()).throw(github.RateLimitedError("limit exhausted, resets at 13:00", None)),
    )
    state = check_self()
    assert state.status is VersionStatus.UNKNOWN
    assert "resets at 13:00" in state.detail


def test_a_newer_release_is_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github, "fetch_latest_release", lambda repo, **kwargs: _release("v9.9.9", "9.9.9"))
    state = check_self()
    assert state.status is VersionStatus.UPDATE_AVAILABLE
    assert state.available is True
    assert state.target_version == "9.9.9"
    assert state.tag == "v9.9.9"
    assert __version__ in state.detail


def test_the_same_version_is_up_to_date(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github, "fetch_latest_release", lambda repo, **kwargs: _release(f"v{__version__}", __version__))
    state = check_self()
    assert state.status is VersionStatus.UP_TO_DATE
    assert state.available is False


def test_a_skipped_tag_is_not_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github, "fetch_latest_release", lambda repo, **kwargs: _release("v9.9.9", "9.9.9"))
    state = check_self(skipped_tag="v9.9.9")
    assert state.status is VersionStatus.UPDATE_AVAILABLE
    assert state.skipped is True
    assert state.available is False  # the badge and the menu stay quiet


# --------------------------------------------------------------------------- #
# apply_self
# --------------------------------------------------------------------------- #


@pytest.fixture
def manager_exe(tmp_path: Path) -> Path:
    path = tmp_path / "UsageMonitorManager.exe"
    path.write_bytes(b"the old manager executable")
    return path


@pytest.fixture
def fake_download(monkeypatch: pytest.MonkeyPatch) -> None:
    def download(asset: ReleaseAsset, dest: Path, on_progress: object = None, **kwargs: object) -> Path:
        dest.write_bytes(NEW_BYTES)
        return dest

    monkeypatch.setattr(github, "download", download)


class Spawned:
    """Records the relaunch instead of performing it."""

    def __init__(self) -> None:
        self.argv: list[str] = []
        self.env: dict[str, str] = {}
        self.cwd: Path | None = None

    def __call__(self, argv: list[str], env: dict[str, str], cwd: Path) -> int:
        self.argv = list(argv)
        self.env = dict(env)
        self.cwd = cwd
        return 4242


def _digest_of(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()


def test_source_runs_refuse_to_self_update(manager_exe: Path) -> None:
    result = apply_self(frozen=False, exe_path=manager_exe)
    assert result.ok is False
    assert "da sorgente" in result.detail
    assert manager_exe.read_bytes() == b"the old manager executable"


def test_apply_self_swaps_relaunches_and_exits(manager_exe: Path, fake_download: None) -> None:
    spawn = Spawned()
    exited: list[str] = []
    check = SelfUpdateCheck(
        status=VersionStatus.UPDATE_AVAILABLE,
        current_version=__version__,
        detail="disponibile",
        release=_release("v9.9.9", "9.9.9", digest=f"sha256:{_digest_of(NEW_BYTES)}"),
    )

    result = apply_self(
        check=check,
        frozen=True,
        exe_path=manager_exe,
        spawn=spawn,
        on_exit=lambda: exited.append("quit"),
    )

    assert result.ok is True, result.detail
    assert result.exiting is True
    assert result.new_pid == 4242
    assert result.to_version == "9.9.9"

    # the swap happened, and the previous manager is still there as .bak
    assert manager_exe.read_bytes() == NEW_BYTES
    assert manager_exe.with_name(f"{manager_exe.name}.bak").read_bytes() == b"the old manager executable"
    assert not manager_exe.with_name(f"{manager_exe.name}.download").exists()

    # the successor is launched from the new executable with our own pid
    assert spawn.argv == [str(manager_exe), POST_UPDATE_FLAG, str(os.getpid())]
    assert spawn.cwd == manager_exe.parent
    assert exited == ["quit"]

    steps = [event.step for event in result.events]
    assert steps.index("backup") < steps.index("swap") < steps.index("start")


def test_the_relaunch_environment_is_scrubbed(
    manager_exe: Path, fake_download: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A one-file child inheriting ``_MEIPASS2`` would load the old bundle."""
    monkeypatch.setenv("_MEIPASS2", r"C:\Temp\_MEI123")
    monkeypatch.setenv("_PYI_ARCHIVE_FILE", r"C:\Temp\old.exe")
    monkeypatch.setenv("PATH_KEPT", "yes")
    spawn = Spawned()
    apply_self(
        check=SelfUpdateCheck(
            status=VersionStatus.UPDATE_AVAILABLE,
            current_version=__version__,
            detail="disponibile",
            release=_release("v9.9.9", "9.9.9", digest=_digest_of(NEW_BYTES)),
        ),
        frozen=True,
        exe_path=manager_exe,
        spawn=spawn,
    )
    assert "_MEIPASS2" not in spawn.env
    assert "_PYI_ARCHIVE_FILE" not in spawn.env
    assert spawn.env["PATH_KEPT"] == "yes"


def test_a_digest_mismatch_touches_nothing(manager_exe: Path, fake_download: None) -> None:
    spawn = Spawned()
    result = apply_self(
        check=SelfUpdateCheck(
            status=VersionStatus.UPDATE_AVAILABLE,
            current_version=__version__,
            detail="disponibile",
            release=_release("v9.9.9", "9.9.9", digest="0" * 64),
        ),
        frozen=True,
        exe_path=manager_exe,
        spawn=spawn,
    )
    assert result.ok is False
    assert "sha256 diverso" in result.detail
    assert manager_exe.read_bytes() == b"the old manager executable"
    assert not manager_exe.with_name(f"{manager_exe.name}.download").exists()
    assert spawn.argv == []


def test_an_unverifiable_asset_needs_an_explicit_yes(manager_exe: Path, fake_download: None) -> None:
    check = SelfUpdateCheck(
        status=VersionStatus.UPDATE_AVAILABLE,
        current_version=__version__,
        detail="disponibile",
        release=_release("v9.9.9", "9.9.9"),  # no digest, no sidecar
    )
    refused = apply_self(check=check, frozen=True, exe_path=manager_exe, spawn=Spawned())
    assert refused.ok is False
    assert "non pubblica uno sha256" in refused.detail
    assert manager_exe.read_bytes() == b"the old manager executable"

    spawn = Spawned()
    accepted = apply_self(
        check=check,
        frozen=True,
        exe_path=manager_exe,
        spawn=spawn,
        confirm_unverified=lambda: True,
    )
    assert accepted.ok is True
    assert manager_exe.read_bytes() == NEW_BYTES


def test_nothing_to_apply_when_there_is_no_release(manager_exe: Path) -> None:
    check = SelfUpdateCheck(
        status=VersionStatus.UNKNOWN,
        current_version=__version__,
        detail=f"nessuna release pubblicata per {MANAGER_REPO}",
    )
    result = apply_self(check=check, frozen=True, exe_path=manager_exe, spawn=Spawned())
    assert result.ok is False
    assert "nessuna release pubblicata" in result.detail


def test_the_sidecar_is_used_when_the_api_has_no_digest(
    manager_exe: Path, fake_download: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(github, "fetch_text", lambda url, **kwargs: f"{_digest_of(NEW_BYTES)}  UsageMonitorManager.exe")
    result = apply_self(
        check=SelfUpdateCheck(
            status=VersionStatus.UPDATE_AVAILABLE,
            current_version=__version__,
            detail="disponibile",
            release=_release("v9.9.9", "9.9.9", sidecar="https://example.invalid/UsageMonitorManager.exe.sha256"),
        ),
        frozen=True,
        exe_path=manager_exe,
        spawn=Spawned(),
    )
    assert result.ok is True
    assert result.integrity is not None and result.integrity.source == "sidecar"
    assert sha256_file(manager_exe) == _digest_of(NEW_BYTES)


# --------------------------------------------------------------------------- #
# --post-update
# --------------------------------------------------------------------------- #


def test_post_update_waits_then_removes_the_backup(manager_exe: Path) -> None:
    backup = manager_exe.with_name(f"{manager_exe.name}.bak")
    backup.write_bytes(b"previous")
    alive = {"left": 3}
    ticks = iter(float(index) for index in range(100))

    def is_alive(pid: int) -> bool:
        if alive["left"] <= 0:
            return False
        alive["left"] -= 1
        return True

    outcome = finish_post_update(
        1234,
        exe_path=manager_exe,
        is_alive=is_alive,
        sleep=lambda seconds: None,
        monotonic=lambda: next(ticks),
    )
    assert outcome.ok is True
    assert alive["left"] == 0  # it really waited for the pid to go
    assert outcome.backup_removed is True
    assert not backup.exists()
    assert outcome.message == f"Manager aggiornato alla v{__version__}"


def test_post_update_gives_up_after_the_timeout(manager_exe: Path) -> None:
    ticks = iter([0.0, 10.0, 20.0, 40.0, 60.0])
    outcome = finish_post_update(
        1234,
        timeout=30.0,
        exe_path=manager_exe,
        is_alive=lambda pid: True,
        sleep=lambda seconds: None,
        monotonic=lambda: next(ticks),
    )
    assert outcome.ok is False
    assert "ancora vivo" in outcome.detail


def test_post_update_without_a_backup_is_fine(manager_exe: Path) -> None:
    outcome = finish_post_update(0, exe_path=manager_exe, is_alive=lambda pid: False, sleep=lambda seconds: None)
    assert outcome.ok is True
    assert outcome.backup_removed is False


# --------------------------------------------------------------------------- #
# Dry run
# --------------------------------------------------------------------------- #


def test_describe_self_on_a_source_checkout() -> None:
    lines = describe_self(SelfUpdateCheck(status=VersionStatus.UPDATE_AVAILABLE, current_version="0.1.0", detail="x"))
    assert len(lines) == 1
    assert "build congelata" in lines[0]


def test_describe_self_lists_every_step(monkeypatch: pytest.MonkeyPatch, manager_exe: Path) -> None:
    monkeypatch.setattr(self_update, "is_frozen", lambda: True)
    lines = describe_self(
        SelfUpdateCheck(
            status=VersionStatus.UPDATE_AVAILABLE,
            current_version=__version__,
            detail="disponibile",
            release=_release("v9.9.9", "9.9.9", digest="a" * 64),
        ),
        exe_path=manager_exe,
    )
    joined = "\n".join(lines)
    assert "scaricherebbe" in joined
    assert "sha256" in joined
    assert POST_UPDATE_FLAG in joined
    assert manager_exe.read_bytes() == b"the old manager executable"  # a dry run changes nothing
