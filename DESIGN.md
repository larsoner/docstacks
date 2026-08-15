# docstacks design and roadmap

## Problem

Versioned documentation sites in the scientific Python ecosystem all share one layout: a GitHub Pages repository whose root contains one directory per released version (`1.12/`, `1.11/`, ...), a `dev/` directory for the development build, a committed `stable` symlink pointing at the current release directory, and a `versions.json` manifest at the root that the pydata-sphinx-theme version switcher fetches at page load.

Every project has hand-rolled the machinery that maintains this: shell scripts and ad-hoc CI steps that `rsync` a build directory in, hand-edit `versions.json`, re-point the symlink, squash history so the repo does not grow without bound, and hope the whole thing is atomic. The scripts diverge, the failure modes are project-specific, and nobody tests them.

`docstacks` is that machinery, extracted and tested. [mike](https://github.com/jimporter/mike) does this job for MkDocs; the gap is that mike is coupled to MkDocs and to its own manifest schema, so Sphinx projects cannot adopt it.

## Scope boundaries

These are fixed, not provisional:

- **Never invoke a documentation builder.** The input to every command is a directory of built HTML. Building is the caller's job, which keeps `docstacks` out of the dependency-hell that plugin-style tools land in and makes it usable from any CI.
- **Never rebuild old versions.** Deployed versions are opaque byte trees. They can be moved, aliased, retitled, or deleted; they are never regenerated, so old docs stay exactly as they were published.
- **Zero runtime dependencies.** Stdlib only, so `pip install docstacks` in CI is trivially fast and cannot conflict with a project's doc-build environment. This constrains the design: no `packaging` for version sorting, no `pydantic` for the model, no `click` for the CLI.

## Manifest schema

The canonical schema is the pydata-sphinx-theme switcher's, *not* mike's. A `versions.json` is a JSON array of objects:

```json
[
  {"name": "1.13 (dev)", "version": "dev", "url": "https://mne.tools/dev/"},
  {"name": "1.12 (stable)", "version": "1.12", "url": "https://mne.tools/stable/", "preferred": true},
  {"version": "1.11", "url": "https://mne.tools/1.11/"}
]
```

Semantics, as implemented by the theme:

- `version` is matched by **strict string equality** against each build's baked-in `version_match` value. It is an identity, not a version number to be parsed — `dev`, `stable`, and `legacy` are all legitimate values.
- `name` is display-only and defaults to `version` when absent.
- `preferred` marks the canonical release. The theme shows its "you are viewing an old version" banner based on `entry.preferred && entry.match`, so exactly one entry should carry it.
- `url` is absolute and should end in `/`.

Two consequences drive the implementation. First, **foreign entries must survive**: real manifests contain hand-added catch-alls (MNE ships a `{"name": "≤ 0.20 (legacy)", "version": "legacy", "url": ".../old_versions/"}` entry) that `docstacks` neither owns nor understands, and must not drop or reorder. Second, **unknown keys must survive**: the theme ignores extra keys, so projects add them, and a round-trip through `docstacks` must be byte-stable.

Hence `Entry` carries an `extra` dict of unrecognized keys, serialization emits `name`, `version`, `url`, `preferred` and then extras in their original order, and false/absent fields are omitted rather than written out — matching how these files look when written by hand.

## Roadmap

### Milestone 1 — manifest layer (this work)

`docstacks.manifest` (model, byte-stable JSON round-trip, mutations, `validate()`), `docstacks.tree.scan_tree` (derive a manifest by scanning a deployed site directory, resolving alias symlinks), and a thin CLI over both: `validate`, `generate`, `list`.

No git, no deployment. This is the layer every later milestone builds on, and it is independently useful: a project can drop `docstacks validate` into CI today, or regenerate a drifted `versions.json` from what is actually deployed.

### Milestone 2 — git deploy backend

```
docstacks deploy <html-dir> <version> [--alias stable]
```

Operates on a **shallow, sparse checkout** of the pages branch — cloning the full history of a docs repo is prohibitive (MNE's is many GB), and sparse checkout means only the paths being touched are materialized.

- Aliases are **symlinks only**, never copies. `stable -> 1.12` is a git symlink object; duplicating the tree would double the repo size per release and break relative asset paths differently across the two copies.
- The commit carries trailers recording provenance:
  ```
  Deployed-version: 1.12
  Source-sha: <sha of the source repo commit that produced the HTML>
  ```
  so any deployed directory can be traced back to the exact source revision without a side-channel.
- `versions.json` at the repo root is updated **in the same commit** as the content. A commit that adds `1.12/` without listing it, or lists it without adding it, is a broken intermediate state that users can hit mid-deploy.
- **Unmanaged root files are preserved**: `CNAME`, `.nojekyll`, `index.html`, `robots.txt`, `.github/` and anything else at the root that is not a version directory `docstacks` knows about. `docstacks` owns the version directories, the alias symlinks, and `versions.json`; everything else at the root is somebody else's and must round-trip untouched.

### Milestone 3 — lifecycle commands

```
docstacks promote <version>          # make it stable
docstacks prune [--keep N | --policy ...]
docstacks delete <version>
docstacks retitle <version> <name>
```

`prune` performs **retention-anchored history squashing**: history older than the retention anchor is collapsed, but the result is *not* a single orphan commit. Squashing everything to one commit is the obvious approach and it is wrong here — it invalidates every previously fetched shallow clone, forces every CI job to re-download the whole tree, and destroys the `Deployed-version:`/`Source-sha:` provenance for versions still on the site. Instead, commits within the retention window keep their identity and only the pre-anchor tail is collapsed.

`delete` removes a version directory and its manifest entry (refusing if an alias still points at it); `retitle` changes only the manifest `name`, touching no content.

### Later — rsync backend

The same command surface against an rsync/SSH target instead of a git branch, for projects that publish to a plain web server. The deploy transaction is factored behind a backend interface from milestone 2 onward so this does not require restructuring.

## Adoption path

1. **MNE-Python** as the pilot: the author maintains it, its manifest exercises the awkward cases (dev entry, legacy foreign entry, symlinked stable), and its deploy scripts are the direct motivation.
2. **scikit-image** next: similar scale, same layout, an independent maintainer group to validate that nothing MNE-specific leaked into the design.
3. **NumPy** as the scale test: the largest history and the strongest requirement that shallow clones stay cheap.
