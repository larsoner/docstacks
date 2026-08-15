# CLAUDE.md

Guidance for AI coding agents working in this repository.

## What this is

`docstacks` manages the one-directory-per-version documentation layout that scientific Python projects publish to GitHub Pages: a site root holding `dev/`, `1.12/`, `1.11/`, a `stable` symlink, and a `versions.json` manifest read by the pydata-sphinx-theme version switcher.
It is "mike for Sphinx" — it owns the deploy transaction, not the build.
It has the manifest layer (`manifest.py`, `tree.py`), the git backend (`deploy.py` and `lifecycle.py` over `_repo.py` and `_git.py`), and a thin CLI over all of it: `deploy`, `promote`, `retitle`, `delete`, `prune`, plus the standalone `validate`, `generate`, and `list`.
Read [DESIGN.md](DESIGN.md) before adding anything structural — it records the roadmap, the switcher schema semantics, and the reasoning behind the constraints below.

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

**History rewriting uses plumbing, not porcelain.**
`prune` re-parents existing tree objects with `git commit-tree`; a rebase or cherry-pick would replay diffs across gigabytes of HTML.
Anything else that reshapes history should follow the same rule.
