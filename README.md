# docstacks

**Status: pre-alpha.** The API and CLI will change without notice.

`docstacks` manages *stacks of documentation versions*: the one-directory-per-version layout that scientific Python projects (MNE-Python, NumPy, scikit-learn, scikit-image) deploy to GitHub Pages, where the repo root holds `dev/`, `1.12/`, `1.11/`, a `stable` symlink, and a `versions.json` manifest read by the [pydata-sphinx-theme](https://pydata-sphinx-theme.readthedocs.io/) version switcher.

Think of it as **mike for Sphinx**: [mike](https://github.com/jimporter/mike) owns the deploy transaction for versioned MkDocs sites, and every Sphinx-based project has hand-rolled its own version of that same shell script. `docstacks` aims to be the shared, tested implementation — speaking the pydata-sphinx-theme switcher schema rather than mike's.

## Non-goals

- **It never invokes a documentation builder.** No `sphinx-build`, no `make html`, no plugins, no conf.py. You hand it a directory of already-built HTML.
- **It never rebuilds old versions.** Previously deployed versions are opaque byte trees that get moved, aliased, or deleted — never regenerated.
- **It does not clone, and it does not own your credentials.** You give it a checkout you made; it gives you a commit. Remote URLs, branch names, tokens, and caching stay in your CI config where you can see them.
- It is not a static-site host, a redirect service, or a theme.

## What works today

Release day, as one atomic commit:

```bash
docstacks promote doc/_build/html 1.13 \
    --repo ~/mne-tools.github.io \
    --base-url https://mne.tools/ \
    --source-sha $GIT_SHA \
    --push
```

That copies the build to `1.13/`, repoints the `stable` symlink at it, marks it preferred in `versions.json`, **and demotes 1.12 in the same commit** — sending its entry back to `https://mne.tools/1.12/` so the manifest stops claiming it is what `/stable/` serves. Everything else at the site root — `CNAME`, `.nojekyll`, your landing page, other versions — is left exactly as it was.

Nightly dev docs are the same shape without the alias juggling:

```bash
docstacks deploy doc/_build/html dev --repo ~/mne-tools.github.io --push
```

Making the checkout is your job (a shallow sparse clone is the usual choice); `docstacks` refuses to touch anything but a clean working tree, and refuses to push from a detached `HEAD`.

The rest of the lifecycle:

```bash
docstacks retitle 1.11 "1.11 (archived)" --repo ~/mne-tools.github.io
docstacks delete 0.24 --repo ~/mne-tools.github.io
docstacks prune --repo ~/mne-tools.github.io --keep 20   # collapse older history
```

`prune` keeps the most recent commits intact and squashes everything older into one root, so the repo stops growing without invalidating recent shallow clones. It rewrites history, so it warns loudly and only force-pushes when you ask.

The manifest tools stand alone too:

```bash
docstacks validate versions.json          # report problems, exit 1 if any
docstacks generate ./site --base-url https://mne.tools/
docstacks list versions.json
```

`docstacks.manifest` round-trips `versions.json` byte-stably, preserving entry order, indentation, unknown keys, and hand-added foreign entries (legacy catch-alls and the like). `docstacks.tree.scan_tree` derives a manifest from a deployed site directory, resolving alias symlink chains such as `stable -> 2.1 -> 2.1.3`.

See [DESIGN.md](DESIGN.md) for the reasoning behind all of it.

## Requirements

Python 3.10+ and a `git` binary on `PATH`. No Python runtime dependencies at all — that is deliberate, so installing this in a documentation CI job cannot disturb the doc build's own environment.

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
