"""Verify that both release archives contain only intended project files."""

from pathlib import Path
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    """Inspect the wheel and sdist without importing optional dependencies."""
    wheels = list((ROOT / "dist").glob("*.whl"))
    sdists = list((ROOT / "dist").glob("*.tar.gz"))
    assert len(wheels) == len(sdists) == 1, "Expected one wheel and one sdist"
    expected = {
        path.relative_to(ROOT / "src").as_posix()
        for path in (ROOT / "src/mysa_client").rglob("*")
        if path.is_file() and (path.suffix == ".py" or path.name == "py.typed")
    }
    with zipfile.ZipFile(wheels[0]) as archive:
        names = set(archive.namelist())
        assert expected <= names, expected - names
        assert all(
            name.startswith("mysa_client/") or ".dist-info/" in name for name in names
        ), "Unexpected wheel content"
        assert any(name.endswith("/licenses/LICENSE") for name in names)
        for name in names:
            assert "__pycache__" not in name, name
            assert not name.endswith(".pyc"), name
    with tarfile.open(sdists[0]) as archive:
        names = {"/".join(Path(name).parts[1:]) for name in archive.getnames()}
        assert {f"src/{name}" for name in expected} <= names
        assert {"LICENSE", "README.md", "CHANGELOG.md", "pyproject.toml"} <= names
        allowed = {"src", "tests", "examples", "docs", "tools", ".github"}
        for name in names:
            if not name:
                continue
            path = Path(name)
            assert (
                len(path.parts) == 1
                and name
                in {
                    "LICENSE",
                    "README.md",
                    "CHANGELOG.md",
                    "pyproject.toml",
                    "PKG-INFO",
                    ".gitignore",
                }
            ) or path.parts[0] in allowed, name
            assert not {".env", ".git", "__pycache__", "research"} & set(path.parts), (
                name
            )
            assert path.suffix not in {".pyc", ".har", ".flow"}, name
    print(
        "Wheel and sdist contain runtime modules, typing marker, license, and intended files."
    )


if __name__ == "__main__":
    main()
