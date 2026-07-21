# git-ai dev

## When to use
When asked to build, install, test, run, verify, debug, or sync git-ai. Also when investigating AI attribution issues, stash/rebase/cherry-pick attribution loss, or daemon problems.

## How to build

```bash
cargo build --release
# Binary: target/release/git-ai.exe (Windows) or target/release/git-ai (Linux/Mac)
```

## How to install

```bash
git-ai bg shutdown        # stop daemon
sleep 2                   # wait for process exit
cp target/release/git-ai.exe ~/.git-ai/bin/git-ai.exe
cp target/release/git-ai.exe ~/.git-ai/bin/git.exe
git-ai bg start           # restart daemon
sleep 2
git-ai --version          # verify
```

If `cp` fails with "Device or resource busy", use `git-ai bg shutdown --hard` and wait 5s.

## How to test

### Quick smoke test

```bash
echo "// AI: test" > src/test_ai.rs
echo "pub fn t() -> bool { true }" >> src/test_ai.rs
git add src/test_ai.rs && git commit -m "test: smoke"
sleep 5
git-ai stats HEAD --json
git notes --ref=ai show HEAD | head -5
```

### Expected: `ai_additions: N` with line-level attribution `s_xxx::t_yyy 1-N`

### If showing `unknown_additions`:
1. `git-ai install-hooks --check` — if "agents must be restarted", restart Claude Code
2. `git-ai bg status` — check `last_error` is null

### Full test suite

Test stash, cherry-pick, rebase attribution preservation. Create on a test branch, test, then delete the branch. See `doc/development-guide.md` §3.3 for complete test commands.

### Daemon debug

```bash
git-ai bg tail -n 50           # recent logs
git-ai bg tail -f              # live tail
export GIT_AI_DEBUG_DAEMON_TRACE=1
git-ai bg restart              # enable verbose event tracing
```

## Known fixes in this fork

### Stash attribution loss (fixed in `92b33ea`)
`ref_cursor.enrich_stash()` couldn't find the stash reflog entry in proxy mode. Added `stash_sha_from_git()` fallback using `git rev-parse --verify refs/stash`.

### Rebase attribution loss (fixed in `1bccef8`)
`rebase_new_tip_from_command` / `strict_rebase_original_head_from_command` only used `cmd.ref_changes`. Added `git_rev_parse()` fallback for HEAD / ORIG_HEAD.

### Cherry-pick
Upstream already has `resolve_cherry_pick_single_source_with_git` — no fix needed.

### Other git operations (commit, amend, reset, revert, merge)
Use `state.refs` (not `ref_changes`) — not affected by the reflog cursor timing issue.

## Sync upstream

```bash
git branch backup/pre-sync-$(date +%Y%m%d) origin/main
git fetch upstream
git rebase --onto upstream/main origin/main~N  # N = local commits count
cargo build --release
# install + test as above
git push --force origin main
```

Full details: `doc/development-guide.md` and `doc/upstream-sync-2026-07-20.md`.
