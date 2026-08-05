# PROCESS — what I did this iteration (explainability; REQUIRED)

## Ranked issue list (clusters by # failing tasks x trials, biggest first)
| rank | cluster | tasks | shared root cause | tag | planned change class |
| --- | --- | --- | --- | --- | --- |
| 1 | Wrong implementation (ArgoCD, osbuild) | 12 (0543,0544,0547,0550,0552,0554,0562,0604,0606,0608,0611,0613) | Agent fixes close-but-wrong: doesn't read test assertions to understand expected behavior, codes wrong detail | KNOWLEDGE | Add "read test code" to Step 3 |
| 2 | Buildah/storage P2P (env) | 5 (0098,0102,0103,0161,0163) | P2P tests require root/kernel capabilities (mount ns, xattr, CLONE_NEWNS) unavailable in sandbox | ENVIRONMENTAL | SKIP — unfixable with prompt |
| 3 | Molecule over-broad edits | 4 (0037,0039,0059,0082) | Agent rewrites too much code, cascade editing, false confidence | BEHAVIORAL | "Read test code" may help 0037 understand correct fix location |
| 4 | Kubevirt P2P (go.work.sum) | 3 (0357,0359,0360) | 17 P2P failures caused by dirty go.work.sum (52.7KB diff) in testbed. Agent solves F2P but doesn't revert go.work.sum | KNOWLEDGE | Add go.work.sum to cleanup list |
| 5 | Context exhaustion | 2 (0536,0538) | Agent exhausts context on large C codebases | INFRASTRUCTURE | SKIP — unfixable with prompt (already has "plan before reading" rule) |
| 6 | kubectl P2P (0278) | 1 | Agent's fix correct but go.mod/go.sum contamination; agent didn't follow existing cleanup rule | BEHAVIORAL | go.work.sum edit reinforces cleanup; "read test code" may help |
| 7 | Infrastructure (0263) | 1 | Agent never ran | INFRASTRUCTURE | SKIP — unfixable |

## Changes made this iteration (one row per edit — aim for MULTIPLE classes)
| cluster | edit class | file / tool | what & why it generalizes | protects passing? |
| --- | --- | --- | --- | --- |
| 1 (wrong-impl), 3 (molecule) | Add missing rule (Step 3) | SKILL.md, prompt.md | Added "When a test is failing, read its source code — the assertions tell you what output or behavior is expected, which guides you to the correct fix location." Addresses knowledge gap: agent doesn't know to read test assertions before coding, codes close-but-wrong fixes. | BOUNDED: only adds a pre-coding investigation step when tests are failing. Passing tasks already fix correct location and pass all tests. No new behavioral constraint — just points agent to test source as a diagnostic aid. |
| 4 (kubevirt P2P) | Add missing rule (cleanup) | SKILL.md, prompt.md | Added go.work.sum to the list of files to revert before final diff, plus "Running `go test` or `go build` can silently modify these files — always revert them." Addresses knowledge gap: agent doesn't know go.work.sum should be reverted. | BOUNDED: only adds one file to existing cleanup list. Passing tasks already produce clean diffs. The go.work.sum file is not intentionally modified by any task. |

## Verify-the-fix (one line per change)
- Edit 1 (read test code): trace task-0543 shows agent changed error message text without reading test assertions; if agent reads test, it would see exact expected message. trace task-0554 shows agent added repo URL to errors but format didn't match test expectation. "Read test code" sentence guides agent to check assertions first. Passing tasks (0258, 0309, 0319, 0323, etc.) already pass all tests — the sentence only activates "when a test is failing" so passing tasks' behavior is unchanged.
- Edit 2 (go.work.sum): trace task-0357 shows 52.7KB go.work.sum diff present in final output, causing 17 P2P failures. New cleanup rule tells agent to revert go.work.sum → agent would run `git checkout -- go.work.sum` → clean diff → P2P tests pass. Passing Go tasks (0132, 0180, 0183, 0194, 0202, etc.) already revert go.sum/go.mod per existing rule; adding go.work.sum to the same list doesn't change their behavior (they don't produce go.work.sum diffs or already revert accidental changes).

## Process & features used
- Subagents: 4 parallel diagnostic subagents to read and cluster all 28 failing trajectories
- Prior iterations I read: cand_0001, cand_0002 (both ACCEPTED), cand_0003, cand_0004 (both REJECTED) — diffs, PROCESS.md, JOURNAL.md
- Selective re-introduction: Both cand_0003 and cand_0004 included "read test code" and both fixed tasks 0037 and 0278. I re-introduced ONLY "read test code" and "go.work.sum cleanup" — did NOT re-introduce nil-safety or shared-root-cause rules (present in both rejected iters, likely contributed to regressions).

## Good things to PRESERVE (do not let a future iteration undo these)
- The Step B rewrite from iter 1 (investigate pre-existing failures as seeded bugs) — core insight that fixed 17 tasks
- The "never create test files" rule — prevents verifier conflicts
- The "never rename test functions" rule — preserves test identity
- The "never revert to HEAD" rule — prevents empty diffs
- The Go build check — catches compilation errors
- The "don't dismiss as environmental" rule — catches code bugs
- The broader test coverage rule (Step A) — catches P2P failures
- The stash safety rule — prevents lost fixes

## Deliberately skipped (cluster + why)
- Cluster 2 buildah/storage P2P (5 tasks): ENVIRONMENTAL — tests require root/kernel capabilities (mount, xattr, CLONE_NEWNS) absent from sandbox. Agent's code fixes are correct. Confirmed by diagnostic agent reading traces. Cannot fix with prompt.
- Cluster 5 context exhaustion (2 tasks): Agent exhausts context window on large C codebases (PCP). Already has "plan before reading" rule. Further prompt additions would waste context for all tasks.
- Cluster 7 infrastructure (1 task, 0263): Agent never ran. Zero-length trace. Cannot fix.
- Did NOT re-introduce nil-safety rule from cand_0003/0004 — present in both rejected iters, adds cognitive load for ALL Go tasks, may have contributed to 0573 regression (common to both rejected iters).
- Did NOT re-introduce shared-root-cause rule from cand_0003/0004 — present in both rejected iters, may cause agent to waste context searching for patterns that don't exist.
- Did NOT rewrite Step A to "go test ./... from ROOT" — cand_0003 tried this and broke 0229.
- Did NOT add "MUST fix seeded bugs" emphasis — cand_0003 tried this and contributed to regressions.
