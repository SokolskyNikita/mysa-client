"""Regression checks for documentation navigation and source contracts."""

from __future__ import annotations

from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest


@pytest.fixture
def checker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    namespace = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "tools/check_docs.py")
    )
    # run_path returns a copy; functions retain their original global dictionary.
    scope = namespace["check_link"].__globals__
    monkeypatch.setitem(scope, "ROOT", tmp_path)
    scope["ERRORS"].clear()
    return SimpleNamespace(**scope)


def test_github_main_links_use_checkout_and_validate_anchors(checker, tmp_path):
    target = tmp_path / "docs/README.md"
    target.parent.mkdir()
    target.write_text("# Guide\n\n## Pair a device\n")
    url = checker.REPO_URL + "blob/main/docs/README.md#pair-a-device"
    assert checker.check_link(tmp_path / "README.md", url) == target
    assert not checker.ERRORS
    checker.check_link(tmp_path / "README.md", url.replace("pair-a-device", "missing"))
    assert "missing heading anchor" in checker.ERRORS[0]


def test_directory_links_resolve_readme_and_external_links_stay_external(
    checker, tmp_path
):
    target = tmp_path / "examples/README.md"
    target.parent.mkdir()
    target.write_text("# Examples\n")
    assert checker.check_link(tmp_path / "README.md", "examples/") == target
    assert (
        checker.check_link(tmp_path / "README.md", "https://example.com/missing")
        is None
    )
    assert not checker.ERRORS


def test_normalized_repository_link_cannot_escape(checker, tmp_path):
    assert (
        checker.check_link(
            tmp_path / "README.md", checker.REPO_URL + "blob/main/%2e%2e/outside.md"
        )
        is None
    )
    assert "escapes the repository" in checker.ERRORS[0]


def test_navigation_rejects_orphans_but_allows_compatibility_pages(checker, tmp_path):
    index = tmp_path / "docs/README.md"
    guide = tmp_path / "docs/guide.md"
    orphan = tmp_path / "docs/orphan.md"
    forwarder = tmp_path / "old.md"
    index.parent.mkdir()
    for page in (index, guide, orphan):
        page.write_text("# Page\n")
    forwarder.write_text("<!-- compatibility-forwarder -->\n# Moved\n")
    checker.check_navigation(
        {index: {guide}, guide: {index}, orphan: set(), forwarder: {guide}}
    )
    assert len(checker.ERRORS) == 1
    assert "orphan.md" in checker.ERRORS[0]


def test_api_contract_checks_members_without_importing_module(checker, tmp_path):
    module = tmp_path / "src/mysa_client/client.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        "raise RuntimeError('must not import')\nclass Client:\n    async def read(self): pass\n"
    )
    page = tmp_path / "README.md"
    checker.check_api_symbols(
        page, "<!-- api-symbol: mysa_client.client:Client.read -->"
    )
    checker.check_api_symbols(
        page, "Use `<!-- api-symbol: missing:Example -->` as syntax."
    )
    assert not checker.ERRORS
    checker.check_api_symbols(
        page, "<!-- api-symbol: mysa_client.client:Client.removed -->"
    )
    assert "missing API symbol" in checker.ERRORS[0]


def test_inventory_baseline_requires_release_review(checker, tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.2.1"\n')
    inventory = tmp_path / "docs/api-coverage.md"
    inventory.parent.mkdir()
    inventory.write_text("Client baseline: **mysa-client 0.1.2**\n")
    checker.check_inventory_baseline()
    assert "baseline must match" in checker.ERRORS[0]


def test_published_anchor_cannot_disappear(checker, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "published-anchors.json").write_text('{"old.md": ["public-anchor"]}')
    page = tmp_path / "old.md"
    page.write_text('# Moved\n<a id="public-anchor"></a>\n')
    checker.check_published_anchors()
    assert not checker.ERRORS
    page.write_text("# Moved\n")
    checker.check_published_anchors()
    assert "published anchor was removed" in checker.ERRORS[0]


def test_callable_index_follows_source_without_imports(tmp_path, monkeypatch):
    namespace = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "tools/update_api_reference.py")
    )
    scope = namespace["render"].__globals__
    monkeypatch.setitem(scope, "ROOT", tmp_path)
    monkeypatch.setitem(scope, "SECTIONS", {"Cloud": {"cloud/client.py": ("Client",)}})
    source = tmp_path / "src/mysa_client/cloud"
    source.mkdir(parents=True)
    (source / "__init__.py").write_text('__all__ = ["Client"]\n')
    client = source / "client.py"
    client.write_text(
        "raise RuntimeError('must not import')\n"
        "class Client:\n"
        "    async def read(self, limit: int = 1) -> str | None:\n"
        '        """Read a value | unknown."""\n'
        "    def _private(self): pass\n"
    )
    before = namespace["render"]()
    assert "Client.read" in before
    assert "limit: int=1" in before
    assert r"str \| None" in before
    assert "_private" not in before
    client.write_text(client.read_text().replace("limit: int = 1", "limit: int = 5"))
    after = namespace["render"]()
    assert before != after
    assert "limit: int=5" in after
