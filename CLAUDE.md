# CLAUDE.md

Guidance for AI coding agents working in this repository.

## What this is

`docstacks` manages the one-directory-per-version documentation layout that scientific Python projects publish to GitHub Pages: a site root holding `dev/`, `1.12/`, `1.11/`, a `stable` symlink, and a `versions.json` manifest read by the pydata-sphinx-theme version switcher.
It is "mike for Sphinx" — it owns the deploy transaction, not the build.
It has the manifest layer (`manifest.py`, `tree.py`), the git backend (`deploy.py` and `lifecycle.py` over `_repo.py` and `_git.py`), the live switcher checks (`check.py`), and a thin CLI over all of it: `deploy`, `promote`, `retitle`, `delete`, `prune`, plus the standalone `validate`, `generate`, and `list`.
Read [DESIGN.md](DESIGN.md) before adding anything structural — it records the roadmap, the switcher schema semantics, and the reasoning behind the constraints below.

## Adoption roadmap

The whole point of this tool is to replace hand-rolled deployment machinery in real projects, starting with MNE-Python; work here should be weighed against the plan below.

### MNE-Python (pilot, deployed)

The deployment target is the `mne-tools/mne-tools.github.io` repo (branch `main`), served at https://mne.tools/ via legacy GitHub Pages, with unmanaged root files (`CNAME`, `.nojekyll`, `index.html`, `versionwarning.js`) that a deploy must never touch.
The layout is `dev/` plus one `X.Y/` directory per version plus `stable` as a committed relative symlink (converted August 2026); GitHub Pages serves committed symlinks, which the whole design relies on.
Deploys run from CircleCI in `mne-tools/mne-python` (`.circleci/config.yml`, "Deploy docs" step) over an SSH deploy key against a cached `--depth=1` clone; since mne-tools/mne-python#14158 the branch mapping is `main` → `dev/` and `maint/X.Y` → `X.Y/`, nightly crons skip when a deployed `_version.txt` already matches the source SHA, and `[circle deploy]` in a commit message forces a build.
The switcher manifest's source of truth is `versions.json` at the site root, owned by docstacks; before September 2026 it lived in mne-python as `doc/_static/versions.json` and was served from `dev/_static/`, and that old URL is baked into every build published before then.

The switchover landed in three pieces: mne-tools/mne-python#14273 (2026-09-03) replaced the CircleCI copy-and-commit block with `python -m docstacks deploy ... --no-manifest`, pinned by git SHA; a manual site commit seeded `versions.json` at the site root; and mne-python commit 2a29ec571 (2026-09-09) flipped `json_url` to https://mne.tools/versions.json, deleted `doc/_static/versions.json`, and made CI deploy with `--base-url https://mne.tools/`.
Every already-published build still fetches https://mne.tools/dev/_static/versions.json, and that keeps working because the CI step runs `ln -sfn ../../versions.json /tmp/build/html/_static/versions.json` on the build output before deploying; `deploy` copies with `symlinks=True`, so the shim is redeployed with `dev/` every time and no shim-management feature was needed after all.
The CI deploy step decides on its own: a `maint/X.Y` directory that sorts newer than `readlink stable` runs `promote` instead of `deploy` (so the first `[circle deploy]` on a new maint branch repoints `stable` and the switcher in one commit), `dev` deploys pass `--name "<stable minor + 1> (dev)"`, and everything else plain-deploys; the old `readlink stable` alias guard is gone since `deploy` keeps existing aliases.
Release day therefore has no manual website step, and the CircleCI nightlies for `main` and `maint/*` both live in every branch's config (each fires only on the branches its filter names), so cutting a maint branch needs no config edits; the previous stable branch's `.circleci/config.yml` is simply deleted to stop its nightly.
The first `[circle deploy]` on a new `maint/X.Y` branch adds that version's manifest entry automatically (non-preferred, pointing at its own directory), so nothing is hand-edited any more.
One tool follow-up is still outstanding: `promote` should accept an already-deployed version without new HTML (no longer needed by MNE, since CI promotes from the build it just made).
The CI deploy step also runs `prune --keep 20 --push` after every deploy, so the site history is a rolling window and MNE's manual history squash is gone; this needs the CI clone to be full rather than `--depth=1` (a short history is a no-op for `prune`, not an error), and a dry run on the real history (2026-09-09) took under a second, kept the tree byte-identical, and cut reachable data from 5.1 GiB to 2.3 GiB.

Adoptable today: a scheduled `docstacks validate https://mne.tools/versions.json --check-urls --check-match --ignore 1.1 --ignore 1.0 --ignore 0.24 --ignore 0.23 --ignore 0.22 --ignore 0.21 --ignore 0.20` (the ignores are pre-pydata-theme archives that can never carry a `version_match`, plus the intentional `0.20` catch-all that points into `dev/old_versions/`).

### Other scientific-python consumers (rough adoption order)

