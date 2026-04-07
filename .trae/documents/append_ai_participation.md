# Plan: Append AI Participation to Commit Message

## Summary
The goal is to append the AI participation percentage to the commit message upon `git commit`. We will calculate the AI stats for the currently staged changes before the commit opens the editor or finalizes the `-m` message, and then inject the AI stats into the commit command arguments.

## Current State Analysis
- `git-ai` wraps `git` and intercepts `git commit` via `commit_pre_command_hook`.
- The AI stats (`CommitStats`) are usually computed *after* the commit using `post_commit_hook`.
- `VirtualAttributions` can split changes into committed and uncommitted parts if we provide a commit SHA.
- We can determine staged AI stats by using `git write-tree` and `git commit-tree` to create a temporary commit representing the index, and running `VirtualAttributions::to_authorship_log_and_initial_working_log` against it.
- `git commit` allows injecting templates via `-t` or multiple message paragraphs via `-m`.

## Proposed Changes
1. **Add Feature Flag (`src/feature_flags.rs`)**
   - Add `append_ai_stats: append_ai_stats, debug = false, release = false` to `define_feature_flags!`. This allows users to opt-in via `GIT_AI_APPEND_AI_STATS=true` or config file.

2. **Add `stats_for_staged_changes` Helper (`src/authorship/stats.rs`)**
   - Implement `stats_for_staged_changes(repo, ignore_patterns)`:
     1. Creates a temporary commit from the index using `git write-tree` and `git commit-tree`.
     2. Calls `VirtualAttributions::from_just_working_log` for the working directory state.
     3. Calls `to_authorship_log_and_initial_working_log` with the temporary commit SHA to isolate staged AI lines.
     4. Calculates and returns `CommitStats` (same logic as `stats_for_commit_stats`).

3. **Update `commit_pre_command_hook` Signature (`src/commands/hooks/commit_hooks.rs`)**
   - Change `parsed_args: &ParsedGitInvocation` to `&mut ParsedGitInvocation` to allow modifying arguments.

4. **Inject AI Stats into Commit Args (`src/commands/hooks/commit_hooks.rs`)**
   - If the `append_ai_stats` feature flag is enabled, calculate the AI participation percentage using `stats_for_staged_changes`.
   - If `parsed_args` has `-m` or `--message`, push `-m "AI Participation: X%"` to `command_args`.
   - If no `-F`, `-C`, `-c`, or `--amend` are present (meaning an editor will open):
     - Read the user's default `commit.template` if it exists.
     - Append `\n\nAI Participation: X%\n` to it.
     - Write to `.git/AI_COMMIT_TEMPLATE`.
     - Push `-t` and `.git/AI_COMMIT_TEMPLATE` to `command_args`. This opens the editor pre-filled with the template and AI stats, while preserving the Git status lines!

## Assumptions & Decisions
- We assume the user wants this behind an opt-in feature flag `append_ai_stats` to prevent modifying commit messages unexpectedly for all users.
- We use a temporary commit tree (`git write-tree` + `git commit-tree`) to accurately determine staged AI stats without affecting the working directory or the actual commit process.
- We inject the message natively via Git's CLI (`-m` and `-t`) rather than trying to intercept the editor process directly.

## Verification
- Ensure `cargo check` and `cargo test` pass.
- Verify `git commit -m "..."` correctly appends the paragraph.
- Verify `git commit` correctly opens the editor with the AI participation text inside the template.