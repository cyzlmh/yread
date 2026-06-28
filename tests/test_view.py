from pathlib import Path

from yread import viewer as view


def test_safe_source_path_stays_inside_repo_and_blocks_sensitive_files(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('ok')\n")
    (tmp_path / ".env").write_text("SECRET=1\n")

    assert view.safe_source_path(tmp_path, "src/main.py") == (tmp_path / "src" / "main.py")
    assert view.safe_source_path(tmp_path, ".env") is None
    assert view.safe_source_path(tmp_path, "../outside.txt") is None
