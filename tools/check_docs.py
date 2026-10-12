"""Validate Markdown, protocol examples, and runnable example syntax offline."""

from __future__ import annotations

import ast
from collections import Counter
import json
from pathlib import Path
import re
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []
LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
SECRET = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    r"|\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"
    r"|\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
    r"|X-Amz-Signature=[0-9a-fA-F]{32,}"
)
KINDS = {"live-sanitized", "app-derived", "constructed"}
REPRESENTATIONS = {"complete", "projection", "fixture", "unknown"}


def fail(path: Path, message: str) -> None:
    """Record a concise diagnostic without including suspicious values."""
    ERRORS.append(f"{path.relative_to(ROOT)}: {message}")


def unique_pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    """Reject repeated JSON keys, which json.loads otherwise silently accepts."""
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def reject_constant(_value: str) -> None:
    """Reject JavaScript-style NaN and infinity values in JSON."""
    raise ValueError("non-finite number")


def read_json(path: Path) -> object:
    """Parse a strict JSON file, rejecting duplicate keys and non-finite values."""
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=unique_pairs,
        parse_constant=reject_constant,
    )


def prose(text: str) -> str:
    """Remove fenced code examples before interpreting Markdown structure."""
    return re.sub(r"^```[^\n]*\n.*?^```\s*$", "", text, flags=re.M | re.S)


def anchors(text: str) -> set[str]:
    """Collect explicit IDs and GitHub-style heading slugs."""
    text = prose(text)
    found = set(re.findall(r'<a\s+(?:id|name)="([^"]+)"', text))
    counts: Counter[str] = Counter()
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", text, re.M):
        title = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", heading)
        slug = re.sub(r"[^\w\- ]", "", title.lower()).replace(" ", "-")
        count = counts[slug]
        counts[slug] += 1
        found.add(f"{slug}-{count}" if count else slug)
    return found


def check_link(source: Path, raw_url: str) -> Path | None:
    """Resolve local Markdown links while preventing repository escapes."""
    url = raw_url.strip("<>")
    if re.match(r"(?:[a-z][a-z0-9+.-]*:|//)", url, re.I):
        return None
    path_text, _, fragment = unquote(url).partition("#")
    target = (source.parent / path_text).resolve() if path_text else source
    if not target.is_relative_to(ROOT):
        fail(source, "local link escapes the repository")
        return None
    if not target.exists():
        fail(source, f"missing target: {url}")
        return None
    if (
        fragment
        and target.suffix == ".md"
        and fragment not in anchors(target.read_text(encoding="utf-8"))
    ):
        fail(source, f"missing heading anchor: {url}")
    if fragment and re.fullmatch(r"L\d+(?:-L\d+)?", fragment):
        line = int(fragment.split("-L")[-1].lstrip("L"))
        if line > len(target.read_text(encoding="utf-8").splitlines()):
            fail(source, f"source line anchor exceeds file length: {url}")
    return target


def check_markdown(path: Path) -> set[Path]:
    """Check fences, local links, heading anchors, and Markdown table widths."""
    text = path.read_text(encoding="utf-8")
    if len(re.findall(r"^```", text, re.M)) % 2:
        fail(path, "unbalanced code fence")
    targets: set[Path] = set()
    visible = prose(text)
    for url in LINK.findall(visible):
        target = check_link(path, url)
        if target is not None:
            targets.add(target)
    width: int | None = None
    for line in visible.splitlines():
        if not line.startswith("|"):
            width = None
            continue
        cells = len(re.findall(r"(?<!\\)\|", line))
        if width is not None and width != cells:
            fail(path, "inconsistent Markdown table width")
        width = cells
    if SECRET.search(text):
        fail(path, "possible credential material (value suppressed)")
    return targets


def check_body(path: Path, value: dict[str, object]) -> None:
    """Validate a request/response body or its local fixture reference."""
    if "body" in value and "bodyFile" in value:
        fail(path, "body and bodyFile are mutually exclusive")
    body_file = value.get("bodyFile")
    if body_file is None:
        return
    target = check_link(path, str(body_file))
    if target is not None and target.is_file():
        try:
            read_json(target)
        except (ValueError, OSError):
            fail(path, "referenced body is not strict JSON")
        else:
            check_sanitization(target)


def check_example_identity(
    path: Path, data: dict[str, object], seen: set[str]
) -> dict[str, object] | None:
    """Validate a documented example ID and its evidence metadata."""
    identifier = data.get("id")
    if identifier != path.stem or not isinstance(identifier, str) or identifier in seen:
        fail(path, "missing, duplicate, or filename-mismatched ID")
    else:
        seen.add(identifier)
    kind = data.get("kind")
    if not isinstance(kind, str) or kind not in KINDS:
        fail(path, "unsupported evidence kind")
    evidence = data.get("evidence")
    required_evidence = {
        "source",
        "observationDate",
        "model",
        "firmware",
        "confirmation",
    }
    if not isinstance(evidence, dict) or not required_evidence <= evidence.keys():
        fail(path, "missing evidence metadata")
        return None
    check_link(path, str(evidence["source"]))
    if (
        kind == "app-derived"
        and not {"appVersion", "sourceReference"} <= evidence.keys()
    ):
        fail(path, "app-derived evidence needs app version and source reference")
    if kind == "live-sanitized" and not evidence["observationDate"]:
        fail(path, "live capture needs an observation date")
    return evidence


