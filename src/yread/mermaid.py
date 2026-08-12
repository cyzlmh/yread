"""Deterministic post-generation repair of mermaid diagrams.

LLM-generated mermaid frequently leaves node / edge / subgraph labels unquoted
even when they contain characters the mermaid lexer treats as syntax — ``@``,
``{``, ``}``, ``|``, ``(``, ``)``, a label that opens with ``[/`` (read as a
parallelogram), or a fullwidth colon in a subgraph title. Such diagrams fail to
render. This module rewrites just those labels into the quoted form, which is
always valid. It is pure-stdlib, idempotent, and runs on every generated page
before the file is written (see :func:`yread.core.write_one_page`).

The unsafe character set and the safety of the quoted fix were both verified
empirically against ``@mermaid-js/mermaid-cli``: every quoted form this module
emits renders, so the transform can never turn a working diagram into a broken
one.
"""

from __future__ import annotations

import re

# Characters that break the lexer inside an UNQUOTED node or edge label. Probed
# one at a time through mermaid-cli. Safe characters common in these diagrams
# (``/`` ``:`` ``.`` ``+`` ``&`` ``<`` ``>`` ``-`` …) are deliberately absent so
# labels that already render are never touched.
_LABEL_UNSAFE = frozenset("@{}|()")

# Subgraph titles are a separate lexer context: a fullwidth ``：`` breaks them
# (an ASCII ``:`` does not), as do ``(`` ``)`` ``{`` ``}``. ``@`` and ``|`` are
# included defensively — quoting is always safe, so we lean toward fixing
# rather than missing a broken title.
_SUB_UNSAFE = frozenset("@{}|()：")

# Node label shapes, longest opener first so ``[(`` / ``((`` / ``{{`` / ``[[`` /
# ``[/`` win over the single-char ``[`` / ``{``. The asymmetric ``>`` shape is
# intentionally omitted: its opener collides with arrow syntax (``-->``) and no
# diagram in the corpus relied on it.
_SHAPES = (
    ("[(", ")]"),
    ("((", "))"),
    ("{{", "}}"),
    ("[[", "]]"),
    ("[/", "/]"),
    ("[", "]"),
    ("{", "}"),
)

_FENCE_RE = re.compile(r"```mermaid[ \t]*\r?\n(.*?)```", re.DOTALL)


def _wrap(content: str) -> tuple[str, bool]:
    """Wrap label ``content`` in double quotes if it carries an unsafe char.

    Returns ``(text, did_wrap)``. Already-quoted content and content that itself
    contains a double quote (too rare to risk re-quoting) pass through unchanged
    — this is what makes the transform idempotent.
    """
    if not content or content[0] == '"' or '"' in content:
        return content, False
    if any(c in _LABEL_UNSAFE for c in content):
        return '"' + content + '"', True
    return content, False


def _find_label_end(line: str, after: int, closer: str) -> tuple[int, bool] | None:
    """Locate the end of a label whose opener finished at ``after``.

    Returns ``(end, is_quoted)`` with ``end`` pointing just past the closer, or
    ``None`` when no closer is present on this line. Quoted labels (content
    starting with ``"``) are located via their closing quote so a ``]`` inside
    the string does not terminate the label early.
    """
    if after >= len(line):
        return None
    if line[after] == '"':
        q = line.find('"', after + 1)
        if q == -1:
            return None
        end = q + 1
        if line.startswith(closer, end):
            return end + len(closer), True
        return None
    cidx = line.find(closer, after)
    if cidx == -1:
        return None
    return cidx + len(closer), False


