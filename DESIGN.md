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

### Milestone 1 — manifest layer (done)

`docstacks.manifest` (model, byte-stable JSON round-trip, mutations, `validate()`), `docstacks.tree.scan_tree` (derive a manifest by scanning a deployed site directory, resolving alias symlinks), and a thin CLI over both: `validate`, `generate`, `list`.

No git, no deployment. This is the layer every later milestone builds on, and it is independently useful: a project can drop `docstacks validate` into CI today, or regenerate a drifted `versions.json` from what is actually deployed.

### Milestone 2 — git deploy backend (done)

```
docstacks deploy <html-dir> <version> --repo <checkout> [--alias stable]
```

- **`docstacks` does not clone.** The `--repo` argument is an existing local checkout that the caller produced; `deploy` refuses anything that is not a clean git working tree. The intended production shape is still a **shallow, sparse checkout** of the pages branch — cloning the full history of a docs repo is prohibitive (MNE's is many GB), and sparse checkout materializes only the paths being touched — but *making* that checkout is a two-line CI recipe (`git clone --depth 1 --filter=blob:none --sparse`), and owning it inside the tool would mean owning credentials, remote URLs, branch naming, and caching too. Keeping `deploy` a pure function of "a directory of HTML plus a checkout" is what makes it testable against a `tmp_path` repo and reusable from any CI. Recipes for the clone step belong in the docs, not in the code.
- **git is a tool dependency, not a package one.** Everything git-related shells out to the `git` binary (`docstacks._git`). A git library would be the only runtime dependency in the project, which the zero-dependency constraint forbids, and the operations needed here are half a dozen plumbing-free commands.
- Aliases are **symlinks only**, never copies. `stable -> 1.12` is a git symlink object; duplicating the tree would double the repo size per release and break relative asset paths differently across the two copies. An alias name that already exists as a *real* directory is a hard error: MNE's site has exactly that (a real `stable/`), and a tool that deletes a real directory to make room for a symlink is a tool that can destroy a deployed version.
- The commit carries trailers recording provenance:
  ```
  Deployed-version: 1.12
  Source-sha: <sha of the source repo commit that produced the HTML>
  ```
  so any deployed directory can be traced back to the exact source revision without a side-channel. They are written as a second `-m` paragraph rather than with `git commit --trailer`, which is too new to rely on across CI images.
- `versions.json` at the repo root is updated **in the same commit** as the content. A commit that adds `1.12/` without listing it, or lists it without adding it, is a broken intermediate state that users can hit mid-deploy.
- **Unmanaged root files are preserved**: `CNAME`, `.nojekyll`, `index.html`, `robots.txt`, `.github/` and anything else at the root that is not a version directory `docstacks` knows about. `docstacks` owns the version directories, the alias symlinks, and `versions.json`; everything else at the root is somebody else's and must round-trip untouched. Staging is therefore path-scoped (`git add -A -- <version> <aliases> versions.json`), never a blanket repo-wide add.
- **Guards run before anything is written.** An empty build (no `index.html`), a dirty checkout, a bad version component, or an alias over a real directory all fail with the working tree exactly as it was found. Deploying byte-identical content twice is likewise refused rather than committed empty.
- Aliases are **not remembered between deploys**. `deploy` writes what it is told and nothing more, so redeploying a version without repeating `--alias stable` moves its manifest URL back to the version directory. Alias *state* is `promote`'s concern, because it is the command that reasons about the site as a whole.
- Pushing resolves the **branch by name** rather than pushing `HEAD`. CI checkouts are frequently detached, where `git push origin HEAD` fails; the check happens in the guard phase, so a deploy that could not be published is refused before it is committed.

### Milestone 3 — lifecycle commands (done)

```
docstacks promote <html-dir> <version> --repo <checkout> [--alias stable]
docstacks retitle <version> <name> --repo <checkout>
docstacks delete <version> --repo <checkout>
docstacks prune --repo <checkout> (--keep N | --keep-since REV)
```

#### `promote`

Everything `deploy` does, plus the demotion of whatever the alias was taken from — in the *same* commit. Without it, release day leaves the manifest lying: `stable` now serves 1.13 while the 1.12 entry still advertises `https://mne.tools/stable/` as its own URL. So any other entry that was `preferred`, or whose URL pointed at an alias being retargeted, is sent back to `<base_url><its version>/`.

The generated `"<version> (stable)"` label is cleared when it is demoted, since it would otherwise go on claiming a status the version no longer has, and the switcher falls back to displaying the bare version. A label someone chose by hand is kept: `docstacks` cannot tell what a human meant by "1.12 LTS", so it fixes only the URL, which it can verify.

#### `delete` and `retitle`

`delete` removes a version directory and its manifest entry, and is **refused while anything still points at the version**: a root symlink aliasing it (transitively — an alias chain counts), or a `preferred` flag on its entry. Deleting under either leaves the site serving a dangling alias or advertising a version that is gone, so the caller has to promote a replacement first. `retitle` changes only the manifest `name`, touching no content.

Both carry their own trailer (`Deleted-version:`, `Retitled-version:`) so that every commit `docstacks` makes is attributable to a version and an operation without parsing the subject line.

#### `prune`

**Retention-anchored history squashing**: history older than the retention anchor is collapsed, but the result is *not* a single orphan commit. Squashing everything to one commit is the obvious approach and it is wrong here — it invalidates every previously fetched shallow clone, forces every CI job to re-download the whole tree, and destroys the `Deployed-version:`/`Source-sha:` provenance for versions still on the site. Instead, commits from the anchor to the tip keep their tree, message, author, and author date, and only the pre-anchor tail becomes one synthetic root.

The rewrite is done with **`git commit-tree`, never a rebase or a cherry-pick**. A rebase replays *diffs*, which on a multi-gigabyte documentation tree means materializing and re-hashing every file in every commit — hours of work to reproduce content that already exists. `commit-tree` re-parents the tree objects git already has: the surviving commits point at byte-identical trees, so the operation is O(number of commits) and cannot alter content even in principle. Concretely:

1. Resolve the anchor (`HEAD~(N-1)` for `--keep N`, or the given revision), and require it to be an ancestor of `HEAD`.
2. If the anchor has no parent it is already the root — nothing to collapse, exit 0 saying so.
3. Build the new root: `git commit-tree <tree of anchor^> -m "Squashed history (docstacks prune)"`. Its tree is the state of the site immediately before the retention window, so the first surviving commit's diff is still meaningful.
4. Walk `git rev-list --reverse <anchor>^..HEAD` — which is inclusive of the anchor, the boundary that is easy to get wrong — re-creating each commit as `git commit-tree <its tree> -p <new parent> -m <its full message>` with `GIT_AUTHOR_NAME`/`EMAIL`/`DATE` restored from the original. The committer becomes whoever ran `prune`, which is accurate: they are the one who made these commit objects.
5. `git update-ref refs/heads/<branch>` to the new tip and `git reset --hard` to resync the index.

`--push` means `git push --force-with-lease`, and the command says loudly on stderr that history was rewritten. Without it, the exact push command is printed rather than run, because rewriting a published docs branch is not something to do by accident.

### Later — rsync backend

The same command surface against an rsync/SSH target instead of a git branch, for projects that publish to a plain web server. The deploy transaction is factored behind a backend interface from milestone 2 onward so this does not require restructuring.

## Adoption path

1. **MNE-Python** as the pilot: the author maintains it, its manifest exercises the awkward cases (dev entry, legacy foreign entry, symlinked stable), and its deploy scripts are the direct motivation.
2. **scikit-image** next: similar scale, same layout, an independent maintainer group to validate that nothing MNE-specific leaked into the design.
3. **NumPy** as the scale test: the largest history and the strongest requirement that shallow clones stay cheap.
