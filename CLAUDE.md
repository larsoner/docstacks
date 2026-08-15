# CLAUDE.md

Guidance for AI coding agents working in this repository.

## What this is

`docstacks` manages the one-directory-per-version documentation layout that scientific Python projects publish to GitHub Pages: a site root holding `dev/`, `1.12/`, `1.11/`, a `stable` symlink, and a `versions.json` manifest read by the pydata-sphinx-theme version switcher.
It is "mike for Sphinx" — it owns the deploy transaction, not the build.
Today only the manifest layer exists (`manifest.py`, `tree.py`, and a thin CLI over both); the git deploy backend and the `promote`/`prune`/`delete`/`retitle` lifecycle commands are still to come.
Read [DESIGN.md](DESIGN.md) before adding anything structural — it records the roadmap, the switcher schema semantics, and the reasoning behind the constraints below.

## Dev setup

```bash
uv venv && uv pip install -e . --group dev
```

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
pytest                                    # the whole suite, runs in well under a second
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

**No global state and no I/O outside the obvious places.**
`manifest.py` touches the filesystem only in `load` and `dump`; `tree.py` reads a directory and nothing else.
Keeping the library side-effect-free is what makes the deploy transaction testable when it lands.