def _rewrite_line(line: str) -> tuple[str, int]:
    """Rewrite labels on a single mermaid line. Returns ``(new_line, wraps)."""
    # Full-line comments and ``%%{init: ...}`` directives carry braces/quotes we
    # must not interpret as labels.
    if line.lstrip().startswith("%%"):
        return line, 0

    # Subgraph title: a bare (unquoted, non-bracket) title with an unsafe char is
    # wrapped wholesale. Bracket-form titles (``subgraph id [title]``) fall
    # through to the shape scanner, which handles the bracket itself.
    m = re.match(r"^(\s*subgraph\s+)(.+)$", line)
    if m:
        head, title = m.group(1), m.group(2)
        if title and title[0] != '"' and "[" not in title and any(c in _SUB_UNSAFE for c in title):
            return head + '"' + title + '"', 1

    out: list[str] = []
    wraps = 0
    i = 0
    n = len(line)
    while i < n:
        # 1) Node label shapes (longest opener first).
        matched = False
        for opener, closer in _SHAPES:
            if not line.startswith(opener, i):
                continue
            end_info = _find_label_end(line, i + len(opener), closer)
            if end_info is None:
                # ``[/`` with no matching ``/]`` is the common "path inside a
                # rectangle" mistake: the leading ``/`` is read as a
                # parallelogram opener. Recover it as a quoted rectangle label
                # so the ``[`` is a plain rectangle opener instead.
                if opener == "[/":
                    cidx = line.find("]", i + 1)
                    if cidx != -1:
                        content = line[i + 1:cidx]
                        if content and content[0] != '"' and '"' not in content:
                            out.append('["' + content + '"]')
                            wraps += 1
                        else:
                            out.append(line[i:cidx + 1])
                        i = cidx + 1
                        matched = True
                        break
                out.append(opener)
                i += len(opener)
                matched = True
                break
            end, is_quoted = end_info
            if is_quoted:
                out.append(line[i:end])  # already quoted → leave untouched
            else:
                content = line[i + len(opener):end - len(closer)]
                wrapped, did = _wrap(content)
                out.append(opener + wrapped + closer)
                wraps += int(did)
            i = end
            matched = True
            break
        if matched:
            continue

        ch = line[i]
        # 2) Edge label ``|...|``. Content between two pipes cannot itself
        # contain a pipe, so a plain find is correct; already-quoted content is
        # left alone by _wrap.
        if ch == "|":
            nidx = line.find("|", i + 1)
            if nidx != -1:
                content = line[i + 1:nidx]
                wrapped, did = _wrap(content)
                out.append("|" + wrapped + "|")
                wraps += int(did)
                i = nidx + 1
                continue
        # 3) A free-standing double quote (e.g. a ``-- "desc" -->`` arrow label,
        # absent in the corpus but cheap to defend against): copy the whole
        # quoted span verbatim so brackets inside it are not mistaken for labels.
        elif ch == '"':
            q = line.find('"', i + 1)
            if q != -1:
                out.append(line[i:q + 1])
                i = q + 1
                continue
        out.append(ch)
        i += 1
    return "".join(out), wraps


def _is_flowchart(block: str) -> bool:
    """True when the block is a ``graph`` / ``flowchart`` diagram — the only
    diagram types whose node/edge label syntax (``[…]``, ``{…}``, ``|…|``) we
    rewrite. Sequence / state / class / ER diagrams use braces and brackets as
    literal text in messages and labels, so touching them would corrupt working
    diagrams."""
    for line in block.splitlines():
        s = line.strip()
        if not s or s.startswith("%%"):
            continue
        return s.startswith(("graph ", "graph\t", "flowchart ", "flowchart\t")) or s in ("graph", "flowchart")
    return False


def sanitize_mermaid(markdown: str) -> tuple[str, int]:
    """Rewrite unquoted mermaid labels that contain lexer-breaking characters
    into their quoted form.

    Only ``graph`` / ``flowchart`` fenced code blocks are touched; prose, other
    code blocks, and other diagram types (sequence, state, class, …) pass through
    verbatim. Returns ``(new_markdown, wraps)`` where ``wraps`` is the number of
    labels quoted (for a log line). The transform is idempotent: running it twice
    applies zero changes the second time.
    """
    total = 0

    def fix_block(block: str) -> str:
        nonlocal total
        if not _is_flowchart(block):
            return block
        lines = block.split("\n")
        for idx, line in enumerate(lines):
            new, wraps = _rewrite_line(line)
            if wraps:
                lines[idx] = new
                total += wraps
        return "\n".join(lines)

    new_md = _FENCE_RE.sub(
        lambda m: "```mermaid\n" + fix_block(m.group(1)) + "```", markdown)
    return new_md, total
