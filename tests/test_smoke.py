"""发布前验收脚本的纯函数：身份核对与 hash 覆盖范围。

``tools/smoke.py`` 会连真机（端口、注册表），所以这里只钉不碰外部状态的那部分：
build-info 与载荷文件是否一一对应、提交对不上时会不会红。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for entry in (REPO_ROOT, REPO_ROOT / "manager", REPO_ROOT / "tools"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import smoke  # noqa: E402


def _payload(tmp_path: Path) -> Path:
    (tmp_path / "tools").mkdir(parents=True, exist_ok=True)
    (tmp_path / "Flandora.exe").write_bytes(b"MZ-manager")
    (tmp_path / "tools" / "Flandora-KeyTool.exe").write_bytes(b"MZ-keytool")
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "config.json").write_text("{}", encoding="utf-8")
    return tmp_path


def _write_info(payload: Path, commit: str, files: dict[str, str], dirty: bool = False) -> None:
    (payload / "build-info.json").write_text(
        json.dumps(
            {"version": "1.0.0", "commit": commit, "dirty": dirty, "files": files},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_payload_files_skip_the_identity_file_and_writable_data(tmp_path):
    payload = _payload(tmp_path)
    (payload / "build-info.json").write_text("{}", encoding="utf-8")

    files = smoke.payload_files(payload)

    assert set(files) == {"Flandora.exe", "tools/Flandora-KeyTool.exe"}
    assert files["Flandora.exe"] == hashlib.sha256(b"MZ-manager").hexdigest()


def test_identity_passes_when_commit_and_hashes_match(tmp_path):
    payload = _payload(tmp_path)
    _write_info(payload, "c" * 40, smoke.payload_files(payload))

    assert smoke.identity_problems(payload, "c" * 40) == []


def test_identity_reports_a_foreign_commit_and_changed_file(tmp_path):
    payload = _payload(tmp_path)
    _write_info(payload, "c" * 40, smoke.payload_files(payload))
    (payload / "Flandora.exe").write_bytes(b"MZ-tampered")

    problems = smoke.identity_problems(payload, "d" * 40)

    assert any("与当前 HEAD" in problem for problem in problems)
    assert any("sha256 与记录不一致" in problem for problem in problems)


def test_identity_reports_a_missing_identity_file(tmp_path):
    payload = _payload(tmp_path)

    problems = smoke.identity_problems(payload, "c" * 40)

    assert problems and "build-info.json" in problems[0]


def test_identity_flags_a_dirty_build(tmp_path):
    payload = _payload(tmp_path)
    _write_info(payload, "c" * 40, smoke.payload_files(payload), dirty=True)

    problems = smoke.identity_problems(payload, "c" * 40)

    assert any("dirty" in problem for problem in problems)
