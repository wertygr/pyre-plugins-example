#!/usr/bin/env python3
"""Build the official PYRE plugin index (index.json + README.md).

Plugin code lives in separate repositories, one per plugin. This repository
only collects their metadata (`plugin.json`) and resolves a stable install URL:

    https://github.com/<owner>/<repo>/releases/latest/download/<asset>

Usage:
    python tools/build.py                  # update index.json and README.md
    python tools/build.py --check          # fail if committed files are stale
    python tools/build.py --allow-missing  # skip plugins that are not published yet

The script uses the standard library only: no dependencies, no API token.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SOURCES_FILE = ROOT / "sources.json"
INDEX_FILE = ROOT / "index.json"
README_FILE = ROOT / "README.md"

BEGIN = "<!-- BEGIN INDEX -->"
END = "<!-- END INDEX -->"

NAME_RE = re.compile(r"^_[a-z0-9](?:[a-z0-9_]*[a-z0-9])?_$")
CONFIG_KEYS = frozenset({"file", "cache", "api", "hooks"})
USER_AGENT = "pyre-plugins-index/1.0"
TIMEOUT = 20.0

RAW_URL = "https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
LATEST_URL = "https://github.com/{owner}/{repo}/releases/latest/download/{asset}"
VERSION_URL = "https://github.com/{owner}/{repo}/releases/download/{{tag}}/{asset}"
REPO_URL = "https://github.com/{owner}/{repo}"


class BuildError(Exception):
    """A source is broken and the index cannot be built."""


class Missing(BuildError):
    """A plugin is not published yet (repo, metadata or release is absent)."""


# --------------------------------------------------------------------------- io

def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.read()
    except urllib.error.HTTPError as e:
        hint = f" ({e.reason})" if e.reason else ""
        raise BuildError(f"HTTP {e.code} for {url}{hint}") from None
    except (urllib.error.URLError, OSError) as e:
        raise BuildError(f"cannot fetch {url}: {e}") from None


def probe(url: str) -> int | None:
    """Return the HTTP status of `url`, or None if the host is unreachable."""
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status
    except urllib.error.HTTPError as e:
        if e.code != 405:  # 405: some hosts reject HEAD, retry with a ranged GET
            return e.code
    except (urllib.error.URLError, OSError):
        return None

    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Range": "bytes=0-0"}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, OSError):
        return None


def read_json(path: Path) -> Any:
    try:
        name = str(path.relative_to(ROOT))
    except ValueError:  # a local plugin checkout outside this repository
        name = str(path)

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise BuildError(f"missing file: {name}") from None
    except json.JSONDecodeError as e:
        raise BuildError(f"{name}: invalid JSON ({e})") from None


# --------------------------------------------------------------------- sources

def load_sources() -> dict:
    sources = read_json(SOURCES_FILE)
    if not isinstance(sources, dict):
        raise BuildError("sources.json must contain an object")

    owner = sources.get("owner")
    if not isinstance(owner, str) or not owner.strip():
        raise BuildError('sources.json: "owner" must be a GitHub user or organization')

    plugins = sources.get("plugins")
    if not isinstance(plugins, list):
        raise BuildError('sources.json: "plugins" must be a list')

    sources.setdefault("branch", "HEAD")
    sources.setdefault("plugin_json", "plugin.json")
    sources.setdefault("asset_suffix", ".zip")
    return sources


def normalize(entry: Any) -> dict:
    """A source entry is either a repo name or an object with repo/asset/local."""
    if isinstance(entry, str):
        entry = {"repo": entry}
    if not isinstance(entry, dict):
        raise BuildError(f"invalid plugins entry: {entry!r}")

    repo = entry.get("repo")
    if repo is not None and not isinstance(repo, str):
        raise BuildError(f"invalid repo name: {repo!r}")

    asset = entry.get("asset")
    local = entry.get("local")

    if asset is not None and not isinstance(asset, str):
        raise BuildError(f"invalid asset name: {asset!r}")
    if local is not None and not isinstance(local, str):
        raise BuildError(f"invalid local path: {local!r}")
    if repo is None and local is None:
        raise BuildError(f"plugin entry needs at least \"repo\" or \"local\": {entry!r}")

    return {"repo": repo, "asset": asset, "local": local}


# -------------------------------------------------------------------- metadata

def is_plugin_file(value: Any) -> bool:
    """True for a relative *.py path inside the package: "hello.py" or "panel/__init__.py"."""
    if not isinstance(value, str) or not value.endswith(".py"):
        return False
    if value.startswith("/") or "\\" in value or ":" in value:
        return False
    parts = value.split("/")
    return all(part and part not in (".", "..") for part in parts)


def validate(meta: Any, where: str) -> list[str]:
    if not isinstance(meta, dict):
        return [f"{where}: plugin.json must contain an object"]

    errors: list[str] = []
    name = meta.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        errors.append(f'{where}: "name" must look like "_hello_" (it is the command name)')

    title = meta.get("title")
    if not isinstance(title, str) or not title.strip():
        errors.append(f'{where}: "title" must be a non-empty string')

    description = meta.get("description", "")
    if not isinstance(description, str):
        errors.append(f'{where}: "description" must be a string')

    tags = meta.get("tags", [])
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        errors.append(f'{where}: "tags" must be a list of strings')

    config = meta.get("config")
    if not isinstance(config, dict):
        errors.append(f'{where}: "config" must be an object (the plugin settings block)')
    else:
        unknown = set(config) - CONFIG_KEYS
        if unknown:
            errors.append(f'{where}: unknown "config" keys: {", ".join(sorted(unknown))}')

        file = config.get("file")
        if not is_plugin_file(file):
            errors.append(
                f'{where}: "config.file" must be a relative "*.py" path inside the package '
                f'(e.g. "hello.py" or "panel/__init__.py")'
            )

        hooks = config.get("hooks", [])
        if not isinstance(hooks, list) or any(not isinstance(hook, str) for hook in hooks):
            errors.append(f'{where}: "config.hooks" must be a list of strings')

        for key in ("cache", "api"):
            if key in config and not isinstance(config[key], bool):
                errors.append(f'{where}: "config.{key}" must be a boolean')

    return errors


def read_meta(entry: dict, sources: dict) -> tuple[dict, str]:
    """Return (metadata, origin) for a source entry."""
    local = entry["local"]
    if local is not None:
        directory = (ROOT / local).resolve()
        path = directory / sources["plugin_json"]
        try:
            meta = read_json(path)
        except BuildError as e:
            raise Missing(str(e)) from None
        return meta, f"local:{local}"

    repo = entry["repo"]
    url = RAW_URL.format(
        owner=sources["owner"],
        repo=repo,
        branch=sources["branch"],
        path=sources["plugin_json"],
    )
    try:
        raw = fetch(url)
    except BuildError as e:
        raise Missing(str(e)) from None

    try:
        meta = json.loads(raw)
    except json.JSONDecodeError as e:
        raise BuildError(f"{url}: invalid JSON ({e})") from None
    return meta, repo


def resolve(entry: dict, sources: dict, check_assets: bool) -> dict:
    meta, origin = read_meta(entry, sources)

    errors = validate(meta, origin)
    if errors:
        raise BuildError("\n".join(errors))

    local = entry["local"]
    if local is not None:
        base = (ROOT / local).resolve()
        plugin_file = base / meta["config"]["file"]
        if not plugin_file.is_file():
            raise BuildError(
                f'{origin}: "config.file" {meta["config"]["file"]!r} does not exist'
            )

    repo = entry["repo"]
    asset = entry["asset"] or f"{meta['name'].strip('_')}{sources['asset_suffix']}"

    url = None
    version_url = None
    if repo is not None:
        # `releases/latest/download/...` is a stable link: it always serves the
        # asset of the latest non-draft, non-prerelease release of that repo.
        url = LATEST_URL.format(owner=sources["owner"], repo=repo, asset=asset)
        version_url = VERSION_URL.format(owner=sources["owner"], repo=repo, asset=asset)

        if check_assets:
            status = probe(url)
            if status == 404:
                raise Missing(
                    f"{origin}: no release asset {asset!r} "
                    f"(publish a release with that asset name)"
                )
            if status is None:
                raise BuildError(f"{origin}: cannot reach {url}")
            if status >= 400:
                raise BuildError(f"{origin}: HTTP {status} for {url}")

    return {
        "name": meta["name"],
        "title": meta["title"],
        "description": meta.get("description", ""),
        "tags": sorted(meta.get("tags", [])),
        "since": meta.get("since"),
        "repo": repo,
        "url": url,
        "version_url": version_url,
        "config": meta["config"],
    }


# ----------------------------------------------------------------------- build

def build(sources: dict, allow_missing: bool, check_assets: bool) -> tuple[list[dict], list[str]]:
    entries: list[dict] = []
    warnings: list[str] = []
    seen: dict[str, str] = {}

    for raw in sources["plugins"]:
        entry = normalize(raw)
        label = entry["repo"] or entry["local"]
        try:
            item = resolve(entry, sources, check_assets)
        except Missing as e:
            if not allow_missing:
                raise
            warnings.append(f"skipped {label}: {e}")
            continue

        if item["name"] in seen:
            raise BuildError(
                f'duplicate plugin name {item["name"]}: '
                f'{seen[item["name"]]} and {label}'
            )
        seen[item["name"]] = label
        entries.append(item)

    entries.sort(key=lambda item: item["name"])
    return entries, warnings


def index_doc(entries: list[dict]) -> dict:
    return {
        "schema": 1,
        "plugins": entries,
    }


def render_index(entries: list[dict]) -> str:
    return json.dumps(index_doc(entries), indent=4, ensure_ascii=False) + "\n"


def cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def render_table(entries: list[dict], owner: str) -> str:
    if not entries:
        return "_No plugins yet._"

    lines = [
        "| plugin | what it shows | tags | install |",
        "|:--|:--|:--|:--|",
    ]
    for item in entries:
        tags = ", ".join(item["tags"]) or "-"
        if item["repo"]:
            name = f'[`{item["name"]}`]({REPO_URL.format(owner=owner, repo=item["repo"])})'
        else:
            name = f'`{item["name"]}`'
        install = f'`_ppm_ install {item["url"]}`' if item["url"] else "not published"
        lines.append(
            f'| {name} | {cell(item["description"])} | {tags} | {install} |'
        )
    return "\n".join(lines)


def apply_readme(template: str, table: str) -> str:
    try:
        start = template.index(BEGIN) + len(BEGIN)
        end = template.index(END)
    except ValueError:
        raise BuildError(
            f"README.md must contain the markers {BEGIN} and {END}"
        ) from None
    return f"{template[:start]}\n\n{table}\n\n{template[end:]}"


# ------------------------------------------------------------------------ main

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build index.json and the README index table.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write files, exit 1 if the committed index is out of date",
    )
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="skip plugins whose repo or release is not published yet",
    )
    parser.add_argument(
        "--no-probe",
        action="store_true",
        help="do not verify that each release asset exists",
    )
    args = parser.parse_args(argv)

    try:
        sources = load_sources()
        entries, warnings = build(
            sources,
            allow_missing=args.allow_missing,
            check_assets=not args.no_probe,
        )

        index_text = render_index(entries)
        table = render_table(entries, sources["owner"])
        readme_text = apply_readme(README_FILE.read_text(encoding="utf-8"), table)

        for warning in warnings:
            print(f"warning: {warning}", file=sys.stderr)

        if args.check:
            stale = []
            if not INDEX_FILE.exists() or INDEX_FILE.read_text(encoding="utf-8") != index_text:
                stale.append("index.json")
            if README_FILE.read_text(encoding="utf-8") != readme_text:
                stale.append("README.md")
            if stale:
                print(
                    f"error: {', '.join(stale)} is out of date "
                    f"(run: python tools/build.py)",
                    file=sys.stderr,
                )
                return 1
            print("index is up to date")
            return 0

        INDEX_FILE.write_text(index_text, encoding="utf-8")
        README_FILE.write_text(readme_text, encoding="utf-8")
        print(f"ok: {len(entries)} plugin(s), {len(warnings)} skipped")
        return 0

    except BuildError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
