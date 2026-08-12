"""Tests for the deterministic mermaid label repair (yread.mermaid).

The unsafe character set and the safety of the quoted fix were verified against
``@mermaid-js/mermaid-cli``; these tests pin the rewriting behavior so the
in-flow sanitizer in ``core.write_one_page`` keeps doing the right thing.
"""

from yread.mermaid import sanitize_mermaid


def run(md: str) -> tuple[str, int]:
    return sanitize_mermaid(md)


def _block(body: str) -> str:
    return "```mermaid\n" + body + "```\n"


# --------------------------------------------------------------------------- #
# The five confirmed failure classes (all from real gateway-docs wiki pages). #
# --------------------------------------------------------------------------- #

def test_at_in_node_label_gets_quoted():
    md = _block("graph TD\n  AP[x<br/>基线] --> WLP[WhiteListProperties<br/>@ConfigurationProperties<br/>prefix=whitelist]\n")
    out, n = run(md)
    assert n == 1
    assert 'WLP["WhiteListProperties<br/>@ConfigurationProperties<br/>prefix=whitelist"]' in out
    assert "AP[x<br/>基线]" in out  # safe label untouched


def test_braces_in_node_label_gets_quoted():
    md = _block("graph TD\n  Controller[SemanticsController<br/>/semantics/{version}/analysis] --> B\n")
    out, n = run(md)
    assert n == 1
    assert 'Controller["SemanticsController<br/>/semantics/{version}/analysis"]' in out


def test_pipe_in_node_label_gets_quoted():
    md = _block("flowchart TD\n  E --> F[拼接: IV || 密文+Tag]\n")
    out, n = run(md)
    assert n == 1
    assert 'F["拼接: IV || 密文+Tag"]' in out


def test_parens_in_node_label_gets_quoted():
    # Discovered by the char matrix: ( ) break unquoted [..] labels even though
    # the original 9 failures did not surface it.
    md = _block("graph TD\n  A[foo (bar)] --> B\n")
    out, n = run(md)
    assert n == 1
    assert 'A["foo (bar)"]' in out


def test_leading_slash_path_mistyped_as_parallelogram():
    # BIN[/opt/...] is read as a parallelogram opener; recover as a quoted
    # rectangle so the leading / is literal text.
    md = _block("flowchart LR\n  BIN[/opt/skillscan/current<br/>assets/bin/skillscan] --> C\n")
    out, n = run(md)
    assert n == 1
    assert 'BIN["/opt/skillscan/current<br/>assets/bin/skillscan"]' in out


def test_at_in_edge_label_gets_quoted():
    md = _block("flowchart LR\n  AOP[LoginAspectJ AOP] -.->|@AfterReturning| KF\n")
    out, n = run(md)
    assert n == 1
    # The node label "LoginAspectJ AOP" is safe and stays; only the edge wraps.
    assert "AOP[LoginAspectJ AOP]" in out
    assert '-.->|"@AfterReturning"|' in out


def test_fullwidth_colon_in_subgraph_title_gets_quoted():
    md = _block("graph TB\n  subgraph 扩展点一：模型适配器\n    A --> B\n  end\n")
    out, n = run(md)
    assert n == 1
    assert 'subgraph "扩展点一：模型适配器"' in out


def test_parens_in_subgraph_title_gets_quoted():
    md = _block("graph TB\n  subgraph Layer (One)\n    A --> B\n  end\n")
    out, n = run(md)
    assert n == 1
    assert 'subgraph "Layer (One)"' in out


# --------------------------------------------------------------------------- #
# Shapes other than the plain rectangle: the wrap is valid in each.            #
# --------------------------------------------------------------------------- #

def test_cylinder_label_unsafe_char_quoted():
    md = _block("graph TD\n  DB[(MySQL<br/>@creds)] --> B\n")
    out, n = run(md)
    assert n == 1
    assert 'DB[("MySQL<br/>@creds")]' in out


def test_diamond_label_unsafe_char_quoted():
    md = _block("graph TD\n  D{@check} --> B\n")
    out, n = run(md)
    assert n == 1
    assert 'D{"@check"}' in out


def test_cylinder_delimiters_not_treated_as_content():
    # [(...)] parens are shape delimiters, not content — a clean cylinder must
    # not be touched just because it contains parens.
    md = _block("graph TD\n  DB[(MySQL data)] --> B\n")
    out, n = run(md)
    assert n == 0
    assert "DB[(MySQL data)]" in out


# --------------------------------------------------------------------------- #
# Already-quoted labels, idempotency, and leaving things alone.                #
# --------------------------------------------------------------------------- #

def test_already_quoted_label_untouched():
    md = _block('graph TD\n  A["@ConfigurationProperties"] --> B\n')
    out, n = run(md)
    assert n == 0
    assert out == md


def test_quoted_edge_label_untouched():
    md = _block('flowchart LR\n  A -->|"提供样本"| B\n')
    out, n = run(md)
    assert n == 0
    assert out == md


def test_idempotent():
    md = _block(
        "graph TD\n"
        "  WLP[WhiteListProperties<br/>@ConfigurationProperties<br/>prefix=whitelist]\n"
        "  F[拼接: IV || 密文+Tag]\n"
        "  subgraph 扩展点一：模型适配器\n    A --> B\n  end\n"
    )
    once, n1 = run(md)
    twice, n2 = run(once)
    assert n1 > 0
    assert n2 == 0
    assert once == twice


def test_safe_labels_untouched():
    # / : . + & < > are all safe unquoted — must not be wrapped.
    md = _block("graph TD\n  A[POST /api/v1/users: list] --> B[a.b.c + d & e <f>]\n")
    out, n = run(md)
    assert n == 0
    assert out == md


def test_non_mermaid_code_block_untouched():
    md = "```bash\n  A[@ConfigurationProperties {x} |y|]\n```\n"
    out, n = run(md)
    assert n == 0
    assert out == md


def test_prose_untouched():
    md = "Some text with @mention and {brace} and |pipe| inline.\n"
    out, n = run(md)
    assert n == 0
    assert out == md


def test_sequence_diagram_untouched():
    # Sequence diagrams use { } [ ] as literal message text and were never
    # broken; the sanitizer must scope itself to graph/flowchart only.
    md = _block(
        "sequenceDiagram\n"
        "  participant API\n"
        "  API-->>SDK: { code: '1', data: { ssoToken: 'xxx' } }\n"
        "  Pipeline-->>Client: risks: [{category, score}, ...]\n"
    )
    out, n = run(md)
    assert n == 0
    assert out == md


def test_fullwidth_colon_safe_in_node_label():
    # A fullwidth colon breaks subgraph titles but is safe inside a node label.
    md = _block("graph TD\n  A[扩展：适配器] --> B\n")
    out, n = run(md)
    assert n == 0
    assert out == md


def test_returns_wrap_count_across_blocks():
    md = _block("graph TD\n  A[@x] --> B\n") + _block("graph TD\n  C[@y] --> D\n")
    _, n = run(md)
    assert n == 2
