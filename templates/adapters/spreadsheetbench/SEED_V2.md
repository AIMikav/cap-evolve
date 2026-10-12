# v2 seed layout (capabilities `[system-prompt, skill]`)

```
prompt.md  task_template.md                       # same contract as seed_capability/
skills/<name>/SKILL.md                            # frontmatter (name, description) + when to call each script
skills/<name>/scripts/*.py                        # empty here; the optimizer writes task-agnostic helpers
```

Run with `helpers.enabled: true` (or `CAPEVOLVE_HELPERS=1`) and `surface.executable_dirs: ["skills/*/scripts"]`.
The adapter then mounts only the current candidate's `skills/` read-only at `/mnt/skills` (a second bind, outside
`spreadsheet/`) and substitutes `{skills_root}` in `prompt.md` / `task_template.md`. With helpers off this
directory behaves as v1 plus an inert `skills/` tree. It is a different editable surface from `seed_capability/`:
register it as its own experiment, never pool its rows with v1.
