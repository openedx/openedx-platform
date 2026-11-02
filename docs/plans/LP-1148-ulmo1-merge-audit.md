# Merge audit of the batch-1 branch `robrap/lp1148-ulmo1-batch1` (#514, ulmo.1)

Ticket: [LP-1148](https://2u-internal.atlassian.net/browse/LP-1148). Companion to `LP-1148-ulmo-upgrade.md` (the plan), delete together with it. Method and scripts: the appendix of `LP-1148-ulmo4-merge-audit.md`.

Audit date: 2026-10-09, at branch commit `4e4bb665ca` (the Django bump; before the PII safelist fix and these docs).

Inputs:

| Input | Ref | Commit |
|---|---|---|
| Fork | `edx/release-ulmo` | `0666f56778` |
| Upstream | `release/ulmo.1` (tag) | `ea91c4c4b6` |
| Pre-ulmo.1 common base | — | `242a69d06b` |
| Reference | #505, `robrap/lp1148-ulmo4-continue` | `be17bd60ca` |

## Summary

- The branch is `edx/release-ulmo` plus: the revert of the revert (`9e4844198b`, 13 conflicted files), the four ports from #505, the Django 5.2.18 bump, the PII safelist fix, and the video `public_view` fix from `edx/ulmo.3` (`e123921eb7`). Conflict resolutions are listed in the message of `9e4844198b`.
- **No merge losses beyond the four #505 already found and fixed** (the ports). Every other flagged hunk is present in combined form or deliberately superseded.
- Outside the 13 conflicted files, the branch's revert commit is **identical** to `010ea66478` (the May revert of the revert) plus `release-ulmo`'s changes since then.
- No production (`base.txt`) pin is downgraded. `openedx-authz` is `0.20.1`. The migration inventory matches the plan.

## Conflicts of the revert of the revert

13 files (the plan expected many). By class:

| Files | Resolution | Why |
|---|---|---|
| `.github/workflows/check-for-tutorial-prs.yml`, `create_user_gdpr_testing.py` | Keep deleted | Deliberate fork removals (#507, ENT-11576). |
| `xmodule/js/src/video/02_html5_hls_video.js` | Accept upstream's move | The fork's HLS retry change is re-applied at the new path by the port `3871216bf3`. |
| `cms/djangoapps/contentstore/views/course.py` | Combined | #509's LP-1102 staff branch first, then upstream's set-based org logic (`670c81f0f2`); drop imports of the toggles upstream removed. |
| `cms/djangoapps/modulestore_migrator/models.py` | Fork | Keep the `.. no_pii:` annotations (#352). Same as #505. |
| `test_content_libraries.py`, user_api `test_api.py`, `test_access.py` | Combined imports | Keep the fork's new imports (#509, #517, `RequestCache`) and upstream's `ZoneInfo`. `test_api.py` keeps `pytz` for #517's tests; `pytz` is still pinned. |
| `requirements/common_constraints.txt` | Fork | The fork's `pip<26.2.1` and social-auth constraints supersede ulmo.1's `pip<25.3`; the pip line was ulmo.1's only change to this file. |
| `requirements/edx/{base,development,doc,testing}.txt` | Both sides' newer pins | `lti-consumer-xblock` 9.14.3 (ulmo.1), `ora2` 6.17.2 (fork), and no `loremipsum` (needed only by ora2 6.17.1). |

## Cross-checks

| Compare | Result |
|---|---|
| Revert commit vs `010ea66478` plus `release-ulmo` drift (`git merge-tree --merge-base 08d667b9c7 010ea66478 edx/release-ulmo`) | Same 13 conflicted files; every other file is identical. |
| Branch vs #505 (`be17bd60ca`) on the 13 files | Same resolutions where #505 had the file (`models.py`, `test_access.py` apart from import order, constraints, the lockfile pins). `course.py`, `test_content_libraries.py`, `test_api.py` and the tutorial workflow differ only by #507, #509 and #517, which #505 does not have. |
| Port files vs #505 | Identical. |
| History-independent merge (`git merge-tree --merge-base 242a69d06b edx/release-ulmo release/ulmo.1`) | 39 conflicted files (48 at ulmo.4+). On the branch: 18 combined, 14 identical to the fork (the same discussion, progress, `pipeline.py`, `thread.py` and constraints files that #505's audit triaged), 7 absent as intended (the moved video JS, the tutorial workflow, `create_user_gdpr_testing.py`). |
| `edx/edx/ulmo.3` | Its fixes not in upstream `release/ulmo.3` or `release-ulmo`: `94963dbae2` (upstream #38012, video `public_view` loads the nonexistent bundle `VideoBlockMain`; a ulmo.1 regression, never backported to `openedx/release/ulmo`) is **taken** as `e123921eb7`. `efdb407b39` ("Clean up some merge errors") does not apply: its `third_party_auth/utils.py` fix is for `fetch_metadata_xml`, which the fork removed, and its `pylint` disable and query counts are already on the branch, as is `771c25ec54` ("Update queries expected"). `582e345108` (temporary SAML SSRF revert) is not applicable. |

## Check 1: change survival

`survival.sh` with `A=4e4bb665ca`, `UP=release/ulmo.1`: fork 150 present, 294 superseded, 34 flagged; upstream 66 present, 36 superseded, 30 flagged. Each flagged hunk was then checked against #505's tree:

- 88 lost hunks are also missing on #505 and were triaged in #505's audit (old-path video JS, superseded constraints and CI, the fork's soft-delete `thread.py`, and so on).
- 7 hunks are missing here but present on #505, and the hunks of #509 and #517 (not in #505) were checked by hand. All are context-only: the change is present, but a neighbouring line differs because of #507, #509, #517 or an upstream import removal.

Dead-copy sweep (files on the branch but not in `release/ulmo.1`): 75 files, the same list as #505 apart from the plan files and the fork's `lms/djangoapps/lti_provider/README.rst` (LTI nonce fix, later also upstream). The only dead copy is the known `xmodule/js/src/video/09_video_audio_description.js`.

## Lockfiles

`lockfile_downgrades.py edx/release-ulmo HEAD`:

- `base.txt`, `doc.txt`: no downgrades. Additions: `openedx-authz` 0.20.1, `pycasbin`, `casbin-django-orm-adapter`, `simpleeval`, `bracex`, `wcmatch`.
- `testing.txt`, `development.txt`: `pact-python` 2.3.3 → 1.6.0. Test-only. Upstream ulmo.1, the May build and #505 all pin 1.6.0; it comes from resolving ulmo.1's dependencies, not from a merge error.
- `package-lock.json`: `globals` 11.12.0 → 9.18.0 and `regenerator-runtime` 0.13.11 → 0.10.5 at the top level. These are hoisting changes, identical to upstream ulmo.1: the nested copies that needed the newer versions are gone.

## Merge-loss fixes from #505 that apply to ulmo.1

- The four ports: all apply (cherry-picked unchanged).
- `.annotation_safe_list.yml` entry for `oel_publishing.PublishableEntityVersionDependency` (`feac70ecd4`): **applies**, because openedx-learning 0.30.2 is in ulmo.1, not only ulmo.4. Ported as `1af9d598aa`.
- pycodestyle E302/E303 fixes: not needed. `test_extract_archive.py` is not in ulmo.1, and `pycodestyle .` (2.8.0, as pinned) passes on the whole tree.

## CI

Green at `91ee0858ac` (2026-10-09).
