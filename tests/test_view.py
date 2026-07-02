from pathlib import Path

from yread import viewer as view


def test_resolve_wiki_defaults_to_yread_output_root(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / ".yread"
    (root / "wiki").mkdir(parents=True)
    (root / "wiki.json").write_text('{"schema_version": 2, "pages": []}\n')
    monkeypatch.chdir(tmp_path)

    assert view.resolve_wiki(None) == root


def test_safe_source_path_stays_inside_repo_and_blocks_sensitive_files(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('ok')\n")
    (tmp_path / ".env").write_text("SECRET=1\n")

    assert view.safe_source_path(tmp_path, "src/main.py") == (tmp_path / "src" / "main.py")
    assert view.safe_source_path(tmp_path, ".env") is None
    assert view.safe_source_path(tmp_path, "../outside.txt") is None