def check_sanitization(path: Path) -> None:
    """Reject likely credentials and unsanitized household identifiers."""
    serialized = path.read_text(encoding="utf-8")
    if SECRET.search(serialized):
        fail(path, "possible credential material (value suppressed)")
    identifiers = re.findall(r"\b[0-9a-fA-F]{12}\b", serialized)
    if any(
        value != "a1b2c3d4e5f6" and not value.startswith("000000")
        for value in identifiers
    ):
        fail(path, "non-placeholder device identifier (value suppressed)")
    if re.search(r"\b(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b", serialized):
        fail(path, "possible household MAC address (value suppressed)")


def check_cloud_example(path: Path, seen: set[str]) -> None:
    """Validate a documented HTTP exchange and its evidence metadata."""
    try:
        data = read_json(path)
    except (ValueError, OSError):
        fail(path, "invalid strict JSON")
        return
    if not isinstance(data, dict):
        fail(path, "example must be a JSON object")
        return
    evidence = check_example_identity(path, data, seen)
    if not isinstance(evidence, dict):
        return
    request = data.get("request")
    response = data.get("response")
    if not isinstance(request, dict) or not isinstance(response, dict):
        fail(path, "HTTP example needs request and response objects")
        return
    if not {"method", "baseUrl", "path", "authentication", "headers"} <= request.keys():
        fail(path, "incomplete HTTP request metadata")
    method = request.get("method")
    if not isinstance(method, str) or method not in {
        "GET",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
    }:
        fail(path, "unknown HTTP method")
    if not str(request.get("path", "")).startswith("/"):
        fail(path, "HTTP path must begin with slash")
    representation = response.get("representation")
    if not isinstance(representation, str) or representation not in REPRESENTATIONS:
        fail(path, "unknown response representation")
    status = response.get("status")
    if "status" not in response or (
        status is not None and (type(status) is not int or not 100 <= status <= 599)
    ):
        fail(path, "status must be null or an HTTP status integer")
    if "host" not in evidence:
        fail(path, "HTTP evidence needs host metadata")
    check_body(path, request)
    check_body(path, response)
    check_sanitization(path)


def check_mqtt_example(path: Path, seen: set[str]) -> None:
    """Validate a sanitized MQTT record and its provenance metadata."""
    try:
        data = read_json(path)
    except (ValueError, OSError):
        fail(path, "invalid strict JSON")
        return
    if not isinstance(data, dict):
        fail(path, "example must be a JSON object")
        return
    check_example_identity(path, data, seen)
    if not {"direction", "topic", "qos", "retain", "payload"} <= data.keys():
        fail(path, "incomplete MQTT metadata")
    direction = data.get("direction")
    if not isinstance(direction, str) or direction not in {
        "client-to-cloud",
        "cloud-to-client",
    }:
        fail(path, "unknown MQTT direction")
    topic = data.get("topic")
    if not isinstance(topic, str) or not topic.startswith("/"):
        fail(path, "MQTT topic must be a non-empty absolute topic")
    qos = data.get("qos")
    if qos is not None and (type(qos) is not int or qos not in {0, 1, 2}):
        fail(path, "MQTT QoS must be 0, 1, 2, or unknown")
    retain = data.get("retain")
    if retain is not None and type(retain) is not bool:
        fail(path, "MQTT retain must be boolean or unknown")
    check_sanitization(path)


def main() -> int:
    """Run all local checks without importing or executing protocol code."""
    markdown = sorted(
        path
        for path in ROOT.rglob("*.md")
        if ".git" not in path.parts and "dist" not in path.parts
    )
    targets: set[Path] = set()
    for path in markdown:
        targets.update(check_markdown(path))

    cloud_examples = sorted((ROOT / "examples/cloud").glob("*.json"))
    mqtt_examples = sorted((ROOT / "examples/mqtt").glob("*.json"))
    seen: set[str] = set()
    examples = [(path, check_cloud_example) for path in cloud_examples]
    examples += [(path, check_mqtt_example) for path in mqtt_examples]
    for path, checker in examples:
        checker(path, seen)
        if path not in targets:
            fail(path, "example has no incoming documentation link")

    fixtures = sorted((ROOT / "tests/fixtures").glob("*.json"))
    for path in fixtures:
        try:
            read_json(path)
        except (ValueError, OSError):
            fail(path, "invalid strict fixture JSON")
        if SECRET.search(path.read_text(encoding="utf-8")):
            fail(path, "possible credential material (value suppressed)")

    scripts = sorted((ROOT / "examples").glob("*.py"))
    scripts += sorted((ROOT / "tools").glob("*.py"))
    for path in scripts:
        try:
            ast.parse(
                path.read_text(encoding="utf-8"),
                filename=str(path),
                feature_version=(3, 11),
            )
        except (SyntaxError, UnicodeError, ValueError):
            fail(path, "Python script does not parse as Python 3.11")
        if SECRET.search(path.read_text(encoding="utf-8")):
            fail(path, "possible credential material (value suppressed)")

    for error in ERRORS:
        print(error)
    if ERRORS:
        print(f"FAILED: {len(ERRORS)} documentation errors")
        return 1
    print(
        f"OK: {len(markdown)} Markdown files, {len(cloud_examples)} HTTP examples, "
        f"{len(mqtt_examples)} MQTT examples, {len(fixtures)} strict fixtures, "
        f"{len(scripts)} Python 3.11 scripts parsed; all checks offline"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
