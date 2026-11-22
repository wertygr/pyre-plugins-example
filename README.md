# PYRE plugin examples

A curated list of official example plugins for [PYRE](https://github.com/wertygr/simple_shell).

Every plugin lives in its own repository, so it can be versioned, released and
committed independently. This repository contains no plugin code: it only
collects metadata from each plugin repo and produces a single index.

- [`index.json`](index.json) — machine-readable list (for tools, e.g. a future `_ppm_ search`)
- [`sources.json`](sources.json) — the human-maintained list of plugin repositories
- [`README.md`](README.md) — the table below, generated from `sources.json`

## Install

Install any example with `ppm`, the PYRE package manager (or copy its files into
`plugins/` and add the `plugin` entry to your settings by hand):

```pycon
>>> _ppm_ install https://github.com/wertygr/pyre-plugin-hello/releases/latest/download/hello.zip
>>> _hello_ world
```

The install link always points at the **latest** release of that plugin, so it
never has to change.

<!-- BEGIN INDEX -->

_No plugins yet._

<!-- END INDEX -->

## Pin a version

`latest` is convenient, but it cannot be rolled back. To pin an exact release,
read the tag from the plugin's releases page and use the versioned link instead:

```text
https://github.com/<owner>/<repo>/releases/download/<tag>/<asset>.zip
```

`index.json` stores both forms: `url` (latest) and `version_url` (template with a
`{tag}` placeholder).

## What a plugin repository looks like

```text
pyre-plugin-hello/
├── plugin.json                 # metadata, the only file this index reads
├── hello.py                    # plugin code, copied into plugins/ on install
├── README.md
└── .github/workflows/release.yml
```

`plugin.json` ([JSON Schema](schema/plugin.schema.json)):

```json
{
    "name": "_hello_",
    "title": "Hello world",
    "description": "Minimal plugin: one command, output through api['pft']",
    "tags": ["basic", "sync"],
    "since": "0.1.0",
    "config": {
        "file": "hello.py",
        "cache": true,
        "api": true,
        "hooks": []
    }
}
```

Rules enforced by the index build:

| field | rule |
|:--|:--|
| `name` | `_lowercase_` — it is the command that invokes the plugin |
| `config` | only `file`, `cache`, `api`, `hooks` (these keys are copied as-is into the settings) |
| `config.file` | path inside the package: `hello.py`, or `panel/__init__.py` for a package directory |
| `title` | non-empty, used in the table |
| `description` | one line, used in the table and in `index.json` |
| `tags` | list of strings |
| `since` | optional, first version that provides the example |

## Multi-file plugins (packages)

A plugin can be a package instead of a single file. Then `config.file` points at
its `__init__.py`, and the release archive carries the whole directory:

```text
pyre-plugin-panel/
├── plugin.json                     # "config": {"file": "panel/__init__.py", ...}
├── panel/
│   ├── __init__.py                 # relative imports (from .app import App) work here
│   ├── main.py
│   └── app.py
└── ...
```

```text
panel.zip
├── config.json
└── panel/
    ├── __init__.py
    ├── main.py
    └── app.py
```

`ppm` copies the directory into `plugins/panel/` and tracks it, so uninstalling
removes it whole. `config.file` must not be a directory: the loader needs a file
path, and only `__init__.py` gives the module a `__path__` for relative imports.
Do not ship `__pycache__`.

## Release asset

A plugin repo must publish a release with a **stable asset name**
(`hello.zip` for `pyre-plugin-hello`). The archive is a ppm package: `config.json`
at its root, generated from `plugin.json` as `{"name": ..., "config": {...}}`,
plus the plugin files.

Stable asset name is what makes this work:

```text
https://github.com/<owner>/<repo>/releases/latest/download/<asset>.zip
```

The link resolves to the newest non-draft, non-prerelease release. A new release
does **not** require rebuilding this index; only metadata changes do.

## Add a plugin to the list

1. Publish the plugin repo with a `plugin.json` and at least one release.
2. Add an entry to [`sources.json`](sources.json):

   ```json
   {"repo": "pyre-plugin-hello", "asset": "hello.zip"}
   ```

   `asset` is optional and defaults to `<name>.zip` without the underscores.
   A bare string (`"pyre-plugin-hello"`) also works.

3. Rebuild:

   ```text
   python tools/build.py
   ```

## Build

`tools/build.py` uses the standard library only and needs no API token. It reads
`plugin.json` from `raw.githubusercontent.com`, validates it, probes each release
asset, then rewrites [`index.json`](index.json) and the table above.

```text
python tools/build.py                  # update index.json and README.md
python tools/build.py --check          # fail if committed files are stale (CI, PRs)
python tools/build.py --allow-missing  # skip plugins that are not published yet
python tools/build.py --no-probe       # skip the release asset check (offline work)
```

A source entry may point at a local checkout instead of GitHub — handy while a
plugin is still being written:

```json
{"local": "../pyre-plugin-hello"}
```

Local entries are read from disk and get no install link until `repo` is set.

CI runs on every push, pull request and once a day (to pick up metadata changes).
Pushes to `main` rebuild the index and commit it; pull requests only verify that
the committed index is up to date.

## License

Example plugins are meant to be copied, so pick a permissive license (MIT) in
each plugin repository.
