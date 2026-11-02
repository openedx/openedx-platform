# Landing the ulmo upgrade (Django 5.2) on `release-ulmo`, in batches

Tickets: see [Jira tickets](#jira-tickets). Epic: [LP-1309](https://2u-internal.atlassian.net/browse/LP-1309) ("edxapp Ulmo upgrade: production deployment (batches)").

This plan and the audit files next to it live in the **batch-1 PR** [#514](https://github.com/edx/edx-platform/pull/514) (branch `robrap/lp1148-ulmo1-batch1`) and are the single copy. They were moved here from the superseded reference PR [#505](https://github.com/edx/edx-platform/pull/505). Keep them up to date in whichever PR is current, and delete them in a small follow-up once the upgrade is in production and stable; see `docs/plans/README.rst`.

Related: the upgrade-process playbook in edx-internal ([PR #14962](https://github.com/edx/edx-internal/pull/14962), `docs/openedx-upgrade-process/`). It holds the reusable method; this plan is the concrete execution.

## Start here (handoff)

Written 2026-10-08 and updated 2026-10-09 so that a fresh session can continue from this file alone. State at that time:

- `edx/release-ulmo` tip was `432ae28549` (2026-10-30), merged into the branch (see [Sync log](#sync-log)). Production runs `release-ulmo` on Django `4.2.28`.
- **Batch 1 = ulmo.1 only.** The code is on the branch (2026-10-09): the revert of the revert `9e4844198b`, the four ports, the Django 5.2.18 bump `4e4bb665ca`, the PII safelist fix `1af9d598aa`, and the video `public_view` fix `e123921eb7`. All steps of [Merge work and audit (batch 1)](#merge-work-and-audit-batch-1) are done (the QA handoff is being done by the LP-1148 owner directly); results are in `LP-1148-ulmo1-merge-audit.md`. LP-1148's work is complete.
- **Next action:** QA (LP-1351), with the [final syncs with `release-ulmo`](#final-sync-with-release-ulmo) until testing starts. CI status is read live from the PR (in the GitHub UI, or with `gh pr checks 514`); it is not recorded here. The cross-check against `edx/edx/ulmo.3` is done and found a ulmo.1 regression, now fixed on the branch (`e123921eb7`, video `public_view` bundle). The migration review is done (see [Migration state in prod, edge and stage](#migration-state-in-prod-edge-and-stage)): **no manual migration steps are needed in any environment** for the forward deploy (for a rollback, see [Deploy and rollback](#deploy-and-rollback-lp-1308)). The dependency-migration comparison on the batch-1 `base.txt` is done and matches (`openedx-authz` is `0.20.1`).
- **Batch 2** is some set of changes after ulmo.1 on `openedx/release/ulmo`. How much goes in it is not decided; see [Batch 2](#batch-2-scope-open).
- #505 (`robrap/lp1148-ulmo4-continue`) is a superseded reference branch, closed; its docs are to be removed (see [What happens to #505](#what-happens-to-505)).
- **Pending housekeeping:**
  - Remove the docs copy from #505's branch and mark its description "superseded, reference only".
  - Set the "Repo" (`edx/edx-platform`) and team fields on LP-1351 by hand in Jira; they could not be set through the API.
  - LP-1351 is not in a sprint (LP-1308 is in "Q4 Sprint 2", starting 2026-10-12, whose goal is "Deploy edxapp Ulmo.1").
  - Check the edx-internal playbook PR (#14962) for claims this plan corrected: "five days in prod", "ulmo.3 was deployed", "rolling back code never un-applies migrations".
  - LP-1148 and LP-1351 are not linked to each other; the handoff step covers it. Add a link if wanted.
- Nothing about the decisions below lives only in a conversation or in notes outside this PR: this plan is the only record, and no memory notes were kept for it. If something here looks wrong, check it against git (every claim cites commits).

## Jira tickets

All ticket text and these sections say "edxapp" so it is clear what is being upgraded. Tickets point here and do not duplicate it.

| Ticket | Owns | Plan section |
|---|---|---|
| [LP-1309](https://2u-internal.atlassian.net/browse/LP-1309) (epic) | The plan is complete: every section has an owning ticket or an explicit "not needed". Closing items: delete these docs, feed the playbook. | whole plan |
| [LP-1148](https://2u-internal.atlassian.net/browse/LP-1148) | Preparing the branch: merge work, audit, cross-checks, CI, read-only DB migration verification, handoff to QA | [Merge work and audit](#merge-work-and-audit-batch-1) |
| [LP-1351](https://2u-internal.atlassian.net/browse/LP-1351) | QA of the candidate branch: reviews, owner decisions, testing | [QA and testing](#qa-and-testing-lp-1351) |
| [LP-846](https://2u-internal.atlassian.net/browse/LP-846) | Proctortrack validation on a sandbox (part of QA). It was written as "Ulmo.3"; the build that ran in prod was ulmo.1. | QA |
| [LP-1304](https://2u-internal.atlassian.net/browse/LP-1304), [LP-1150](https://2u-internal.atlassian.net/browse/LP-1150), [LP-1326](https://2u-internal.atlassian.net/browse/LP-1326) | Blockers; see [Blockers](#blockers) | QA, deploy |
| [LP-1308](https://2u-internal.atlassian.net/browse/LP-1308) | Deploy and rollback | [Deploy and rollback](#deploy-and-rollback-lp-1308) |
| Batch 2 | No ticket yet. Create one when the scope is decided. | [Batch 2](#batch-2-scope-open) |

[LP-1305](https://2u-internal.atlassian.net/browse/LP-1305) ("Deploy ulmo.3 using release-ulmo to production") was closed as a duplicate of LP-1308; its contents were moved into the tickets above and this plan.

## What is verified and what is not

Written 2026-10-08, updated 2026-10-09. This plan has no tests behind it; the claims come from read-only git, `gh` and Jira checks, plus reading the edx-internal playbook PR. Re-check before relying on any of them.

- **Verified from git (cite commits; cheap to re-run):** the deploy/revert history table; that `e45e7825ea` contains upstream `release/ulmo.1` and not ulmo.2/.3/.4, and pins Django 5.2.7; that `010ea66478` is the revert of `3a8fdad2fd` and is not in `release-ulmo`; the 3-file difference between `010ea66478` and `e45e7825ea`; that the ulmo.1-range breaking commits listed are ancestors of `release/ulmo.1` (and `fca21c955f`, `4d2e220d6f` are not); that the asset-sandbox fix exists on `release-ulmo` as `723014f37b` (#481) with the same flag; the dependency pins at ulmo.1, ulmo.4 and `release-ulmo`.
- **Verified on the batch-1 branch (2026-10-09):** the revert of `3a8fdad2fd` conflicts in 13 files; the survival audit, cross-checks with `010ea66478` and #505, lockfile and migration checks. See `LP-1148-ulmo1-merge-audit.md`.
- **Verified from content (2026-10-09):** for every ulmo.2/3-only change, the file contents at `e45e7825ea` match "lacks the change" or "already has it as a fork backport"; see [Prod deploy evidence](#prod-deploy-evidence).
- **Verified from the deploy record (2026-10-09):** every prod edxapp deploy commit in edx-internal since 2026-02-10 (361 distinct image tags for LMS, CMS and the LMS kafka consumer) is a commit on `release-ulmo` containing no ulmo.2/3 commit. Prod never ran ulmo.2 or later. See [Prod deploy evidence](#prod-deploy-evidence).
- **Weakly verified (needs re-running on fresh refs before use):** the "already in `release-ulmo`" vs "not in" split in the post-ulmo.1 table. It used exact `git patch-id` matches against the last 400 `release-ulmo` commits, so a modified cherry-pick shows as "not in", and the refs were last fetched 2026-10-08. The claim that post-ulmo.1 fixes target code `release-ulmo` already has was judged from commit messages only, not code, and the ulmo.2/.3/.4 release notes were not read.
- **Not checked at all:**
  - Whether #481 (the asset-sandbox fix) is **deployed**. It is merged on `release-ulmo`; the edx-internal prod deploy commits (see the evidence section) could answer this.
  - Whether the `010ea66478` resolutions are semantically right. Batch 1 reproduces them exactly outside its 13 conflicted files, and the survival audit shows nothing lost, but neither proves the combined code behaves correctly; that is for CI and QA (hours in prod is weak evidence).
  - **How long the ulmo.1 build really served prod.** The sources disagree; see [May 27 timeline: open discrepancy](#may-27-timeline-open-discrepancy). (The migration state of prod, edge and stage is now known; see [Migration state](#migration-state-in-prod-edge-and-stage).)
  - The edx-internal playbook claims (open question 4, principle 2, ADR 0001) were read on branch `robrap/openedx-upgrade-process` of PR #14962 and may have changed.

## Where things are

| What | Ref |
|---|---|
| Batch-1 branch (this PR, [#514](https://github.com/edx/edx-platform/pull/514)) | `robrap/lp1148-ulmo1-batch1` on `edx/edx-platform`, last synced with `edx/release-ulmo` `432ae28549` |
| Deploy branch | `edx/release-ulmo` |
| Upstream | `openedx/release/ulmo` (tags `release/ulmo.1` 2026-01-15, `.2` 2026-02-18, `.3` 2026-04-24, `.4` 2026-07-13; tip `2efdce0760`) |
| Reference branch #505 | `robrap/lp1148-ulmo4-continue` @ `be17bd60ca`, draft PR [#505](https://github.com/edx/edx-platform/pull/505). ulmo.3 base plus ulmo.4+ merge. Source of ports, the 4-loss audit, and a second opinion on conflicts. |
| `edx/edx/ulmo.1` | `010ea66478`, the revert of the revert of the May deploy (see [History](#history-what-was-deployed)). |
| `edx/edx/ulmo.3` | Someone's test branch: ulmo.1 re-applied + ulmo.2 + ulmo.3. Tip dated 2026-06-23, after the May incident. Never landed. Not proven. Reference only. |
| Audits | Batch 1: `LP-1148-ulmo1-merge-audit.md`. #505: `LP-1148-ulmo4-merge-audit.md` (holds the scripts). Both in this directory. |
| Playbook | edx-internal PR #14962, `docs/openedx-upgrade-process/` |
| Deploy record | edx-internal commits "Deploy edxapp-lms to prod: app image lms-prod-<sha>-<build>" (`git log` on master) |
| edx-internal locally | A clone at `../other/edx-internal` (relative to this repo); migrations and rollback are defined in `gocd/generated-pipelines/templates/edxapp.yaml.j2` and `argocd/applications/edxapp-migrations/` |

## History: what was deployed

All of this is ordinary linear history on `release-ulmo`; nothing was force-pushed.

| Date | Commit | What |
|---|---|---|
| 2026-05-19 | `980550c446` | Merged #212 (ulmo.1). |
| 2026-05-19 | `f67142decf` | Reverted it (#300): casbin migration error and h5p xblock issue. |
| 2026-05-22 | `e45e7825ea` | Merged #304 (head `2b5f74b19e`, branch `edx/ulmo.1`). Its head contained `0972c91dc4`, a revert of the revert, so the content came back. Pinned `django==5.2.7`; contains upstream `release/ulmo.1` and nothing later (ulmo.2/.3/.4 are not ancestors). **Merged on 2026-05-22 but the deploy was held for testing.** |
| 2026-05-27 | `3a8fdad2fd` | Reverted it (#311, merged 16:35 ET): "errors on prod involving XBlocks". |
| 2026-05-29 | `010ea66478` | Revert of that revert, on top of `release-ulmo` `08d667b9c7`. Now the tip of `edx/edx/ulmo.1`. **Not in `release-ulmo`.** Differs from `e45e7825ea` in 3 files only (unrelated #310/#312 config). Does not have #500. |

- **Only ulmo.1 has ever run in prod, and only on 2026-05-27.** Ulmo.2 and later never have. The earlier idea that ulmo.3 was deployed is wrong; LP-846 was written as "Ulmo.3" but described the ulmo.1 build.
- **Root cause of the XBlockSaveError:** `UnsignedBigIntAutoField` (the `StudentModule` primary key) reported `AutoField` as its internal type. Django 5.2's `IntegerFieldOverflow` check silently dropped lookups for ids above 2147483647, and saving student state failed with "Forced update did not affect any rows". Fixed upstream (#39181) and on `release-ulmo` (#500, `0cfc2835be`). This is the one bug found in May, and it was in upstream-adjacent code, not a conflict resolution. Hours of prod exposure is weak evidence that the original merge resolutions are right.

### Prod deploy evidence

Gathered 2026-10-09 from the edx-internal commit history (times UTC; ET is UTC-4).

| Prod LMS image tag | Set at | What it is | Django |
|---|---|---|---|
| `lms-prod-b81ac43e51-7917` | 2026-05-22 17:48 | Last commit before the ulmo.1 merge | 4.2.28 |
| `lms-prod-e45e7825ea-7922` | 2026-05-27 15:43 | **The ulmo.1 merge (#304)** | **5.2.7** |
| `lms-prod-eff8e6a132-7923` | 2026-05-27 23:25 | The revert of #304 (#311) | 4.2.28 |
| `lms-prod-df5c3e473b-7924` | 2026-05-28 | #310 | 4.2.28 |
| `lms-prod-08d667b9c7-7925` | 2026-05-29 | #312 | 4.2.28 |

- CMS and the LMS kafka consumer follow the same pattern (15:42 on 2026-05-27 for `e45e7825ea-7922`, 23:23 for `eff8e6a132-7923`).
- Builds `e45e7825ea-7918` to `-7921` were **stage-only** rebuilds (stage 2026-05-22 to 2026-05-26); they never went to prod.
- **All** prod edxapp deploy commits from 2026-02-10 to 2026-10-09 (1,017 commits, 361 distinct tags) are commits on `release-ulmo` that contain none of the ten ulmo.2/3-only commits. Only deploys recorded in this commit format are covered (not other services).
- A Datadog check (metric `trace.django.request.hits`, `version` tag, prod) showed `e45e7825ea` serving from about 11:48 to 19:30 ET on 2026-05-27, which matches the manifest times above (15:43 to 23:25 UTC).
- Content checks at `e45e7825ea`: Django `5.2.7` (ulmo.3 has `5.2.11`); the SAML SSRF fix, library_content transformer, LTI logging and docs fixes are absent; the survey redirect, discussion email and `activation_key` removals are already present as fork backports; `openedx/features/announcements` is removed (still present on `release-ulmo`); `UnsignedBigIntAutoField` lacks the #500 fix (present on `release-ulmo`).

### How to check what was in prod, and when (versus what was in git)

**Move this to the general upgrade playbook** (edx-internal PR #14962, `01-playbook.md`) later, and use it when doing RCAs on any required rollback. A change can sit merged in git and deployed to stage for days before it reaches prod (here: #304 merged 2026-05-22, on stage 2026-05-22 to 05-26, on prod only 2026-05-27), so the merge date and "it was on stage" say nothing about when prod ran it. Ticket titles and plans written from memory were wrong about this too (LP-846 said ulmo.3; this plan said "five days in prod").

Sources, from strongest to weakest:
1. **The edx-internal deploy commits.** Every deploy is a commit on `edx/edx-internal` `master` named like `Deploy edxapp-lms to prod: app image lms-prod-<sha>-<build>, nginx sidecar prod-<sha>-<build>`. `<sha>` is the edx-platform commit; `<build>` is the CI build number. The same commit is made per environment (`prod`, `stage`, `edge`) and service (`edxapp-lms`, `edxapp-cms`, `edxapp-lms-kafka-consumer`). The commit timestamp is when the manifest changed, not when the rollout finished. List them with:
   `gh api -X GET repos/edx/edx-internal/commits -f since=<ISO> -f until=<ISO> -f per_page=100 --paginate --jq '.[] | select(.commit.message | test("^Deploy edxapp-(lms|cms|lms-kafka-consumer) to prod")) | "\(.commit.committer.date) \(.commit.message | split("\n")[0])"'`
   Times are UTC.
2. **Datadog metrics.** `trace.django.request.hits` grouped by the `version` tag (filter by prod `env` and `service`). It shows what actually served requests and is independent of git; metrics carry a limited set of attributes (the `version` tag is one of them), and a long window has too many versions to read or copy out, so use a short window around the deploy. Logs and traces expire after about two weeks, so save evidence early.
3. **The incident write-up.** Useful for what people saw, but check its times against sources 1 and 2 (here it disagreed; see the discrepancy below).

To map a deployed tag to content: take `<sha>`, then (a) `git merge-base --is-ancestor <sha> edx/release-ulmo`; (b) check whether any commit of interest is an ancestor of it; (c) compare the files, not only ancestry, since cherry-picks and fork backports defeat ancestry (`git show <sha>:<path>`, `git grep <pattern> <sha> -- <path>`; for a file, compare the blob at `<sha>` with the blob just before and just after the upstream change); (d) compare pins (`git show <sha>:requirements/edx/base.txt | grep -i '^django=='`). Use `git show`/`git grep` on a commit instead of checking it out.

Pitfalls found:
- Several builds can share one `<sha>` (stage rebuilds `-7918` to `-7921`); only the build number tells them apart.
- Stage and prod are different manifest commits for the same tag; check the `prod` lines.
- A rollback done out-of-band (not through a manifest commit) will not appear in source 1.
- zsh reads `"$sha:requirements/..."` as a modifier; write `"${sha}:requirements/..."`.

### May 27 timeline: open discrepancy

The sources disagree on how long the ulmo.1 build served prod:
- The edx-internal manifests and Datadog say about 7.7 hours (11:43 to 19:25 ET).
- The incident page (Confluence, "AU-2968/BOMS-621") says: migrations 15:40-15:45 UTC, code deploy 15:46-15:52, **code rollback 16:39-16:45, migrations rollback 16:47-17:06**. No prod commit in edx-internal changes the image between 15:43 and 23:25 UTC.
- LP-1326 says "reverted ~40 minutes later"; LP-846 said "live for approximately one hour".

What the databases add (2026-10-09): on prod and edge the May migrations ran at 15:43 UTC, and afterwards the reversible ones are gone while `casbin_adapter`, `openedx_authz` and `submissions 0006` remain, which supports the incident page's migration rollback around 16:47-17:06 UTC. It does not tell us when the code was rolled back, and it does not explain why the manifests and Datadog still show the ulmo.1 image until 23:25 UTC. One possibility: a rollback done outside the manifest commits (for example in the deploy tool) while the `version` tag lagged.

This no longer blocks anything: the migration state of every environment is now known (see [Migration state in prod, edge and stage](#migration-state-in-prod-edge-and-stage)) and needs no manual steps. The timeline is still unreconciled, and reconciling it is a task under the QA ticket (LP-1351) if someone wants the full incident story.

### The revert trap (important for anything that merges into `release-ulmo`)

A revert of a merge works. The trap comes afterwards: once the ulmo.1 merge is reverted, git treats those commits as already merged, so a plain merge of `openedx/release/ulmo` (or anything built on ulmo.1) brings only what came **after** ulmo.1 and silently leaves out all ulmo.1 content, including Django 5.2. The only way back is a revert of the revert, which is what #304 and `010ea66478` did. If batch 1 lands and is reverted again, the trap comes back and the next attempt needs another revert of the revert.

Once ulmo.1 is genuinely in `release-ulmo`, the trap is gone and later batches are plain merges of upstream tags.

## Decisions

| Decision | Choice |
|---|---|
| Batching | Small batches over one big catch-up (edx-internal ADR 0001). Point releases are **not** assumed safe: each batch gets its own risk review. |
| Batch 1 | **ulmo.1 only**, the only point that has run in prod. Built as: fresh branch from the `release-ulmo` tip, `git revert 3a8fdad2fd` (the revert of the revert), the ports below, the Django bump. Does **not** use `edx/ulmo.3` as a base. |
| Batch 2+ | Some set of changes after ulmo.1 on `openedx/release/ulmo`, merged into `release-ulmo` once batch 1 has landed. **How much goes in a batch is undecided**: it could be ulmo.2 only, up to the upstream tip. Do this before starting verawood. |
| Django | Batch 1 is on upstream's 5.2.7; bump to **5.2.18** (security release) as its own commit so it is easy to drop. Use the `upgrade-one-python-dependency.yml` workflow (it produced #506 for #505); do not run `make upgrade`. Also take **5.2.19** (security release due 2026-11-03, about 15:30 UTC; 6 low and moderate fixes) the same way, even if upstream `release/ulmo` does not take it. It comes out after the 2026-11-02 stage test, so decide then whether it goes in before the prod deploy and how much to retest. |
| Cross-checks | Treat `e45e7825ea` / `edx/ulmo.1`, `edx/ulmo.3` and #505 as second opinions, never as truth. See [Cross-checks and the disagreement rule](#cross-checks-and-the-disagreement-rule). |
| Rollback | Redeploy the previous image quickly for any unknown issue, and pause the pipeline. Revert or fix forward is a separate, slower decision; re-landing after a revert needs a revert of the revert. Migration rollback is manual and skipped by default. See [Deploy and rollback](#deploy-and-rollback-lp-1308). |
| Branching (long term) | Keep `release-ulmo` as the deploy branch through the ulmo catch-up. Then cut `release-verawood` **from `release-ulmo`** (not from upstream) and merge `openedx/release/verawood` into it in batches; revisit `master` only after reaching verawood parity. While catching up, watch `openedx/release/verawood` for security/bug fixes and cherry-pick urgent ones (as #319, #333 and #481 were). This is open question 4 in the edx-internal playbook; recommend it there. |
| Testing | See [QA and testing](#qa-and-testing-lp-1351). The notes there support testing; they are not a prescribed procedure. Where something is tested depends on where it can be tested. |
| Docs | In the batch-1 PR only. Later batches carry them forward. |
| Branch naming | `robrap/` prefix. |

## What happens to #505

#505 was built on ulmo.3 with a single ulmo.4+ merge. It cannot sensibly be batch 2: after batch 1 lands, its ulmo.3-based history would conflict heavily, and a fresh merge of the upstream tags is simpler. It stays as a **reference**:

1. Docs moved to this PR. #505's branch removes its copies in a follow-up commit and its title/description say "superseded, reference only", pointing here.
2. References were repointed (edx-internal#14962, LP-1148, LP-1309).
3. **#505 is closed.** Do not delete its branch until batch 2 no longer needs it as a reference.

#505's reusable content: the ports (`6732d6fd28`, `687065c198`, `c56841c5e4`, `91b8e72823`), the merge audit and its scripts, the fork's PDF-viewer analysis (commit message of `fc8f1f0a8c`, for batch 2), `contentstore 0014` analysis, and its conflict resolutions.

## Merge work and audit (batch 1)

Ticket: LP-1148. This is the meaty work. The migration review (step 9) is done on the data side and did not change the branch plan. Do the merge in a throwaway worktree first to measure the conflicts, then on the branch.

**Status (2026-10-09): steps 1-9 done**; step 10 (handoff to QA) open. Done as: revert, ports and bump committed on a throwaway worktree based on `edx/release-ulmo`, then `edx/release-ulmo` merged into the branch (`65e7a211c7`) and those commits cherry-picked on top, so the branch tree is the worktree tree plus these docs. Keep the steps below for re-doing the work if `release-ulmo` diverges badly.

1. **Sync.** `git fetch edx openedx --tags`. Base is `edx/release-ulmo`; if it moved, merge it so the tested tree is the tree that lands.
2. **Revert the revert.** `git revert -m1 3a8fdad2fd` on the branch. On `0666f56778` this conflicted in only 13 files; the resolutions are in the message of `9e4844198b` and the audit file. Resolve using, in this order of preference: the fork's own intent (divergence registry in the playbook, `03-...registry`), then the resolutions already made in `010ea66478` and, for the same files, in #505's `fef5916109` (merge of `release-ulmo`) and `fc8f1f0a8c`. Do not take a side wholesale: do not use `git checkout --theirs/--ours` on a whole file without diffing that side against the merge base, and before committing run `git diff --name-only --diff-filter=U` and `git status` to confirm every hand-edited file is staged. (Both mistakes happened once in #505 and were caught.)
3. **Ports** (video JS moved from `xmodule/js/src/video/` to `xmodule/assets/video/public/js/` in ulmo.1, so fork changes to the old paths are not carried by git): cherry-pick or redo `6732d6fd28` (LP-1205 audio-description flag removal, #461), `687065c198` (HLS fragment retry limit and its spec), `c56841c5e4` (language menu sizing, #215 JS half), `91b8e72823` (drop the studio-frontend translations pull from the Makefile; upstream `28ab2ceb67` is in ulmo.1). Check whether `release-ulmo` gained any other fork changes to old-path video files since May (`git log 010ea66478..edx/release-ulmo -- xmodule/js/src/video xmodule/assets/video`; at last look: `353a5da311`, `43300f9143`, `fb8bc85234`). Delete the dead RequireJS copy follow-up later.
4. **Take only the audit's merge-loss fixes that belong to the ulmo.1 range.** Skip the pycodestyle E302/E303 fixes (`test_extract_archive.py` / `test_videos.py`): ulmo.4-only, and `pycodestyle .` passes on the branch. The `.annotation_safe_list.yml` entry for `oel_publishing.PublishableEntityVersionDependency` is **not** ulmo.4-only, as this plan first said: openedx-learning 0.30.2 is already in ulmo.1, so it was ported (`1af9d598aa`). The `xmodule/` CODEOWNERS fix in #505 was for upstream's community owners; check whether ulmo.1 does the same.
5. **Django bump** to 5.2.18 as its own commit (see Decisions). Done as a hand edit of the six `django==` pin lines, the same lines the workflow changed for #505 (`d408f09dbf`); a patch release changes no dependencies. Rerun it through the workflow if an exact workflow-generated commit is wanted.
6. **Audit.** Re-run the audit scripts (appendix of `LP-1148-ulmo4-merge-audit.md`: `survival.sh`, `hunks.py`, the dead-copy sweep, `lockfile_downgrades.py`) with the ulmo.1 range: the pre-ulmo.1 common base `242a69d06b`, upstream `release/ulmo.1`, fork `edx/release-ulmo`. Write results in a new `LP-1148-ulmo1-merge-audit.md`. Also run the history-independent merge (`git merge-tree --write-tree --name-only --merge-base 242a69d06b edx/release-ulmo release/ulmo.1`; 39 conflicted files for ulmo.1, 48 at ulmo.4+) as the independent reference. Also, on the batch-1 `base.txt`: confirm `openedx-authz` is `0.20.0` or `0.20.1` (both end at migration `0006`; `0.21.0` and later add a seventh, so review it if the version is newer), and repeat the dependency-migration comparison from [Migration state](#migration-state-in-prod-edge-and-stage).
7. **Cross-check** per the next section.
8. **CI.** Fix failures as separate commits. Many jobs pull images from Docker Hub and can fail on its unauthenticated rate limit ("toomanyrequests"), and one failing shard cancels its siblings; check the log before debugging, and use `gh run rerun <run-id> --failed` once the run finishes. commitlint is not a required check but does run: every commit subject needs a conventional-commit type, including the revert of the revert (git's default `Reapply "..."` fails; use `revert: ...`). Pay particular attention to `lms/djangoapps/courseware/tests/test_fields.py`, the video tests, query-count tests (ulmo.3 needed "Update queries expected"), `make lint-imports`, migrations checks and `makemigrations --check --dry-run` for lms and cms. `make check_keywords` runs after `pii_check` in the same job. Also re-check `.github`: in #505 all differences from `release-ulmo` were modifications (mostly action version bumps), no added or removed workflows, and the `django-version: "5.2"` matrix leg of `unit-tests.yml` was dropped since `pinned` is now 5.2, so the "dj=pinned" jobs are the Django 5.2 tests.
9. **Migration state** (below): done. A manual `show_unapplied_migrations` against a stage snapshot was planned and then dropped as redundant: its output is the code's migrations minus the applied rows, and both are known (the branch's migration list matches the plan, and each environment's applied state was queried). The `check migrations` CI job covers the migration graph, and the deploy runs `show_unapplied_migrations` itself; see the review item in [Deploy and rollback](#deploy-and-rollback-lp-1308).
10. **Handoff to QA.** (2026-10-09: the QA section was updated with the audit, cross-check and regression-check findings; the LP-1148 owner is doing the handoff directly.) When the migration review is done and the branch is ready for testing, update [QA and testing](#qa-and-testing-lp-1351) with anything the audit, cross-checks or migration review found (things to add or remove), and confirm the QA ticket (LP-1351) still matches it. Then push, open or update the batch-1 PR (draft) with these docs.

### Cross-checks and the disagreement rule

Comparisons to run (record results in the batch-1 audit file):

| Compare | Expectation |
|---|---|
| candidate vs `e45e7825ea` (what ran in May) | Every difference explained by `release-ulmo` drift, #500, the ports, or the Django bump. Anything else is a disagreement. |
| candidate vs `010ea66478` | Same, plus the revert-of-revert resolutions. |
| candidate vs #505 (`be17bd60ca`) | Differences should be only the upstream ulmo.1 → `2efdce0760` delta, the pdf.js/ulmo.4 changes, docs, and Django 5.2.11/5.2.18. |
| candidate vs `edx/edx/ulmo.3` | Differences = ulmo.2/3 content plus whatever ulmo.3's author fixed ("Clean up some merge errors", "Update queries expected"); each such fix is a hint to check, not an instruction. **Done 2026-10-09:** `94963dbae2` (upstream #38012) fixes a ulmo.1 regression and was taken; the other fixes do not apply (see the audit file). |
| candidate vs the history-independent merge | The independent reference; its conflicted files (39 for ulmo.1) are the high-risk list. |
| Lockfile downgrade check | No production (`base.txt`) downgrades against `release-ulmo`; investigate anything. |
| Batch 2 later (merge from `openedx/release/ulmo` into `release-ulmo`) vs #505's tree | A nice consistency check, not a pass/fail: merge history differs so trees need not be identical. |

**Disagreement rule.** None of the references is proven, and `e45e7825ea`/`edx/ulmo.1`, `edx/ulmo.3` and #505 are not independent of each other (ulmo.3 and #505 inherit the May resolutions). For each differing hunk, decide who got it right:
- Classify it as upstream intent or a deliberate fork-only change (use the divergence registry and `git log` on the fork side).
- A fork-only change wins unless upstream removed the feature deliberately.
- Break ties with tests/CI, how the code behaved in prod in May, and the owner of the fork PR.
- Record each ruling (hunk, who was right, why) in the audit file.

### Ulmo.1-regression check

Not every post-ulmo.1 upstream commit is safe to skip or take. Before finalizing batch 1, read the ulmo.2, ulmo.3 and ulmo.4 release notes and grep post-ulmo.1 commit bodies for "regression", "fixes #" and "fixes ulmo.1", and ask whether any fixes a bug **introduced by** ulmo.1 that `release-ulmo` would not have.

**The first pass ("none found", by commit message only) was wrong.** Upstream `5c759f1e13` (the video JS move, in ulmo.1) made the video block's `public_view` load a webpack bundle `VideoBlockMain` that does not exist, so public (anonymous) video views get no player JS. `release-ulmo` had the correct `VideoBlockDisplay`. Upstream fixed it in #38012 (`8dd99defac`) on master and verawood but **never backported it to `openedx/release/ulmo`**, so the May build and #505 had the bug too. It was found through `edx/ulmo.3`, which carries the fix as `94963dbae2`; batch 1 takes it as `e123921eb7`. Lesson: also search `openedx/master` and `openedx/release/verawood` for fixes to code that ulmo.1 introduced, not only `openedx/release/ulmo`.

**Rest of the check (2026-10-09), done:**
- **Release notes:** none of the ulmo.2-4 fixes is an ulmo.1 regression. No per-point release notes exist (no GitHub releases; the ulmo.2/3 forum posts are generic), so the commit lists were read instead: they are security fixes or bugs already in Teak or earlier.
- **Sweep of later upstream fixes:**
  - **Method:** take the `fix`/`revert` commits on `openedx/master`, `openedx/release/verawood` and `openedx/release/ulmo` after `release/ulmo.1` that touch files ulmo.1 changed (195). Keep those that apply to the branch but are not on it (38). Of those, keep the ones that do **not** apply to `release-ulmo`, i.e. that target code batch 1 introduces: 7.
  - **Results:** besides #38012 above:
    - **Content libraries v2 only, not taken (decided 2026-10-09).** edx has not adopted v2 (Learning Core) libraries: prod has one test library, `edxtest`. These fixes should be bundled before moving to v2 libraries, and are listed in [Feed to playbook](#feed-to-playbook):
      - `15f39ae1aa` (#38361, verawood only): maps `CAN_LEARN_FROM_THIS_CONTENT_LIBRARY` to authz `view_library` in the authz bridge ulmo.1 adds. Without it, a user who holds a v2 library role only in authz may be refused "learn from library".
      - `11840267f0` and `aee47edf21`: Meilisearch v2 library search (edx-internal configures Meilisearch only on stage).
      - `b278144750` / `7922fa429a`: ComponentLinks left behind when a parent container is deleted (upstream sync from v2 libraries; the link tables are empty in every environment).
      - `e26a3fb7e6`: course name in the collection description when importing legacy libraries into v2.
    - `3430dbd050`: celery `!=5.6.1`. It only flagged because of context lines; batch 1 pins celery 5.5.3. Keep it in mind if celery is upgraded.
- **Known issue to check in QA:** an Ulmo forum sprint update reportedly lists "LTI 1.3 launches fail when using reusable configuration (e.g., Turnitin)". This is unverified, and no fix was found in ulmo.2-4.

### Migration state in prod, edge and stage

Ticket: LP-1148. Status: **done (2026-10-09).** The dependency-migration comparison was redone on the batch-1 `base.txt` and matches. The planned manual `show_unapplied_migrations` against a stage snapshot was dropped as redundant (see step 9 above).

**Conclusion: no manual migration steps are needed in any environment** for the forward deploy. The normal deploy applies what is missing (the `run_migrations` step of the GoCD edxapp pipeline). Nothing needs rolling back or re-running. For a rollback, see [Deploy and rollback](#deploy-and-rollback-lp-1308).

#### Facts the rollback plan relies on

- `openedx_authz 0006` (a `RunPython` with no reverse) is already applied in prod, edge and stage, so it is not in the forward plan and a rollback does not touch it.
- `contentstore 0014` drops `downstream_is_modified`. Old code still reads it, but only on library-link paths.
- Verified 2026-10-30 by read-only replica counts: prod has 1 library (the test `edxtest` library, created during the first deployment as a diagnostic) with 0 learning packages, drafts, component-content rows, component links and container links. Edge has 0 of everything. No v2 libraries will be created. So old code against the new schema is harmless for library paths, and `oel_components 0004` and `oel_publishing 0010` have no data to act on.

#### State of each environment (queried 2026-10-09, read-only)

| | Prod | Edge | Stage |
|---|---|---|---|
| In-repo ulmo.1 migrations (`contentstore 0014`, `modulestore_migrator 0002`-`0006`) and `openedx-learning` (`oel_components 0004`, `oel_publishing 0009`/`0010`) | Not applied (prod is at `contentstore 0013`, `modulestore_migrator 0001`, `oel_components 0003`, `oel_publishing 0008`) | Same as prod | **Applied**, 2026-05-19 (`modulestore_migrator 0002` was applied earlier, 2025-10-22) |
| `casbin_adapter 0001`, `openedx_authz 0001`-`0006` | Applied 2026-05-27 15:43 UTC | Applied 2026-05-27 15:43 UTC | `0001`-`0005` on 2026-05-19; `0006` on 2026-05-22 |
| Package no-ops: `submissions 0006`/`0007`, `lti_consumer 0019`, `user_tasks 0005`, `workflow 0006` | Only `submissions 0006` (from 2026-05-27) and `workflow 0006` (2026-06-01, `release-ulmo`'s own ora2 bump) | Same as prod | All applied (2026-05-19 and 05-22) |
| In-repo MariaDB no-ops (`student 0048`, `entitlements 0017`, `course_goals 0010`, `program_enrollments 0012`, `external_user_ids 0009`) and `survey_report 0006` | Not applied | Not applied | Not applied |
| Exact counts: libraries / permissions / casbin rules | **1 / 1 / 1** | 0 / 0 / 0 | 23 / 36 / 36 |
| `oel_publishing_draft`; `contentstore_componentlink`, `contentstore_containerlink` | 0; 0, 0 | 0; 0, 0 | 26; 0, 0 |

What this means:
- **Prod and edge are not at their pre-May state.** The casbin, authz and `submissions 0006` migrations from the May 27 deploy are still applied and will be skipped. The reversible ones were rolled back. Batch 1 applies everything else fresh, including the package no-ops `lti_consumer 0019`, `user_tasks 0005` and `submissions 0007`.
- **Prod's one library permission is already in authz.** The May 27 run of `openedx_authz 0006` copied it: the library `edxtest` has one admin permission and one casbin rule, scope and subject. (The first `table_rows` estimates said 0; the exact counts show 1. Never trust `information_schema` row counts; on stage it showed 34 where the real count was 36.) Edge has no libraries, so there is nothing to copy there.
- **Stage was never rolled back,** and its library data and authz data are in sync (36 permissions, 36 rules).
- **`contentstore 0014` is low-risk everywhere:** `contentstore_componentlink` and `contentstore_containerlink` are empty in all three, so nothing is lost and it is instant. Splitting it (expand now, contract later) is not needed.
- **`oel_publishing 0010`** (a row-by-row backfill) has no rows to process in prod and edge (no drafts or containers); on stage it already ran.
- **Stage's database is ahead of its code.** Stage already has `contentstore 0014` (it drops `downstream_is_modified`) while running `release-ulmo`, which still reads that column. Library-sync queries on stage may fail until batch 1 deploys. This is the same hazard as the code-only-rollback caveat in [Deploy and rollback](#deploy-and-rollback-lp-1308), seen in practice.
- **Re-running the authz data copy would be safe if it were ever needed:** assigning a role that already exists is skipped, and the group path loops over the same call. It is not needed. Do not try to un-apply `openedx_authz 0006`: it has no reverse step, and leaving applied migrations alone is harmless.
- The MariaDB conversions are no-ops: the database is MySQL (AWS Aurora; stage 8.0.42, prod 8.0.39, edge 8.0.28), and each migration returns early unless `SELECT VERSION()` contains "mariadb".

#### How migrations run and roll back

From edx-internal (`gocd/generated-pipelines/templates/edxapp.yaml.j2`, `argocd/applications/edxapp-migrations/`):
- The GoCD edxapp pipeline has a `run_migrations` step that runs a Kubernetes job per variant before the code deploy. Forward: `show_unapplied_migrations`, then `run_migrations`; it uploads `migration_plan.yml` and `migration_result.yml` to a `<env>-gocd-artifacts` bucket.
- Rollback restores each app to the state recorded in that plan (`run_specific_migrations`), app by app in the order they were migrated, and stops at the first failure.
- The May 27 partial state (casbin, authz and `submissions 0006` left applied, everything reversible rolled back) is consistent with the rollback stopping at the irreversible `openedx_authz 0006`. This is an inference; the S3 artifacts were not pulled and are not needed, because the current state is known.
- A rollback of batch 1 should not repeat that failure: authz and casbin are already applied in prod and edge, so they will not be in batch 1's forward plan.
- `migrations: enabled: false` in the argocd per-environment config is not read by anything in edx-internal (the `django-ida` chart is external); it looks like a leftover from before GoCD ran migrations. It does not matter here.
- There is no agreed way yet to run a one-off management command in an environment (a temporary GitHub Action is one option). None is needed for migrations.

#### Queries as run (2026-10-09; read-only, main edxapp database)

```sql
-- A. What is applied? (a row means "applied now"; no row means not applied)
SELECT app, name, applied FROM django_migrations
WHERE applied >= '2026-05-15'
   OR app IN ('openedx_authz', 'casbin_adapter', 'oel_publishing', 'oel_components', 'modulestore_migrator', 'contentstore')
ORDER BY app, name;

-- B. Exact counts (use COUNT(*), not information_schema table_rows)
SELECT 'casbin_rule' t, COUNT(*) n FROM casbin_rule
UNION ALL SELECT 'openedx_authz_scope', COUNT(*) FROM openedx_authz_scope
UNION ALL SELECT 'openedx_authz_subject', COUNT(*) FROM openedx_authz_subject
UNION ALL SELECT 'openedx_authz_extendedcasbinrule', COUNT(*) FROM openedx_authz_extendedcasbinrule
UNION ALL SELECT 'content_libraries_contentlibrary', COUNT(*) FROM content_libraries_contentlibrary
UNION ALL SELECT 'content_libraries_contentlibrarypermission', COUNT(*) FROM content_libraries_contentlibrarypermission
UNION ALL SELECT 'oel_publishing_learningpackage', COUNT(*) FROM oel_publishing_learningpackage
UNION ALL SELECT 'oel_publishing_draft', COUNT(*) FROM oel_publishing_draft
UNION ALL SELECT 'contentstore_componentlink', COUNT(*) FROM contentstore_componentlink
UNION ALL SELECT 'contentstore_containerlink', COUNT(*) FROM contentstore_containerlink;

-- C. The library rows (small tables; shows which libraries and permissions exist)
SELECT * FROM content_libraries_contentlibrary;
SELECT * FROM content_libraries_contentlibrarypermission;
```

#### What the ulmo.1 range adds (code-side review, 2026-10-09)

Read from git and GitHub only (`e45e7825ea` is the May build; "release-ulmo" is the tip, 2026-10-09).

1. **In-repo migrations added relative to pre-May `release-ulmo`** (the 11 files in `e45e7825ea`): `contentstore 0014`, `modulestore_migrator 0002`/`0003`/`0004`/`0006` (`0004` has "squashed_0005" in its name but no `replaces`; there is no `0005`, and `0006` depends on `0004`), `survey_report 0006`, and five MariaDB conversions (`student 0048`, `entitlements 0017`, `course_goals 0010`, `program_enrollments 0012`, `external_user_ids 0009`). `announcements 0001_initial` is deleted with the app; its `django_migrations` row will be stale, harmless unless a migration depends on it. `release/ulmo.1` and `release/ulmo.4` have identical migration file sets, so batch 2 adds none. The fork migrations `third_party_auth 0014/0015`, `support 0007` and `course_overviews 0030` are already in `release-ulmo` (upstream `release/ulmo.1` lacks `third_party_auth 0014/0015`, which are fork-only). Re-check this list against the batch-1 diff.
2. **Dependency upgrades also ship migrations** (not in the list above, and not in `release-ulmo` today):
   - **`openedx-authz` (new; also new `pycasbin` and `casbin-django-orm-adapter`).** Brings `openedx_authz 0001`-`0006` and, through `0001`'s dependency, `casbin_adapter 0001_initial`. The first ulmo.1 attempt (#212) was reverted on 2026-05-19 (#300) for a "casbin migration error". `openedx_authz 0006_migrate_legacy_permissions` is a `RunPython` data migration with **no reverse**: it copies `ContentLibraryPermission` rows (the `content_libraries` app, which holds v2 Learning Core libraries, keyed by `LibraryLocatorV2`) into the casbin tables, so it cannot be un-applied with `migrate openedx_authz zero`. Prod, edge and stage already have it applied (above). Upstream `release/ulmo.1` pins `0.20.0`, the May build pinned `0.20.1`; both end at migration `0006`, and `0.21.0` and later add a seventh. `release-ulmo` has no `openedx-authz` today, so there is nothing to pin there; the package arrives with batch 1. After batch 1 lands, a constraint so that a dependency bot does not bump it unreviewed is an optional follow-up.
   - **`openedx-learning` 0.27.1 → 0.30.2.** Three new migrations: `oel_components 0004_remove_componentversioncontent_uuid` (drops a column), `oel_publishing 0009_dependencies_and_hashing` (new tables, columns, constraints) and `oel_publishing 0010_backfill_dependencies` (a row-by-row data backfill; its docstring calls the draft update "slow and expensive"). `modulestore_migrator 0004` depends on `oel_publishing 0008`, which exists in 0.27.1.
   - **Patch bumps ship MariaDB no-ops too:** `edx-submissions` 3.12.1 (`submissions 0006`, `0007`), `lti-consumer-xblock` 9.14.3 (`lti_consumer 0019`), `django-user-tasks` 3.4.4 (`user_tasks 0005`); `ora2` (`workflow 0006`) arrived with `release-ulmo`'s own bump.
3. **`contentstore 0014`.** `0013` adds `downstream_is_modified` as `BooleanField(default=False)`, so un-applying `0014` re-adds it with a default; the schema is reversible. It loses data both ways: going forward it drops `downstream_is_modified` with no data migration, and un-applying drops `downstream_customized`. The tables are empty in every environment, so this does not matter in practice.
4. **Batch 1 must not downgrade dependency pins.** `e45e7825ea` had older pins than `release-ulmo` has now, for packages that carry migrations: `edx-enterprise` 8.0.15 (now 8.17.0), `enterprise-integrated-channels` 0.1.58 (now 0.1.70), `openedx-django-wiki` 3.1.1 (now 3.1.2), `ora2` 6.17.1 (now 6.17.2). Keep `release-ulmo`'s versions; the lockfile downgrade check is for this, and it matters for migrations because the databases already have those packages' newer migrations applied.
5. **Check on the batch-1 `base.txt`** (part of the audit step above): `openedx-authz` is `0.20.0` or `0.20.1` (if newer, review the new authz migrations), and no other dependency change adds a migration that this section does not list.

## QA and testing (LP-1351)

Ticket: LP-1351. This is the single place for review and testing information; the ticket points here. The notes support testing; they are not a prescribed procedure. Where something is tested (local, sandbox or stage) is decided by where it can be tested. Before QA starts, the LP-1148 owner confirms this section is current (see "Handoff to QA" above).

### Review and owner decisions

1. **PDF textbook viewer** (owners of fork PRs #51/#64/#86): applies to **batch 2** (pdf.js 5.7.284, `4d2e220d6f`), not batch 1. Review the choice and the alternatives in the commit message of `fc8f1f0a8c` on #505.
   - The upstream patch adds the viewer's own origin to `HOSTED_VIEWER_ORIGINS`, which as read skips pdf.js's own cross-origin `file` check.
   - The fork guards do not block protocol-relative `//host` URLs.
   - The fork's #86 (empty `DEFAULT_URL`) no longer applies; confirm pdf.js 5's default-file behaviour.
2. **Forum bulk delete** (discussions owners): **not changed by this upgrade**, but worth knowing.
   - The fork's #97/#260 widened upstream 699c831fa3's global-staff-only gate to course Discussion Admins/Moderators.
   - With `course_or_org=org`, a moderator of one course can bulk-delete a learner's posts across the whole org.
   - Suggested narrow fix, separate from this upgrade: require global staff for org-scope bulk delete in `BulkDeleteUserPosts.post`.
3. **studio-frontend** (Studio owners): the upgrade drops it, following upstream, while `release-ulmo` still has 18 references. Confirm nothing still needs it.
4. **Authoring MFE and legacy Studio removal** (Studio/authoring owners): see [Breaking changes in the range](#breaking-changes-in-the-range). Confirm the forked `frontend-app-authoring` does not depend on the removed legacy home, course outline or files pages, or on the library serializer `version` field. Also check for DB-backed `legacy_studio.*` course/org waffle overrides.
5. **Announcements app removed** (whoever owns the learner-facing site content): nothing in argocd prod config references it, but confirm no fork template, menu or link depends on `openedx/features/announcements`.

### Changes and risks worth testing

- **The previous prod failure:** seed `courseware_studentmodule` `AUTO_INCREMENT` above 2^31 (e.g. `ALTER TABLE courseware_studentmodule AUTO_INCREMENT = 4240000000;`). Navigate into a unit and back in the Learning MFE and confirm XBlock state saves without "Forced update did not affect any rows" (see edx PR #495). LP-1326 proposes doing this on stage permanently.
- **The fork's learner-state work:** incremental loading of large assessment xblocks and hydrating learner state for paginated assessment children (`courseware/model_data.py`, `block_render.py`). Exercise a large problem bank or assessment.
- **Video:** HLS playback, audio description (upload in Studio, playback in LMS), language menu height with many caption languages. Also the **public (anonymous) video view** (`public_view`; a public unit or the video share page): the player must load. Upstream ulmo.1 broke it (a bundle that does not exist); batch 1 carries the fix `e123921eb7`.
- **PDF textbooks:** batch 1 keeps the old pdf.js plus the fork's guards; confirm they still work.
- **Content library permissions (new openedx-authz):** ulmo.1 moves library permissions into casbin tables (`openedx_authz 0006` copies existing `ContentLibraryPermission` rows). Exercise Studio library access (view, edit, team management) after the migrations. This was the area of the first revert (#300). Stage already has the migrated data (23 libraries, 36 permissions, 36 casbin rules), so stage is the place to test it; prod has one library (`edxtest`) and edge none.
- **Library creation org list (LP-1102, #509):** batch 1 combines #509's global-staff branch with upstream's ulmo.1 change that unions the org-staff and course-creator orgs (`670c81f0f2`), in `get_allowed_organizations_for_libraries` (`cms/djangoapps/contentstore/views/course.py`). This combination has never run anywhere: May's build had no #509, and `release-ulmo` has no `670c81f0f2`. Check the orgs offered when creating a library for global staff, org staff, course admins and course creators, with the `EXPANDED_LIBRARY_CREATION_ORGS` flag on and off.
- **LTI 1.3 with reusable configuration** (for example Turnitin): reported as an Ulmo known issue (unverified; see [Ulmo.1-regression check](#ulmo1-regression-check)). Check whether the fork uses reusable LTI configurations, and if so test a launch.
- **Proctortrack** on a sandbox (LP-846): the May 27 "Decoding attempt" error.
- **SAML:** the May merge had SAML conflicts; test SAML login if at all possible (sandbox or local).
- **Stage scenarios that failed on the sandbox in May** (due to sandbox errors): course import and export; the ulmo deployment bugs (LP-1298 to LP-1303).
- **Pre-flight items** from the playbook (PR #14962 doc `04`, section B, Django 4.2→5.2) and its deployed-settings checks against `argocd/applications/edxapp-*/` in edx-internal (the real deploy inventory, not Datadog). Verified on 2026-10-06 against #505:
  - **Storage settings:** argocd prod sets the legacy `DEFAULT_FILE_STORAGE` and `STATICFILES_STORAGE`, which Django 5.1 removed. They are still honoured because `lms/envs/production.py` and `cms/envs/production.py` map them into `STORAGES` ("For backward compatibility"). No `get_storage_class` callers remain (only a docstring in `common/djangoapps/util/storage.py`).
  - **Fork-only settings** from the learner-state and bulk-unenroll work are in `lms/envs/common.py`: `INCREMENTAL_LOAD_PROBLEM_THRESHOLD`, `INCREMENTAL_LOAD_EAGER_COUNT`, `XBLOCK_CHILDREN_BATCH_MAX`, `BULK_UNENROLL_*`. Re-confirmed on the batch-1 tree (2026-10-09), as were the storage-settings mapping and the absence of `get_storage_class` callers.
  - **Intentionally deferred commits:** the only `temp:` commit relative to `release-ulmo` was `582e345108` (SAML SSRF revert, on `edx/ulmo.3`). Not applicable to batch 1.
  - **MariaDB UUID migrations** are no-ops: the database is MySQL (AWS Aurora), not MariaDB (confirmed by the team; no query needed).
  - **Breaking changes:** see below.
- **May 27 timeline** (task under LP-1351): reconcile the sources in [May 27 timeline: open discrepancy](#may-27-timeline-open-discrepancy).

### Changes from the 2026-10-30 sync

Only items with extra risk from combining them with batch 1. Everything else from that sync is listed as "reviewed, not retested" in the [sync log](#sync-log).

- **CHANGE to the library creation org list item above (LP-1102):** #528 sorts the org dropdown alphabetically (case-insensitive), and it changed the same function, `get_allowed_organizations_for_libraries`, as the combination that item says has never run anywhere. The merge keeps #528's sort for global staff and also sorts batch 1's org union case-insensitively. When checking the orgs offered, also check that they are in alphabetical order, ignoring case.

### Breaking changes in the range

The merge audit checks that nothing was lost in merging. It does not check what upstream deliberately broke. These are the commits marked `!` in `242a69d06b..openedx/release/ulmo` (the pre-ulmo.1 base, not the merge-base with `release-ulmo`, which spans only 25 commits because of the revert trap). The `BREAKING CHANGE:` footers were also searched and found nothing beyond these. **All rows except `fca21c955f` are in ulmo.1 and so are in batch 1.**

| Commit | What | Check before landing |
|---|---|---|
| `ae8996f68b` | Django 5.2 | Covered by CI, devstack and the storage-settings check. |
| `0077058e37`, `e64d4cee8d`, `fcfa4138fd` | Legacy Studio home, course outline and files/uploads pages removed, with their `legacy_studio.*` waffle flags | Forked authoring MFE works without the legacy pages. No DB-backed course/org overrides still selecting a legacy page (needs the toggles API or DB). |
| `20bc7113e3` | Studio Maintenance and Announcements app removed (`openedx/features/announcements`) | Nothing in fork templates or menus links to it. argocd prod config has no reference. The app's migration is deleted. |
| `4c051378d0` | Last calls to `cs_comments_service` removed | The audit covers `thread.py` (the fork keeps its soft-delete `_delete_thread`). Prod still sets `COMMENTS_SERVICE_URL`/`KEY` in argocd; confirm nothing in the branch still reads them. |
| `f7a1a9d990` | `version` removed from the library serializer | Forked authoring MFE does not read `version`. |
| `09e86e24b2` | `top_level_downstream_parent_key` changed from a Dict field to a String field | No migration; touches the upstream-sync code (`cms/lib/xblock/upstream_sync.py`, `contentstore/tasks.py`, `xmodule/util/keys.py`). Confirm whether prod has downstream blocks that stored the old dict form, and whether the new code reads them. Not yet investigated. |
| `fca21c955f` | Survey `redirect_url` (GHSA-2843-x998-f8r2) | Not in ulmo.1 (batch 2). Already in prod independently as `e46653783d`; the files are identical. No action. Verify by content, not ancestry. |

Cross-repo: the first three rows and `f7a1a9d990` change what the authoring MFE can call. Confirm the forked `frontend-app-authoring` (and `frontend-app-learner-dashboard` where it touches the same APIs) before deploying, and decide the deploy order if a fix is needed on the MFE side.

### Blockers

Tracked in Jira, not here. The first three must be done before the upgrade is deployed.

| Ticket | What | Affects testing? |
|---|---|---|
| LP-1304 (Maintenance, P3): Ensure edxapp private requirements are Django 5.2 compatible | The private production/stage plugin pins live in edx-internal, `argocd/applications/edxapp-lms/requirements/private_requirements.txt` (also used by CMS, BOMS-233; deployed via `edx/internal-dockerfiles`). `ai-aside==3.8.9` and `federated-content-connector==1.7.0` are already tested on Django 5.2 upstream and are just older pins. `platform-plugin-braze` (git-pinned commit) and `learner-pathway-progress<1.3.5` are **real blockers**: no Django 5.2 in their test matrix, and still on Python 3.8. Some need their own tickets and upgrades. | Yes. CI installs the repo's own requirements, not these private ones. A sandbox or stage deploy only tests Django 5.2 against the plugins if they are installed there. |
| LP-1150 (Maintenance): Ensure web certificates are tested for Django 5.2 compatibility | Affects the DB only; certainly before deploying. Shown as Done on 2026-10-09. | No. |
| LP-1326 (Maintenance): Bump `courseware_studentmodule` AUTO_INCREMENT on stage | Lets stage testing catch large-primary-key regressions like the May bug. One-way door on stage; see the ticket's risks. | Yes. Without it, stage cannot reproduce the May failure. |

Open question from LP-1304: for plugins already Django 5.2-compatible (`ai-aside`, `federated-content-connector`), is it safe to bring in the newer version **before** the edxapp upgrade, while prod is still on Django 4.2? Depends on whether each package's supported-Django matrix still includes 4.2 at the newer version; check per package.

### Final sync with `release-ulmo`

Until testing starts, check about once a day (`git fetch edx --tags && git fetch openedx`, then `git log --oneline HEAD..edx/release-ulmo`) and merge promptly; small merges are easier to audit. To re-run the survival check on a sync: `S=<scratch> A=HEAD UP=release/ulmo.1 bash survival.sh` (scripts in the appendix of `LP-1148-ulmo4-merge-audit.md`), then check only the LOST items from the newly merged fork commits; everything else was triaged in `LP-1148-ulmo1-merge-audit.md`. Re-run `lockfile_downgrades.py edx/release-ulmo HEAD` too. Merge the branch tip; do not cherry-pick. Add an entry to the [sync log](#sync-log) for each sync (what was merged, the local checks, and the retest decision); do not record CI results there, since a commit that records them is itself untested. Re-run the survival check and the lockfile downgrade check on every sync. Wide commits (like #503, 39 files) need the survival check most.

Resolve conflicts in `requirements/edx/*.txt` (compiled lockfiles) case by case; look at each hunk rather than applying one rule:
- Adjacent insertions of different packages (like `signxml` and `simpleeval` on 2026-10-14): keep both.
- Both sides changed the same pin: decide deliberately. Never let a downgrade win. Compare against what the branch needs for Django 5.2, and say what was chosen in the sync log.
- `# via` lines are derived from the pins: reconcile them with the final set of pins rather than picking a side.
- A conflict in a `.in` file is a real source change: resolve it first, then make the `.txt` files consistent with it.
- Always check the result with `lockfile_downgrades.py`. If a hunk is still ambiguous, ask the LP-1148 owner.

Before testing, QA checks that the checks on the PR's tip are green (in the GitHub UI, or with `gh pr checks 514`) and records the SHA tested in LP-1351. QA is part of the owning team, so a red or pending check is something to help resolve, not a reason to wait idle. A docs-only commit after a green run cannot change behavior, so it needs no separate confirmation beyond CI passing again.

There is no freeze on `release-ulmo`. When the last `release-ulmo` changes are pulled in, look at what they touch (video JS, courseware state, PDF viewer, anything in the list above), judge the risk, and decide how much retesting that needs. Record the tested `release-ulmo` and upstream SHAs and the retest decision here, and confirm the PR is still conflict-free, before marking the PR ready and again right before merging.

#### Sync log

- **2026-10-30, `release-ulmo` `bac76e763e` → `432ae28549`** (merge `cf8965ba1b`). 25 commits. All of them were already in prod (Datadog `version` tag `lms-prod-432ae28549-261` on 2026-10-30). Conflicts:
  - `cms/djangoapps/contentstore/views/course.py`: #528 (LP-1102, alphabetical org dropdown) against batch 1's org union (`670c81f0f2`). Kept #528's sort for global staff and the union, and made the union's `sorted()` case-insensitive so #528 holds on that path too. See [Changes from the 2026-10-30 sync](#changes-from-the-2026-10-30-sync).
  - #494 (edx-enterprise 8.18.0 → 9.0.0, deletes `openedx/features/enterprise_support/`): took the deletion of `enterprise_support/tests/test_logout.py`. The relocated `EnterpriseLogoutTests` in `user_authn/views/tests/test_logout.py` uses `assert_dict_contains_subset`, like the rest of batch 1. Also converted the one `assertDictContainsSubset` that #535 added in `support/tests/test_views.py`.
  - Requirements merged without conflicts: certifi 2026.10.19, pip 26.2.2 and edx-enterprise 9.0.0 are on the branch.

  Survival check (`BASE=bac76e763e`): LOST only for the resolutions above, plus context-only misses in the #530 and #494 lockfiles and in an import block of `505916dc41` (`TEST_PASSWORD` is imported). Lockfile downgrade check: only the already-triaged `pact-python`, `globals` and `regenerator-runtime`.

  **Retest decision:** only the library org dropdown (above). **Reviewed, not retested.** Common reason: already in prod, no conflict with batch 1, and no ulmo.1/Django 5.2 interaction.
  - #494 enterprise_support removal: edx-enterprise 9.0.0 declares Django 5.2.
  - #539 XBlock handler fixes (`febfa0e63f`, `1a62af0fe3`, `505916dc41`, `13c6cb54fa`) and #529 lazy-load hydration: batch 1's delta in `block_render.py` is one docstring line.
  - #539 certificate escaping (`b56f32330a`): batch 1's delta in `webview.py` is the one-line LinkedIn `add_to_profile_url` call.
  - #539 discussions and ID verification fixes; #515 `all_blocks`; #535 support tools; #546 RV plugin API; #530 certifi/pip; #526, #527, #490, #538 and `6401ea2918` (certificate access by edx-exams proctoring status).
- **2026-10-19, `release-ulmo` `f55e9c7d61` → `bac76e763e`** (merge `e79ed6ad3e`). Pulled in #519 (`get_learner_display_name_override` added to the branding API; `get_learner_display_username` returns the same result for existing callers), #525 (edx-enterprise 8.17.0 → 8.18.0, pin lines only) and #523 (LP-1362: the bulk unenroll preview flags Exec Ed and Bootcamp runs, and confirm now returns 400 unless the request carries `confirm_exec_ed=true`; also resolves wrong-case course ids to the stored spelling). No conflicts. Survival check (`BASE=f55e9c7d61`): #519 and #523 PRESENT; #525 LOST only in the four lockfiles, and `edx-enterprise==8.18.0` is on the branch in `constraints.txt` and the four lockfiles. Lockfile downgrade check: only the already-triaged `pact-python`, `globals` and `regenerator-runtime`. **Retest decision:** nothing touches video JS, courseware state, the PDF viewer or the other areas listed above, and the three changes are identical on `release-ulmo`, so no extra retesting beyond CI. FYI for the bulk unenroll owners (not upgrade QA): #523 changes the API contract, so confirm returns 400 without `confirm_exec_ed` and the support-tools frontend must send it (separate repo). **Not pulled in, deliberately:** upstream `release/ulmo` gained `9b58d0459e` (openedx-forum 0.3.8 → 0.4.1.post1, GHSA-c6fp-f54c-6cv4: the forum HTTP API is unrouted unless `FORUM_ENABLE_HTTP_API` is set, and needs staff when it is). Prod runs the `edx/forum` fork (pinned in edx-internal's `private_requirements.txt`), not the `openedx-forum` pin in this repo's lockfiles, so LP-1391 fixes it there and this branch does not need it. If LP-1391 reaches prod during testing, retest discussions on this branch (post, reply, vote, flag, pin, ban). `9b58d0459e` and `723be60058` remain batch 2 candidates for the `openedx-forum` pin. The branch contains `release-ulmo`, so it is conflict-free with it.
- **2026-10-14, `release-ulmo` `0666f56778` → `f55e9c7d61`** (merge `04857cae2b`). Pulled in #518 (enterprise-integrated-channels 0.1.70 → 0.1.77, adds `signxml==5.1.0`; declares Django 5.2), #520 (marketing email opt-in in `learner_home/views.py`), #479 (docs only) and #521 (web certificates on course rerun, `contentstore/views/course.py`). Conflicts only in `requirements/edx/{base,development,doc,testing}.txt`, where `signxml` and the branch's `simpleeval` were added on adjacent lines; kept both. Survival check (`BASE=0666f56778`): #520, #479, #521 PRESENT; #518 LOST only because its context lines changed, and a package-level check confirms every pin and `via` line it added is on the branch. Lockfile downgrade check: only the already-triaged `pact-python`, `globals` and `regenerator-runtime` (see `LP-1148-ulmo1-merge-audit.md`). **Retest decision:** nothing touches video JS, courseware state or the PDF viewer, so no extra retesting beyond a smoke test of course rerun (certificates enabled on the rerun) and the learner dashboard. Branch is conflict-free with `release-ulmo` after the merge. CI on #514 green at `af97af76a2` (45 pass, 1 skipped). (CI results are no longer recorded in this log; see [Final sync with `release-ulmo`](#final-sync-with-release-ulmo).)

## Deploy and rollback (LP-1308)

Ticket: LP-1308. Stage first (test on 2026-11-02), then prod.

**QA on stage:** the same testing as on the sandbox, plus the call-outs in [Changes from the 2026-10-30 sync](#changes-from-the-2026-10-30-sync), due to the latest changes on `release-ulmo`.

**A prod deploy goes to prod and edge at the same time.** For the May 27 attempt, see [History](#history-what-was-deployed).

**Dashboard:** [LP-1308 ulmo.1 deploy](https://app.datadoghq.com/dashboard/dfe-74q-pte) in Datadog. Watch it in one browser tab per environment (`env:prod`, `env:edge`). It documents the known, expected issues, so that they do not cause an unnecessary rollback; anything unexpected is a reason to roll back. It also shows whether a rollback worked. Two LP-1308 monitors (311464046, 312818221) alert on the XBlock save errors; they are deleted after the deploy (see LP-1308).

**Rollback:**
- **Rolling back is a quick decision, made without hesitation, for any unknown issue.** Redeploy the previous image (the manual `rollback_code` stage; it reads `previously-deployed-tag.txt`), then pause the pipeline to block more deployments.
- **Revert or fix forward is a separate, slower decision**, made in the IST/ET overlap hours. To revert, use GitHub's revert button on the merged #514 and retitle it `revert: ...` for commitlint. Re-landing later needs a revert of the revert (see [The revert trap](#the-revert-trap-important-for-anything-that-merges-into-release-ulmo)).
- **Migration rollback is a separate manual stage (`rollback_migrations`); the default is to skip it.** It reverses only the apps in the forward run's `migration_plan.yml`, in order, and stops at the first failure. Old code against the new schema logs `Unknown column` (MySQL 1054). Stage shows this today: `contentstore 0014` is applied there, and Studio's library-sync summary (`/api/contentstore/v2/downstreams/<course>/summary`) returns 500. After a prod rollback, either live with these benign errors (no library data exists; see [Facts the rollback plan relies on](#facts-the-rollback-plan-relies-on)) or run `rollback_migrations`.

After the QA ticket and the blockers are done:

1. **Write the rollout and rollback plan** (above), then deploy to stage, then prod. No maintenance banner (decided in LP-1308).
2. **Mark the PR ready and merge it** with GitHub's **"Create a merge commit"** (as #212 and #304 were), not squash or rebase. Squash would also bring ulmo.1 back, since its upstream commits are already ancestors of `release-ulmo`, but it would collapse the revert, ports, Django bump and PII fix into one commit (so the bump could no longer be reverted on its own) and orphan the commit SHAs cited in this plan and the audit. Rebase would rewrite those SHAs and replay the `release-ulmo` merge. Do the final sync (see QA section) first, and again just before merging.
3. **Once stable in production**, do the [final steps for every batch](#every-batch-final-steps). For batch 1, also delete the saved May 27 spans and logs from Google Drive (listed in LP-1308).
4. **After the last batch**, delete `docs/plans/LP-1148-*` in a follow-up PR, and update `docs/plans/README.rst`. First make sure every item in [Feed to playbook](#feed-to-playbook) is resolved with an edx-internal edit or consciously dropped.

## Every batch: final steps

At the end of every batch, batch 1 included, once it is stable in production:

1. **Capture what the batch taught in the edx-internal upgrade playbook** ([PR #14962](https://github.com/edx/edx-internal/pull/14962), `docs/openedx-upgrade-process/`). Add new lessons to [Feed to playbook](#feed-to-playbook). Then resolve each open row there with an edx-internal edit, or drop it with a reason. This step closes each batch, rather than opening the next one, so the last batch is covered too.

## Batch 2 (scope open)

Batch 2 is some set of changes after ulmo.1 on `openedx/release/ulmo`, merged straight into `release-ulmo` once batch 1 has landed (a plain merge, since the revert trap is gone by then). How much goes in it is not decided: it could be ulmo.2 only, through ulmo.3 or ulmo.4, or up to the upstream tip. The lists below help decide, and each behaviour-changing item needs coordination with its owner. No Jira ticket exists yet; create one when the scope is decided.

### Post-ulmo.1 changes not yet in `release-ulmo`

Found with `git patch-id` against the last 400 `release-ulmo` commits (ancestry misses cherry-picks). Caveat: only unmodified cherry-picks match, so a modified cherry-pick shows up as "not in". Re-run on fresh refs before relying on it.

**Already in `release-ulmo` by content** (not new): asset-sandbox XSS fix (`2efdce0760` = `723014f37b`, #481, 2026-09-21, with the per-course `course_assets.allow_unsafe_asset_rendering` flag), LTI nonce replay (+style fix), `set_course_mode_price` staff gate (#319), discussion-email style-tag removal, survey redirect (`fca21c955f`), `activation_key` removal from the account API.

**Not in `release-ulmo`** (new to prod when batch 2 lands):

| Commit | What | Coordination note |
|---|---|---|
| `46c543590c` | Gate support course-team GET on can-manage authorization | Behaviour change for support users. |
| `52f2b3bb3e` | Escape user input in notification content templates | Rendering change. |
| `241b914a19`, `c99de016f1` | Studio video-download SSRF block, resource bounds | Behaviour change for large/remote downloads. |
| `6291b6badd` (+`4f15b6d4e0`) | SAML metadata SSRF block | The fork has its own validator (`TestValidateSAMLMetadataURL`); see follow-ups. |
| `c5ab9b34a3` | library_content transformer for all ItemBankMixin xblocks | Targets code that already exists on `release-ulmo`. |
| `97de0586d8` | `safe_extractall` commonpath | |
| `f7f95089fe` | Sidebar cache key uses block-structure version | `release-ulmo` still keys on `course_version`. |
| `4d2e220d6f` | Vendored pdf.js 1.0.907 → 5.7.284 | Fork PDF guards (#51/#64/#86); see the PDF item in [Review and owner decisions](#review-and-owner-decisions). **Not in batch 1.** |
| `723be60058` | openedx-forum 0.3.8 → 0.4.1 | Pinned-NULL thread sort bug. |
| `6fd5f947a3`, `e66e4ebbf7` | Display number/org in course serializer; search by display number | |
| Django 5.2.7 → 5.2.11, ora2 6.17.1 → 6.17.2, edx-search 4.3.0 → 4.4.0 | Requirements | `release-ulmo` already has ora2 6.17.2. |

Behaviour-changing items (support gate, video SSRF/limits, SAML SSRF, notification escaping) need coordination with their owners (support, Studio, SSO and notification owners) before batch 2; they are the reason not to deploy everything at once. Whether #481 itself is deployed is checkable from the edx-internal prod deploy commits: find the first prod image tag whose commit contains `723014f37b`.

## Follow-ups (separate from landing)

- Delete the unused RequireJS copy `xmodule/js/src/video/09_video_audio_description.js`. Upstream deleted that directory's other files; the live module is under `xmodule/assets/video/public/js/`.
- Optionally re-add the fork-only IPv6 and reserved-address cases to `TestValidateSAMLMetadataURL`. Coverage only; the validator code is unchanged.
- ~~Remove the Open edX tutorial workflow `.github/workflows/check-for-tutorial-prs.yml` from `release-ulmo`.~~ Done in [#507](https://github.com/edx/edx-platform/pull/507) (`bd440bd477`); batch 1 keeps it deleted.
- Report upstream that openedx-learning 0.30.2's `PublishableEntityVersionDependency` docstring says `.. no_pii` without the colon (fixed on openedx-learning main).
- Report the two pycodestyle errors upstream (E302 in `openedx/core/lib/tests/test_extract_archive.py`, E303 in `cms/djangoapps/contentstore/rest_api/v1/views/tests/test_videos.py`, on `openedx/release/ulmo`) so the next upstream merge doesn't conflict with our whitespace fix.

## Feed to playbook

The edx-internal playbook ([PR #14962](https://github.com/edx/edx-internal/pull/14962)) is for learning how to continue upgrades; this plan is the source of truth for landing ulmo. Lessons that belong there, so that `docs/plans/LP-1148-*` can be deleted without losing them. Mark each one resolved when the edx-internal edit has merged, or dropped with a reason.

| Lesson | Playbook home | Status |
|---|---|---|
| Hunk-level survival check, and the moved-files / dead-copy class of silent merge loss (scripts are in the #505 audit appendix; copy them, since the appendix will be deleted) | `01-playbook.md` | Open |
| The revert trap: a plain merge omits content that was merged and reverted; the fix is a revert of the revert; also applies to rollback | `01-playbook.md` | Open |
| Do not assume point releases are safe: diff candidate changes against prod by `git patch-id` (not ancestry, which misses cherry-picks) and review behaviour-changing fixes (per-course flags, SSRF gates, auth gates) for coordination; split batches at the last point that has run in prod | `01-playbook.md` | Open |
| Do not build on someone's unproven test branch (`edx/ulmo.3`); build on `release-ulmo` plus prod-tested content, and use test branches only as cross-checks, with a disagreement rule | `01-playbook.md` | Open |
| Breaking-commit detection needs the true prior content base. With reverts, the merge-base spans 25 commits here instead of 157 | `01-playbook.md`, breaking-commit detection | Open |
| Prior-deploy state: when an earlier attempt was deployed and rolled back, query `django_migrations` in every environment before re-landing | `01-playbook.md`, migration review | Open |
| Proving what ran in prod and when, versus what was in git or on stage: the edx-internal "Deploy ... to prod" commit history (image tag = `<sha>-<build>`) plus a Datadog `version` tag check; use it in RCAs for rollbacks and before trusting ticket titles or prior claims (copy the "How to check what was in prod" subsection) | `01-playbook.md`, new section, and the RCA/rollback checklist | Open |
| Rollback section: roll back the image quickly and pause the pipeline; decide on revert or fix forward separately. After contract-phase migrations (`contentstore 0014`), old code errors against the new schema. Decide per migration whether those errors are benign (here they are: no data) or need the manual `rollback_migrations` stage. | `01-playbook.md`, new section | Open |
| Deploy dashboard: check each prior-incident error signature against current logs and keep only those absent there; measure baselines per environment (a prod deploy also goes to edge); filter expected errors out of the overall error view; group by the `version` tag | `01-playbook.md`, deploy section | Open |
| MariaDB UUID migrations are no-ops on MySQL (we run AWS Aurora MySQL) but are in this batch; the preflight should state the engine up front instead of leaving it as a check | `04-...preflight`, `02-...digest` | Open |
| `openedx/features/announcements` is retired upstream, so it is not a pluginize candidate; batch-state wording in registry rows 3 and 16 belongs here, not there | `03-...registry` | Open |
| `05-...ulmo3-batch` open review items are superseded by the batch-1/batch-2 split | `05-...ulmo3-batch` | Open |
| Branching strategy: keep `release-ulmo` through the ulmo catch-up, then cut `release-verawood` from it and merge upstream in batches; revisit `master` at parity (open question 4) | `00-index.md` | Open |
| Before adopting content libraries v2, take the upstream v2-library fixes that are not on `release/ulmo`: `15f39ae1aa` (authz "learn from library" mapping), `11840267f0` and `aee47edf21` (Meilisearch library search), `b278144750` (ComponentLinks cleanup), `e26a3fb7e6` (import collection description). Most are already on verawood. See [Ulmo.1-regression check](#ulmo1-regression-check). | Batch/feature planning (a new section, or the registry) | Open |
| Ulmo.1-regression checks must also search `openedx/master` and `openedx/release/verawood`: upstream does not backport every fix to the named release branch (#38012 was never backported to `release/ulmo`). Use the "applies to the candidate but not to the deploy branch" filter from the regression check | `01-playbook.md` | Open |
| Lockfile downgrade check at every sync (script is in the audit appendix; also copied to the playbook) | `01-playbook.md`, `scripts/` | Done in edx-internal#14962 |
| First retrospective entry: the playbook, applied before any deploy, surfaced the breaking-change inventory and the rollback caveat that the merge audit alone missed | `01-playbook.md`, retrospectives | Open |
