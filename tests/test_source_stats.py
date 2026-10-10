import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from yread import cli, core


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo with spaces 中文"
    root.mkdir()
    (root / "a.java").write_text("/* note */ int x = 1;\n", encoding="utf-8")
    (root / "b.java").write_text("/* note */ int x = 1;\n", encoding="utf-8")
    (root / "c.ts").write_text("const x = 1;\nconst y = 2;\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_a.py").write_text("assert True\n", encoding="utf-8")
    (root / "README.md").write_text("# Docs\n", encoding="utf-8")
    (root / "config.json").write_text("{}\n", encoding="utf-8")
    (root / "vendor").mkdir()
    (root / "vendor" / "ignored.py").write_text("x = 1\n", encoding="utf-8")
    (root / "ignored").mkdir()
    (root / "ignored" / "ignored.py").write_text("x = 1\n", encoding="utf-8")
    (root / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    (root / ".env.secret.py").write_text("SECRET = 1\n", encoding="utf-8")
    return root


@pytest.fixture
def fake_cloc(monkeypatch: pytest.MonkeyPatch) -> str:
    executable = "/tools/cloc"
    monkeypatch.setattr(core.shutil, "which", lambda name: executable if name == "cloc" else None)
    return executable


def report_for(repo: Path, *, absolute: bool = False) -> dict:
    counts = {"a.java": 1, "b.java": 1, "c.ts": 2, "tests/test_a.py": 1}
    return {
        "header": {"cloc_version": "test"},
        "SUM": {"code": 5},
        **{str(repo / name) if absolute else name: {"code": code, "language": "ignored label"}
           for name, code in counts.items()},
    }


def completed(report: object) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["cloc"], 0, json.dumps(report), "")


@pytest.mark.parametrize("absolute", [False, True])
def test_cloc_shared_counts_and_filtered_scope(
    repo: Path, fake_cloc: str, monkeypatch: pytest.MonkeyPatch, absolute: bool,
) -> None:
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return completed(report_for(repo, absolute=absolute))

    monkeypatch.setattr(core.subprocess, "run", run)
    result = core.source_stats(repo)
    assert result == {
        "backend": "cloc",
        "languages": [
            {"language": "Java", "files": 2, "loc": 2},
            {"language": "TypeScript", "files": 1, "loc": 2},
        ],
        "code": {"core_files": 3, "core_loc": 4, "avg_file_loc": 1,
                 "test_files": 1, "test_loc": 1, "test_ratio": 0.25},
    }
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv == [fake_cloc, f"--config={os.devnull}", "--json", "--by-file", "--quiet",
                    "--skip-uniqueness", "--list-file=-"]
    assert set(kwargs["input"].splitlines()) == {
        str(repo / name) for name in ("a.java", "b.java", "c.ts", "tests/test_a.py")
    }
    assert kwargs["cwd"] == repo.resolve()
    assert kwargs["check"] is True
    assert kwargs["timeout"] == 60
    assert kwargs["encoding"] == "utf-8"
    assert not kwargs.get("shell", False)
    assert sum(lang["loc"] for lang in result["languages"]) == result["code"]["core_loc"]


def test_missing_cloc_uses_legacy_counts(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core.shutil, "which", lambda _name: None)

    def unexpected_run(*args, **kwargs):
        pytest.fail("missing cloc must not spawn a process")

    monkeypatch.setattr(core.subprocess, "run", unexpected_run)
    result = core.source_stats(repo)
    assert result["backend"] == "builtin"
    # The legacy heuristic misses code following a leading block comment.
    assert result["code"]["core_loc"] == 2
    assert result["code"]["test_loc"] == 1
    assert result["code"]["core_files"] == 3
    assert sum(lang["loc"] for lang in result["languages"]) == 2


@pytest.mark.parametrize("error", [
    FileNotFoundError("cloc disappeared"),
    PermissionError("cannot execute"),
    subprocess.CalledProcessError(1, ["cloc"]),
    subprocess.TimeoutExpired(["cloc"], 60),
    UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid output"),
])
def test_cloc_failure_falls_back_for_whole_report(
    repo: Path, fake_cloc: str, monkeypatch: pytest.MonkeyPatch, error: Exception,
) -> None:
    def run(*args, **kwargs):
        raise error

    monkeypatch.setattr(core.subprocess, "run", run)
    result = core.source_stats(repo)
    assert result["backend"] == "builtin"
    assert result["code"]["core_loc"] == 2
    assert result["code"]["test_loc"] == 1