scikit-image is the nearest-term second adopter: `gh-pages` of `scikit-image/docs` already has dir-per-version plus a `stable` symlink, CI force-pushes `dev`, and releases are a manual `reset --hard` ritual that `deploy`/`promote`/`prune` replace directly.
NumPy's `numpy/doc` repo is already "manual docstacks" (dir-per-version, `stable` symlink, hand-edited manifest, hand-run release upload); its extra needs are auxiliary per-version artifacts (PDFs copied into each version dir) and cheap operation against an ~826 MB repo, which is why docstacks never clones and must stay sparse-checkout-friendly.
scikit-learn already automates everything docstacks does (sparse-checkout deploys, a manifest generated from the deployed tree) but is drowning in repo size and may leave git hosting entirely (scikit-learn/scikit-learn#34254) — watching brief, not a target.
pandas lost its doc server in 2026 and its hosting is in flux (pandas-dev/pandas#64703); adoption would need two-level aliases (`stable -> 2.1 -> 2.1.3`, which `tree.py` already resolves on read) — watching brief.
SciPy deploys to a plain server over rsync/SSH, so adoption there needs a non-git transport backend — explicitly future work, do not start it without a design conversation.

### Spec coordination (mike and pydata-sphinx-theme)

Format convergence is being discussed with mike's author in jimporter/mike#263 and with the theme in pydata/pydata-sphinx-theme#2470: mike says `title` where we say `name`, wants `url` optional with relative resolution as the default, has an `aliases` array per entry, and may adopt `preferred`.
Hold all manifest-format changes (accepting `title` as a `name` fallback, tolerating an absent `url`, emitting `aliases`) until those threads settle — do not unilaterally change the emitted schema.
Longer-term, the git layer here is intended to be extractable as a deploy-target plugin for mike's planned "NuMike" generalization; keep it cleanly separated from the manifest layer.
The package name itself may still change (candidates were floated in jimporter/mike#263), so avoid spreading the current name into new user-facing strings beyond what already exists.

## Dev setup

```bash
uv venv && uv pip install -e . --group dev
```

`git` must be on `PATH`: `deploy.py` shells out to the binary, and the deploy tests build throwaway repositories under `tmp_path`.

Or with pip:

```bash
python -m venv .venv && .venv/bin/pip install -e . --group dev
```

Install the git hooks once per clone:

```bash
prek install
```

## Commands

```bash
pytest                                    # the whole suite, a few seconds
pytest tests/test_tree.py -k alias_chain  # one test
pytest --cov=docstacks --cov-report=term-missing

prek run -a                               # ruff, codespell, yamllint, toml-sort, zizmor
ty check                                  # type checking, must be clean
```

All three gates — `prek run -a`, `ty check`, `pytest` — must pass before a change is finished.
CI runs exactly these.

## Conventions

**Zero runtime dependencies is a hard constraint.**
`[project.dependencies]` stays empty; the package must import with nothing but the standard library so that `pip install docstacks` in a documentation CI job is instant and cannot conflict with the project's own doc-build environment.
This rules out `packaging` for version comparison, `pydantic` for the model, and `click` for the CLI, and it is not negotiable for the sake of convenience.
Development tooling lives in the `dev` dependency group, which is unconstrained.

**src layout.**
Importable code is under `src/docstacks/`; there is no `docstacks/` at the repo root.
Tests are in `tests/`, one file per module, named after it (`tests/test_tree.py` covers `src/docstacks/tree.py`), with shared fixtures in `tests/conftest.py`.

**Type hints everywhere.**
Every function and method, public or private, is annotated, including the return type.
`ty check` covers `src/` and `tests/` alike and must stay clean rather than accumulating ignores.

**Docstrings are numpydoc-style.**
Everything public gets one, with `Parameters` and `Returns` sections; private helpers get a single summary line.
Follow the local flavor used elsewhere in the codebase: `str | None` rather than "str or None", no "optional" on keyword arguments that have defaults, no `Raises` section.

**Mutators return `self`.**
`Manifest.add`, `remove`, `retitle`, and `set_preferred` all mutate in place and return the manifest so they can be chained.
Anything new in that family should do the same.

**Comments explain why, not what.**
Assume the reader knows Python and has the diff in front of them.
A comment earns its place by recording a constraint, an invariant, or a surprise — the pandas-style alias chain that `tree.py` has to resolve, for instance — and stays to one line.

**No global state, and I/O stays where it is expected.**
`manifest.py` touches the filesystem only in `load` and `dump`; `tree.py` reads a directory and nothing else; `deploy.py` and `lifecycle.py` are the modules that write, and they do so only after every guard has passed.

**git is a tool dependency, not a package one.**
All git access goes through `docstacks._git`, which shells out to the binary; do not reach for a git library, and do not call `subprocess` for git anywhere else.
Guards, staging, committing, and pushing live in `docstacks._repo` and are shared by every command that writes — a new command reuses them rather than reimplementing the clean-tree or detached-`HEAD` checks.

**Never leave a half-written checkout.**
Validate everything up front, then mutate, then stage path-by-path (`git add -A -- <paths>`) rather than with a repo-wide add.
Every command that touches a version commits with a trailer naming it (`Deployed-version:`, `Deleted-version:`, `Retitled-version:`), so any commit can be attributed to a version and an operation without parsing the subject line.

**Network access is opt-in and lives in `check.py`.**
`urllib` only, no retries, a caller-supplied timeout, and every transport failure turned into a describable finding rather than an exception that aborts the sweep.
Tests never touch the real network: `conftest.py` serves a `tmp_path` tree over localhost with `http.server`.

**History rewriting uses plumbing, not porcelain.**
`prune` re-parents existing tree objects with `git commit-tree`; a rebase or cherry-pick would replay diffs across gigabytes of HTML.
Anything else that reshapes history should follow the same rule.
