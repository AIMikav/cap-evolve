# PROCESS — what I did this iteration (explainability; REQUIRED)

## Ranked issue list (clusters by # failing tasks × trials, biggest first)
| rank | cluster | tasks | shared root cause | tag | planned change class |
| --- | --- | --- | --- | --- | --- |
| 1 | Wrong implementation / wrong fix location (ArgoCD, osbuild, CVO) | 12 (0543,0544,0547,0550,0552,0554,0562,0604,0606,0608,0611,0613) | Agent fixes right bug in wrong layer/function, or wrong approach (nil panic, wrong scope). Tests expect fix in specific location. | KNOWLEDGE | Add "read test code to find WHERE fix belongs" rule |
| 2 | P2P seeded bugs in unmodified packages — kubevirt namespace | 3 (0357,0359,0360) | F2P 1/1 but 17 P2P in virtctl/* — shared namespace utility returns wrong value | KNOWLEDGE | Add "shared root cause for bulk P2P" rule |
| 3 | P2P seeded bugs — buildah copier/internal | 3 (0098,0102,0103) | F2P partially fixed but P2P in copier, volumes, open packages; agent dismisses as environmental | KNOWLEDGE | Strengthen full-repo test + seeded bug emphasis |
| 4 | P2P seeded bugs — containers/storage | 2 (0161,0163) | F2P fixed but 12 P2P in mount, archive, unshare packages | KNOWLEDGE | Same as above |
| 5 | Molecule incomplete fix — seeded bugs not found | 4 (0037,0039,0059,0082) | Agent fixes described bug but misses seeded bugs elsewhere; dismisses as environmental | KNOWLEDGE | Strengthen seeded bug emphasis |
| 6 | kubectl P2P — TestApplySetParentValidation | 1 (0278) | F2P 3/3 but changes lost during git stash + context overflow | KNOWLEDGE | Stash safety already addressed in iter 2 |
| 7 | Context exhaustion / infrastructure | 3 (0263,0536,0538) | Agent never ran (0263), or build infra issues in verifier (0536,0538) | N/A | Cannot fix with prompt |

## Changes made this iteration (one row per edit — aim for MULTIPLE classes)
| cluster | edit class | file / tool | what & why it generalizes | protects passing? |
| --- | --- | --- | --- | --- |
| 1 (wrong fix location) | Add missing rule (Step 3) | SKILL.md, prompt.md | "Read FAILING TEST code to understand WHERE fix belongs — test assertions tell you the correct output, function, and location" | BOUNDED: adds a pre-coding step. Passing tasks already find the right location. |
| 2,3,4,5 (P2P seeded bugs) | Rewrite existing rule (Step A) | SKILL.md, prompt.md | "Run FULL repo test suite (go test ./... from ROOT), not just modified package — any failing test counts equally" | BOUNDED: adds broader test step. Passing tasks already pass all tests. |
| 2 (kubevirt namespace) | Add missing rule (critical rules) | SKILL.md, prompt.md | "When many packages fail with SIMILAR errors, look for SHARED root cause (utility function returning wrong value) — fix once to fix all" | BOUNDED: adds diagnostic pattern. Only fires when bulk test failures with shared signature exist. |
| 1 (nil panic) | Add missing rule (critical rules) | SKILL.md, prompt.md | "For Go: check nil before dereferencing pointer fields (FinishedAt, StartedAt, DeletionTimestamp) — nil panic cascades to ALL tests in binary" | BOUNDED: additive safety check. Does not change behavior on passing tasks. |
| 3,4,5 (seeded bugs) | Strengthen existing rule (Step B) | SKILL.md, prompt.md | "Seeded bugs you MUST also fix — they count equally against your score" (stronger than "should be fixed") | BOUNDED: emphasizes existing rule. Passing tasks already pass. |

## Verify-the-fix (one line per change)
- Edit 1 (read test code): trace task-0543 shows agent fixed wrong error message (validation vs runtime) without reading test file; trace task-0550 shows agent fixed wrong layer (generators vs utils). New rule tells agent to read test source → would discover expected fix location. Passing tasks (0042, 0093, 0258 etc.) already fix the right location → rule does NOT change their behavior.
- Edit 2 (full repo test suite): trace task-0357/0359/0360 shows agent fixes F2P but never runs `go test ./...` from repo root to discover 17 virtctl P2P failures; trace task-0037/0039 shows agent dismisses failures without broad test run. New rule says run from repo root → would discover seeded bugs. Passing tasks already pass all repo tests → no behavior change.
- Edit 3 (shared root cause for bulk failures): trace task-0357/0359/0360 shows 17 identical P2P failures in virtctl packages caused by `clientutil.GetNamespace()` returning wrong namespace. New rule tells agent to look for shared utility → would find and fix GetNamespace. Passing tasks don't have bulk P2P failures → no behavior change.
- Edit 4 (nil safety): trace task-0544 shows agent's fix caused nil pointer panic on `FinishedAt.Time` → 20 test regressions. New rule tells agent to guard nil → would prevent panic. Passing tasks already have nil-safe code → no behavior change.
- Edit 5 (seeded bugs must fix): trace task-0037/0039/0059/0098 shows agent dismisses seeded bugs as "environmental" and stops. Stronger language "MUST also fix — they count equally" → agent would continue investigating. Passing tasks have no unfixed seeded bugs → no behavior change.

## Process & features used
- 5 parallel diagnostic subagents to read and cluster all 28 failing trajectories concurrently
- Read all cross-iteration files (LEDGER, JOURNAL, RUNMAP, prior iterations)
- Built on iter 1+2 accepted changes; did NOT re-add anything rejected

## Good things to PRESERVE
- All iter 1 rules (investigate pre-existing failures, multi-package, no new test files, no HEAD revert, go build)
- All iter 2 rules (don't dismiss as environmental, broader test coverage, stash safety, minimal edit, grep-r, git add, context efficiency)

## Deliberately skipped (cluster + why)
- Cluster 7 "infrastructure" (0263, 0536, 0538): Agent never ran (0263) or verifier build infra issues (0536/0538). Cannot fix with prompt changes.
- Did not attempt overly specific guidance for individual ArgoCD/osbuild tasks — the "read test code" and "fix WHERE test expects" rules are the general fix for the wrong-implementation cluster.