@pytest.mark.parametrize("invalid", [
    "not JSON", "", "[]", "null", "{}",
    '{"a.java": {"code": 99}}',  # partial output must not mix counters
    '{"outside.py": {"code": 1}}',
    '{"a.java": null}',
    '{"a.java": {"code": -1}}',
    '{"a.java": {"code": true}}',
    '{"a.java": {"code": "1"}}',
    '{"a.java": {"code": 1.5}}',
    '{"a.java": {"blank": 0}}',
])
def test_invalid_or_incomplete_cloc_report_falls_back(
    repo: Path, fake_cloc: str, monkeypatch: pytest.MonkeyPatch, invalid: str,
) -> None:
    monkeypatch.setattr(core.subprocess, "run",
                        lambda *args, **kwargs: subprocess.CompletedProcess(["cloc"], 0, invalid, ""))
    result = core.source_stats(repo)
    assert result["backend"] == "builtin"
    assert result["code"]["core_loc"] == 2
    assert result["code"]["core_files"] == 3


def test_empty_file_is_retained_when_cloc_omits_it(
    repo: Path, fake_cloc: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (repo / "empty.py").touch()
    monkeypatch.setattr(core.subprocess, "run", lambda *args, **kwargs: completed(report_for(repo)))
    result = core.source_stats(repo)
    assert result["backend"] == "cloc"
    assert result["code"]["core_files"] == 4
    assert {s["language"]: s for s in result["languages"]}["Python"] == {
        "language": "Python", "files": 1, "loc": 0,
    }


def test_no_sources_does_not_spawn_cloc(
    tmp_path: Path, fake_cloc: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_run(*args, **kwargs):
        pytest.fail("no source files must not spawn cloc")

    monkeypatch.setattr(core.subprocess, "run", unexpected_run)
    result = core.source_stats(tmp_path)
    assert result["languages"] == []
    assert result["code"] == {"core_files": 0, "core_loc": 0, "avg_file_loc": 0,
                              "test_files": 0, "test_loc": 0, "test_ratio": 0.0}


@pytest.mark.skipif(os.name == "nt", reason="Windows filenames cannot contain line breaks")
def test_filename_with_newline_falls_back(
    tmp_path: Path, fake_cloc: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "odd\nname.py").write_text("x = 1\n", encoding="utf-8")

    def unexpected_run(*args, **kwargs):
        pytest.fail("line-separated input cannot represent this filename")

    monkeypatch.setattr(core.subprocess, "run", unexpected_run)
    result = core.source_stats(tmp_path)
    assert result["backend"] == "builtin"
    assert result["code"]["core_loc"] == 1


@pytest.mark.parametrize("function,key", [(core.language_stats, "languages"), (core.code_stats, "code")])
def test_existing_public_helpers_keep_their_shapes(
    repo: Path, fake_cloc: str, monkeypatch: pytest.MonkeyPatch, function, key: str,
) -> None:
    monkeypatch.setattr(core.subprocess, "run", lambda *args, **kwargs: completed(report_for(repo)))
    assert function(repo) == core.source_stats(repo)[key]


@pytest.mark.parametrize("backend", ["cloc", "builtin"])
def test_profile_shares_one_counter_run_and_displays_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], backend: str,
) -> None:
    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")
    calls = []

    def source_stats(repo):
        calls.append(repo)
        return {"backend": backend, "languages": [{"language": "Python", "files": 1, "loc": 1}],
                "code": {"core_files": 1, "core_loc": 1, "avg_file_loc": 1,
                         "test_files": 0, "test_loc": 0, "test_ratio": 0.0}}

    monkeypatch.setattr(core, "source_stats", source_stats)
    monkeypatch.setattr(core, "git_stats", lambda _repo: {})
    monkeypatch.setattr(core, "github_repo_info", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "_read_config", lambda: {})
    assert cli.main(["profile", str(tmp_path)]) == 0
    assert calls == [tmp_path.resolve()]
    out = capsys.readouterr().out
    assert "Counter" in out
    assert ("cloc" if backend == "cloc" else "builtin heuristic (fallback)") in out


@pytest.mark.skipif(shutil.which("cloc") is None, reason="optional cloc executable not installed")
def test_real_cloc_counts_duplicate_and_mixed_comment_files(repo: Path) -> None:
    result = core.source_stats(repo)
    assert result["backend"] == "cloc"
    assert result["code"]["core_loc"] == 4
    assert result["code"]["core_files"] == 3
    assert result["code"]["test_loc"] == 1
