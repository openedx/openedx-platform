# Merge audit of the reference branch `robrap/lp1148-ulmo4-continue` (#505, ulmo.4+)

Ticket: [LP-1148](https://2u-internal.atlassian.net/browse/LP-1148). Companion to `LP-1148-ulmo-upgrade.md` (the plan), delete together with it.

**Status: reference.** This audits the superseded [#505](https://github.com/edx/edx-platform/pull/505) branch (ulmo.3 base plus ulmo.4+). It is kept because its method, scripts (see the appendix) and findings are reused for batch 1 (ulmo.1). The batch-1 results go in `LP-1148-ulmo1-merge-audit.md`, not yet created. Where this file says "the branch" or "the upgrade branch", it means #505's branch.

Audit date: 2026-10-06, at branch tip `91b8e72823`, the last code commit before the docs were added.

Inputs:

| Input | Ref | Commit |
|---|---|---|
| Fork | `edx/release-ulmo` | `0cfc2835be` |
| Upstream | `openedx/release/ulmo` | `2efdce0760` |
| Pre-ulmo.1 common base | — | `242a69d06b` |

If any of these move, re-run the checks; the scripts are in the appendix.

## Summary

- **4 real merge losses were found, and all are fixed** (commits `6732d6fd28`, `687065c198`, `c56841c5e4`, `91b8e72823`). Three are the same class: fork changes to video JS files that upstream moved from `xmodule/js/src/video/` to `xmodule/assets/video/public/js/`. The fourth is a leftover studio-frontend Makefile line.
- Nothing else was lost: every other flagged item is present in combined form, or deliberately superseded.
- Merging the branch into `release-ulmo` is conflict-free and yields the branch's tree exactly.

## Scope

This audit checks that nothing was **lost in merging**. It does not check what upstream **deliberately broke**, nor database state. For those, see the plan: [Breaking changes in the range](LP-1148-ulmo-upgrade.md#breaking-changes-in-the-range) (nine `!` commits, including the legacy Studio removals) and remaining step 3 (migration state in stage and prod).

Migration inventory, for reference. Files under `*/migrations/` that differ from `edx/release-ulmo` (`git diff --name-status edx/release-ulmo HEAD -- '*/migrations/*.py'`): added `contentstore 0014`, `modulestore_migrator 0002`, `0003`, `0004`, `0006`, `survey_report 0006`, and five `*_mariadb_uuid_conversion` migrations (`student 0048`, `entitlements 0017`, `course_goals 0010`, `program_enrollments 0012`, `external_user_ids 0009`); deleted `announcements 0001_initial` (the app was removed upstream by `20bc7113e3`). `release/ulmo.1` and `release/ulmo.4` have identical migration file sets (`git diff --name-status release/ulmo.1 release/ulmo.4 -- '*/migrations/*.py'` is empty).

## Why not compare against a "from scratch" branch

`release-ulmo` contains two merged-then-reverted ulmo.1 attempts (#212, #304), so a plain merge of upstream into it silently omits all of ulmo.1, Django 5.2 included. The plan describes this "revert trap".

A history-independent merge (`git merge-recursive 242a69d06b -- edx/release-ulmo openedx/release/ulmo`) has **48 conflicted files**. Resolving them all by hand would duplicate the same judgement calls, by the same person, and diffing two hand resolutions mostly surfaces noise. Instead, those 48 files became the high-risk list for check 2, and check 1 provides a mechanical cross-check.

## Check 1: change survival (automated)

Method:

- Every non-merge commit since `242a69d06b` was checked: 469 fork-only commits (`242a69d06b..edx/release-ulmo ^openedx/release/ulmo`) and 157 upstream commits (`242a69d06b..openedx/release/ulmo`).
- Each commit was checked against two trees with `git apply --cached --reverse --check`: its own branch tip and the upgrade branch.
- A hunk is flagged only if it is still intact on its own tip but not on the upgrade branch. Comparing against the own tip filters out changes that later commits legitimately rewrote.
- This compares content, not SHAs, so security fixes landed independently in both repos are handled correctly.

Results:

- Commit level: fork 146 present, 286 superseded on the fork itself, 37 flagged; upstream 85 present, 43 superseded, 29 flagged.
- Hunk level: 102 file-level items flagged. This excludes the two expected ulmo.1 revert commits and the generated lockfiles (`requirements/edx/*.txt`, `scripts/user_retirement/requirements/*.txt`, `package-lock.json`), which are recompiled anyway.
- Every item was triaged by comparing the missing hunk with the branch's actual content.

Limitation: a change that merged cleanly into a **dead copy** of a moved file reads as present. A separate sweep listed files that are on the branch but not upstream (69). Exactly one is a dead copy: `xmodule/js/src/video/09_video_audio_description.js`. It is how the LP-1205 loss was found, and deleting it is a follow-up.

### Real losses (fixed)

| Source | What was lost | Fix |
|---|---|---|
| fork 353a5da311 (#461, LP-1205) | Audio-description flag removal merged into the dead old-path JS. The live JS still gated on `audioDescriptionEnabled`, which the backend no longer sends, so audio description would never have rendered. | `6732d6fd28` |
| fork fb8bc85234 / 43300f9143 | HLS fragment retry limit (old path). The merged spec also referenced `Hls` while the migrated spec imports `HLS`. | `687065c198` |
| fork 8fb361ba8a (#215) | Language menu max height. The CSS half survived; the JS half was on the old path. | `c56841c5e4` |
| upstream 28ab2ceb67 | studio-frontend removal: the Makefile `pull_translations` line survived. Upstream's own version leaves a dangling `\`; this branch keeps `$(ATLAS_EXTRA_SOURCES)` as the continuation line. | `91b8e72823` |

### Security fixes verified present and wired in

- **upstream 6291b6badd**, SAML metadata SSRF:
  - `validate_saml_metadata_url` runs before the fetch in `third_party_auth/utils.py` and `tasks.py`.
  - `SAML_METADATA_URL_ALLOW_PRIVATE_IPS` defaults to `False`.
  - The `samlproviderdata/views.py` hunk is moot: the fork removed those views (8b99c121f6).
- **upstream 21cead2384**, `activation_key` removed from the account API: not in the serializer, the views or `admin_fields`. The fork had ported this independently (6cc669fef1), and the regression test is present.
- **upstream 3a97ff2d5e**, no autogenerated username via SSO: `pipeline.py` checks `details.get('username') is None`.
- **upstream 46c543590c**, support course-team GET authz: `_caller_can_manage_course_team` is invoked at the top of `get`.
- **upstream c99de016f1**, Studio video-download bounds: streaming with `requests.get(..., stream=True)`, `VideoDownloadThrottle`, and `VIDEO_DOWNLOAD_RATE_LIMIT`.
- **upstream 2efdce0760**, stored-XSS sandboxing of course assets: an ancestor of the branch.
- **upstream 4d2e220d6f**, pdf.js 5.7.284: present, with the fork's guards re-added. See the PDF section.
- **`release-ulmo` #500**, the BigAutoField fix for the previous prod XBlockSaveError: present (`lms/djangoapps/courseware/fields.py`).

### Notable non-losses

- **fork 603faba462**, a revert of the "UserStateCache/FieldDataCache dynamic children" feature (cf11446cf5):
  - The reverted feature is **not** on the branch; none of its functions or templates exist.
  - The hunks only failed because the branch's `xmodule` files are upstream's newer versions (e.g. `ModuleStoreRuntime` renames).
  - The branch does carry the fork's **later** learner-state work, identical to the fork tip: 1aab795e29 / faa0930649 (incremental loading of large assessment xblocks) and 87abfc31fb (hydrating learner state for paginated assessment children: `courseware/model_data.py`, `block_render.py`, `vert_module_lazy_bootstrap.html`).
  - Given the previous XBlockSaveError, exercise this on devstack and stage.
- **fork 2f1e16dae6** (social-auth-core 4.7.0 shim): superseded. The branch pins social-auth-core 4.8.1 and uses the new signatures.
- **upstream 4c051378d0** (`thread.py`): superseded deliberately. The fork keeps its own soft-delete `_delete_thread` (`deleted_by`).
- **upstream 4953c12fbc / 72c23ac570 / dc7db1d3ad** (constraints): superseded. The fork's newer pins win (`edx-enterprise==8.17.0`, `pip<26.2.1`); social-auth-core is unpinned as upstream intended.
- **CI workflows** (upstream 272b1669c2, ba56c0aa2d, 6c7a95c7a0; fork 0c7b3cf6d6, 50c752ca78): combined. `merge_group:` and `setup-python@v6` are present, alongside the fork's `release-ulmo` push branches and its docker/mongo fixes.
- **upstream d87ba47643** (header Profile link): the branch uses `urljoin(PROFILE_MICROFRONTEND_URL, username)`. This is verified correct, because prod and stage set `PROFILE_MICROFRONTEND_URL` to `https://profile[.stage].edx.org/u/` (edx-internal `argocd/applications/edxapp-lms/{prod,stage}-config.yaml`).
- **fork fb3862279c** (default audio codec): combined. The branch's `02_html5_hls_video.js` sets `defaultAudioCodec`. Nothing reads it in either version, so the behaviour is unchanged.
- **fork 0a400fbd1c / 34bd6597b2** (hls.js 1.6.15): present (`package.json` `^1.6.15`, lock resolves 1.6.15). The only missing hunks are an `HLS`→`Hls` variable rename, which is cosmetic.

## Check 2: the 48 high-risk files

These are the files that conflict in a history-independent merge, classified by comparing the branch's blob with the fork tip and the upstream tip:

| Class | Count | Notes |
|---|---|---|
| Combined (differs from both) | 21 | Expected. |
| Identical to the fork (upstream edits not taken as text) | 15 | Discussion API and tests, progress API/template, `third_party_auth/pipeline.py`, comment client `thread.py`, `common_constraints.txt`. The June ulmo.3 merge kept the fork's version of these files, and later fork edits carried forward. Hunk-level triage (check 1) confirmed the upstream intent is present in the fork's versions (`send_signal_after_commit`, the cs_comments_service removals, backend progress calculation, the SSO username fix), or was deliberately replaced (soft-delete in `thread.py`). |
| Identical to upstream (fork edits superseded) | 4 | `pdf_viewer.html` (PDF decision), `third_party_auth/tasks.py` (a style-only fork change), the user_retirement lockfiles. |
| Absent, as intended | 8 | Moved video JS (5 files), pdf.js 1.0 `viewer.js`, the fork-deleted `create_user_gdpr_testing.py` (ENT-11576), and the SAML provider data views. |

## Check 3: landing on `release-ulmo`

- `edx/release-ulmo` (`0cfc2835be`) is an ancestor of the branch, so a PR merge is conflict-free.
- `git merge-tree --write-tree edx/release-ulmo robrap/lp1148-ulmo4-continue` produces the branch's own tree.
- Spot checks on that tree:
  - Django 5.2.11 (pre-bump)
  - BigAutoField fix present
  - pdf.js 5.7.284
  - openedx-forum 0.4.1
  - upstream `2efdce0760` reachable

## PDF textbook viewer

The fork (#51 `d97e9437c4`, #64 `1917f35a00`, #86 `b3b72587ba`) and upstream (`4d2e220d6f`, GHSA-mj74-gfq3-2v9f) fixed this incompatibly; the fork's postMessage design uses pdf.js 1.0 APIs. The chosen option and all the alternatives are in the commit message of `fc8f1f0a8c`. The branch differs from upstream only in:

- `lms/djangoapps/staticbook/views.py`: only relative URLs go into `file=`; absolute `https://` URLs render no viewer; dangerous chapter URL schemes are blanked.
- `lms/templates/static_pdfbook.html`: the chapter-click handler skips absolute http(s) URLs.
- `lms/djangoapps/staticbook/tests.py`: upstream's tests, with the absolute-URL assertions inverted, plus two guard tests.

## Appendix: scripts

Both scripts are read-only. Set `S` to a scratch directory, and run them from the repo root after `git fetch edx openedx`.

### `survival.sh`: commit-level check

```bash
#!/usr/bin/env bash
# For each commit: does its diff still reverse-apply on its own tip and on the branch?
# Output TSV: side sha status files_lost subject   (status: PRESENT | SUPERSEDED | LOST)
set -u
: "${S:?set S to a scratch dir}"
BASE=${BASE:-242a69d06b}
A=${A:-robrap/lp1148-ulmo4-continue}
FORK=${FORK:-edx/release-ulmo}
UP=${UP:-openedx/release/ulmo}

mkidx() { GIT_INDEX_FILE="$S/idx.$1" git read-tree "$2"; }
mkidx A "$A"; mkidx fork "$FORK"; mkidx up "$UP"

check() {  # $1=index name, patch on stdin; prints failing paths
  GIT_INDEX_FILE="$S/idx.$1" git apply --cached --reverse --check - 2>&1 \
    | perl -ne 'print "$2\n" if /^error: (patch failed: )?([^:]+)/' | sort -u
}

run() {  # $1=side label, $2=own index, rev-list args...
  local side=$1 own=$2; shift 2
  git rev-list --no-merges --reverse "$@" | while read -r c; do
    p=$(git diff --binary --full-index "$c^" "$c")
    [ -z "$p" ] && continue
    fa=$(printf '%s\n' "$p" | check A)
    subj=$(git log -1 --format=%s "$c" | cut -c1-100)
    if [ -z "$fa" ]; then printf '%s\t%s\tPRESENT\t\t%s\n' "$side" "${c:0:10}" "$subj"; continue; fi
    fo=$(printf '%s\n' "$p" | check "$own")
    lost=$(comm -23 <(printf '%s\n' "$fa") <(printf '%s\n' "$fo") | paste -sd, -)
    if [ -z "$lost" ]; then printf '%s\t%s\tSUPERSEDED\t\t%s\n' "$side" "${c:0:10}" "$subj"
    else printf '%s\t%s\tLOST\t%s\t%s\n' "$side" "${c:0:10}" "$lost" "$subj"; fi
  done
}

run fork fork "$BASE..$FORK" "^$UP"
run up up "$BASE..$UP"
```

Run it as `S=/tmp/lp1148 bash survival.sh > $S/survival.tsv`. It takes a few minutes.

### `hunks.py`: hunk-level detail for LOST items

```python
"""Print hunks that reverse-apply on their own branch tip but not on the branch.
Reads $S/survival.tsv and the $S/idx.* index files written by survival.sh."""
import os, re, subprocess, sys

S = os.environ['S']
SKIP_COMMITS = {'f67142decf', '3a8fdad2fd'}  # reverts of the ulmo.1 merges: expected
SKIP_FILE = re.compile(r'^(requirements/edx/.*\.txt|scripts/user_retirement/requirements/.*\.txt|package-lock\.json)$')

def git(*args, inp=None, env=None):
    return subprocess.run(['git', *args], input=inp, capture_output=True, text=True, env=env)

def applies(index, patch):
    env = dict(os.environ, GIT_INDEX_FILE=os.path.join(S, f'idx.{index}'))
    return git('apply', '--cached', '--reverse', '--check', '-', inp=patch, env=env).returncode == 0

def hunks_for(commit, path):
    diff = git('diff', '--full-index', f'{commit}^', commit, '--', path).stdout
    m = re.search(r'^@@', diff, re.M)
    if not m:
        return []
    header, body = diff[:m.start()], diff[m.start():]
    return [(header, p) for p in re.split(r'(?m)^(?=@@)', body) if p.strip()]

for line in open(os.path.join(S, 'survival.tsv')):
    side, sha, status, files, subj = line.rstrip('\n').split('\t')
    if status != 'LOST' or sha in SKIP_COMMITS:
        continue
    own = 'fork' if side == 'fork' else 'up'
    for path in files.split(','):
        if SKIP_FILE.match(path):
            continue
        hs = hunks_for(sha, path)
        if not hs:
            print(f'### {side} {sha} {path}  [file-level]  {subj}\n')
            continue
        lost = [h for hdr, h in hs if applies(own, hdr + h) and not applies('A', hdr + h)]
        if lost:
            print(f'### {side} {sha} {path}  ({len(lost)}/{len(hs)} hunks missing)  {subj}')
            print('\n'.join(h.rstrip('\n') for h in lost) + '\n')
```

### Dead-copy sweep

```bash
git ls-tree -r --name-only openedx/release/ulmo | sort > $S/up.txt
git ls-tree -r --name-only robrap/lp1148-ulmo4-continue | sort > $S/a.txt
comm -23 $S/a.txt $S/up.txt   # files on the branch but not upstream: fork features, or stale copies of moved files
```

Triage the output by hand. Anything under a directory that upstream moved, such as `xmodule/js/src/video/`, is a suspect.

### `lockfile_downgrades.py`: pins that went down

```python
#!/usr/bin/env python3
"""Report pinned packages that are LOWER on a merged branch than on the fork's deployed branch.

Read-only. Run from the repo root after fetching.
  python3 lockfile_downgrades.py edx/release-ulmo HEAD
  python3 lockfile_downgrades.py edx/release-ulmo HEAD requirements/edx/base.txt package-lock.json

Compares exact `name==version` pins in compiled requirements files, and the `packages` map of
package-lock.json. Version comparison is a simple numeric-segment key (no `packaging` dependency),
so pre-release suffixes may be misordered. It does NOT detect dropped constraints, and compares to
the fork branch's lockfiles, not to what is running in production images.
"""
import json, re, subprocess, sys

DEFAULT_FILES = [
    'requirements/edx/base.txt', 'requirements/edx/testing.txt', 'requirements/edx/development.txt',
    'requirements/edx/doc.txt', 'scripts/user_retirement/requirements/base.txt',
    'scripts/structures_pruning/requirements/base.txt', 'package-lock.json',
]


def vkey(v):
    out = []
    for p in re.split(r'[.\-+]', v):
        m = re.match(r'^(\d+)(.*)$', p)
        out.append((int(m.group(1)), m.group(2)) if m else (-1, p))
    return out


def show(ref, path):
    return subprocess.run(['git', 'show', f'{ref}:{path}'], capture_output=True, text=True).stdout


def load_requirements(ref, path):
    pins = {}
    for line in show(ref, path).splitlines():
        m = re.match(r'^([A-Za-z0-9_.\-]+)(\[[^\]]*\])?==([^\s;\\]+)', line)
        if m:
            pins[m.group(1).lower().replace('_', '-')] = m.group(3)
    return pins


def load_npm(ref, path):
    text = show(ref, path)
    if not text:
        return {}
    return {k: v['version'] for k, v in json.loads(text).get('packages', {}).items() if k and v.get('version')}


def main(base, head, files):
    for path in files:
        load = load_npm if path.endswith('package-lock.json') else load_requirements
        a, b = load(base, path), load(head, path)
        if not a:
            print(f'== {path}: absent or empty on {base}')
            continue
        changed = [k for k in a if k in b and a[k] != b[k]]
        down = sorted((k.split('node_modules/')[-1], a[k], b[k]) for k in changed if vkey(b[k]) < vkey(a[k]))
        print(f'== {path}: changed {len(changed)}, DOWNGRADED {len(down)}, '
              f'removed {len([k for k in a if k not in b])}, added {len([k for k in b if k not in a])}')
        for row in down:
            print('   DOWN', row)


if __name__ == '__main__':
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2], sys.argv[3:] or DEFAULT_FILES)
```

Run it from the repo root as `python3 lockfile_downgrades.py edx/release-ulmo HEAD`. It does not detect dropped constraints, and it compares lockfiles, not production images. Re-run it at every sync gate.
