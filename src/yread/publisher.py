"""Publish a built yread site to a configured SSH target."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote

TARGET_RE = re.compile(
    r"^(?P<host>[A-Za-z0-9_.@-]+):(?P<root>/(?!/)[A-Za-z0-9_.-][A-Za-z0-9_./-]*)$"
)


def parse_target(value: str) -> tuple[str, str]:
    """Parse the intentionally small ``user@host:/absolute/path`` target syntax."""
    match = TARGET_RE.fullmatch(value.strip())
    if not match:
        raise SystemExit(
            "HUB_TARGET must look like user@host:/srv/yread "
            "(use an SSH config alias for custom ports)"
        )
    host, root = match.group("host"), match.group("root").rstrip("/")
    if ".." in Path(root).parts:
        raise SystemExit("HUB_TARGET path cannot contain '..'")
    return host, root


class _ProjectMetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.value: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "meta" and values.get("name") == "yread:project":
            self.value = values.get("content")


def _load_metadata(dist_dir: Path) -> dict:
    if not dist_dir.is_dir():
        raise SystemExit(f"built site not found: {dist_dir}; run yread build first")
    index = dist_dir / "index.html"
    if not index.is_file():
        raise SystemExit(f"built site under {dist_dir} has no index.html")
    parser = _ProjectMetaParser()
    parser.feed(index.read_text(encoding="utf-8", errors="replace"))
    try:
        meta = json.loads(parser.value or "")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"built site under {dist_dir} has no valid yread metadata") from exc
    if (
        not isinstance(meta, dict)
        or meta.get("schema_version") != 1
        or not isinstance(meta.get("project_id"), str)
        or not meta["project_id"].strip()
        or not isinstance(meta.get("pages"), int)
        or meta["pages"] < 0
    ):
        raise SystemExit(f"built site under {dist_dir} has no valid yread metadata")
    return meta


def _validate_dist(dist_dir: Path, meta: dict) -> None:
    if any(path.is_dir() for path in dist_dir.iterdir()):
        raise SystemExit(f"built site under {dist_dir} is not flat; run yread build again")
    files = list(dist_dir.iterdir())
    if any(path.suffix.lower() != ".html" for path in files):
        raise SystemExit(f"built site under {dist_dir} contains non-HTML files")
    page_count = sum(path.name != "index.html" for path in files)
    if page_count != meta["pages"]:
        raise SystemExit(
            f"built site under {dist_dir} is incomplete; "
            f"expected {meta['pages']} pages, found {page_count}"
        )


def publish_site(dist_dir: Path, target: str, *, run=subprocess.run) -> str:
    """Upload one built project and return its path relative to the Hub root."""
    dist_dir = dist_dir.resolve()
    meta = _load_metadata(dist_dir)
    _validate_dist(dist_dir, meta)
    host, root = parse_target(target)
    if not shutil.which("ssh") or not shutil.which("rsync"):
        raise SystemExit("publish requires ssh and rsync")

    # Encode each ID component into a shell-safe remote directory name. GitHub
    # owner/repo IDs stay readable; unusual local directory names remain safe.
    project_parts = meta["project_id"].split("/")
    if any(not part or part in {".", ".."} for part in project_parts):
        raise SystemExit(f"unsafe project_id: {meta['project_id']!r}")
    project_path = "/".join(quote(part, safe="-._~") for part in project_parts)
    remote_dir = f"{root}/projects/{project_path}"

    with tempfile.TemporaryDirectory(prefix="yread-publish-") as temp:
        staging = Path(temp) / "site"
        shutil.copytree(dist_dir, staging)
        (staging / "project.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        try:
            run(["ssh", host, f"mkdir -p -- {remote_dir}"], check=True)
            run(
                [
                    "rsync",
                    "--archive",
                    "--delete",
                    "--delay-updates",
                    "--chmod=D755,F644",
                    f"{staging}/",
                    f"{host}:{remote_dir}/",
                ],
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            raise SystemExit(f"publish failed with exit code {exc.returncode}") from exc
    return f"projects/{project_path}/"
