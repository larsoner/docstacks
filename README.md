# docstacks

**Status: pre-alpha.** The API and CLI will change without notice.

`docstacks` manages *stacks of documentation versions*: the one-directory-per-version layout that scientific Python projects (MNE-Python, NumPy, scikit-learn, scikit-image) deploy to GitHub Pages, where the repo root holds `dev/`, `1.12/`, `1.11/`, a `stable` symlink, and a `versions.json` manifest read by the [pydata-sphinx-theme](https://pydata-sphinx-theme.readthedocs.io/) version switcher.

Think of it as **mike for Sphinx**: [mike](https://github.com/jimporter/mike) owns the deploy transaction for versioned MkDocs sites, and every Sphinx-based project has hand-rolled its own version of that same shell script. `docstacks` aims to be the shared, tested implementation — speaking the pydata-sphinx-theme switcher schema rather than mike's.

## Non-goals

- **It never invokes a documentation builder.** No `sphinx-build`, no `make html`, no plugins, no conf.py. You hand it a directory of already-built HTML.
- **It never rebuilds old versions.** Previously deployed versions are opaque byte trees that get moved, aliased, or deleted — never regenerated.
- It is not a static-site host, a redirect service, or a theme.

## What works today

Milestone 1 — the manifest layer only:

```bash
docstacks validate versions.json          # report problems, exit 1 if any
docstacks generate ./site --base-url https://mne.tools/
docstacks list versions.json
```

`docstacks.manifest` round-trips `versions.json` byte-stably, preserving entry order, indentation, unknown keys, and hand-added foreign entries (legacy catch-alls and the like). `docstacks.tree.scan_tree` derives a manifest from a deployed site directory, resolving alias symlink chains such as `stable -> 2.1 -> 2.1.3`.

Deploying is not implemented yet. See [DESIGN.md](DESIGN.md) for the roadmap.

## Development

```bash
uv venv && uv pip install -e . --group dev   # or: pip install -e . --group dev
prek install                                 # ruff, codespell, yamllint, toml-sort, zizmor
```

Three gates, all of which CI runs:

```bash
prek run -a
ty check
pytest --cov=docstacks --cov-report=term-missing
```

Runtime dependencies are deliberately empty and must stay that way; see [CLAUDE.md](CLAUDE.md) for the rest of the conventions.

## License

BSD 3-Clause.
