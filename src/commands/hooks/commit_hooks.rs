use crate::authorship::pre_commit;
use crate::commands::git_handlers::CommandHooksContext;
use crate::git::cli_parser::{ParsedGitInvocation, is_dry_run};
use crate::git::repository::Repository;
use crate::git::rewrite_log::RewriteLogEvent;
use crate::utils::debug_log;

pub fn commit_pre_command_hook(
    parsed_args: &ParsedGitInvocation,
    repository: &mut Repository,
) -> bool {
    if is_dry_run(&parsed_args.command_args) {
        return false;
    }

    // store HEAD context for post-command hook
    repository.require_pre_command_head();

    let default_author = get_commit_default_author(repository, &parsed_args.command_args);

    // Run pre-commit logic
    if let Err(e) = pre_commit::pre_commit(repository, default_author.clone()) {
        if e.to_string()
            .contains("Cannot run checkpoint on bare repositories")
        {
            eprintln!(
                "Cannot run checkpoint on bare repositories (skipping git-ai pre-commit hook)"
            );
            return false;
        }
        eprintln!("Pre-commit failed: {}", e);
        std::process::exit(1);
    }
    true
}

pub fn commit_post_command_hook(
    parsed_args: &ParsedGitInvocation,
    exit_status: std::process::ExitStatus,
    repository: &mut Repository,
    command_hooks_context: &mut CommandHooksContext,
) {
    if is_dry_run(&parsed_args.command_args) {
        return;
    }

    if !exit_status.success() {
        return;
    }

    if let Some(pre_commit_hook_result) = command_hooks_context.pre_commit_hook_result
        && !pre_commit_hook_result
    {
        debug_log("Skipping git-ai post-commit hook because pre-commit hook failed");
        return;
    }

    let supress_output = parsed_args.has_command_flag("--porcelain")
        || parsed_args.has_command_flag("--quiet")
        || parsed_args.has_command_flag("-q")
        || parsed_args.has_command_flag("--no-status");

    let original_commit = repository.pre_command_base_commit.clone();
    let new_sha = repository.head().ok().and_then(|h| h.target().ok());

    // empty repo, commit did not land
    if new_sha.is_none() {
        return;
    }

    let commit_author = get_commit_default_author(repository, &parsed_args.command_args);
    if parsed_args.has_command_flag("--amend") {
        if let (Some(orig), Some(sha)) = (original_commit.clone(), new_sha.clone()) {
            repository.handle_rewrite_log_event(
                RewriteLogEvent::commit_amend(orig, sha),
                commit_author.clone(),
                supress_output,
                true,
            );
        } else {
            repository.handle_rewrite_log_event(
                RewriteLogEvent::commit(original_commit, new_sha.clone().unwrap()),
                commit_author.clone(),
                supress_output,
                true,
            );
        }
    } else {
        repository.handle_rewrite_log_event(
            RewriteLogEvent::commit(original_commit, new_sha.clone().unwrap()),
            commit_author.clone(),
            supress_output,
            true,
        );
    }

    // Now, apply the "事后 Amend 追加法" to append "ai生成"
    if let Some(sha) = new_sha {
        // Skip if it's a merge commit
        let is_merge = repository
            .find_commit(sha.clone())
            .map(|c| c.parent_count().unwrap_or(0) > 1)
            .unwrap_or(false);

        if !is_merge {
            let mut log_args = repository.global_args_for_exec();
            log_args.push("log".to_string());
            log_args.push("-1".to_string());
            log_args.push("--pretty=%B".to_string());
            log_args.push(sha.clone());

            let message = if let Ok(output) = crate::git::repository::exec_git(&log_args) {
                String::from_utf8_lossy(&output.stdout).trim().to_string()
            } else {
                String::new()
            };

            if !message.is_empty() && !message.contains("ai参与率") {
                // Calculate AI participation rate
                let ai_rate = if let Ok(stats) = crate::authorship::stats::stats_for_commit_stats(&repository, &sha, &[]) {
                    let total = stats.human_additions + stats.ai_additions;
                    if total > 0 {
                        ((stats.ai_additions as f64 / total as f64) * 100.0).round() as u32
                    } else {
                        0
                    }
                } else {
                    0
                };

                let new_message = format!("{}\n\nai参与率: {}%", message, ai_rate);

                use crate::git::repository::disable_internal_git_hooks;
                let _guard = disable_internal_git_hooks();

                let mut amend_args = repository.global_args_for_exec();
                amend_args.push("commit".to_string());
                amend_args.push("--amend".to_string());
                amend_args.push("-m".to_string());
                amend_args.push(new_message);

                if let Ok(output) = crate::git::repository::exec_git(&amend_args) {
                    if output.status.success() {
                        if let Some(final_sha) = repository.head().ok().and_then(|h| h.target().ok()) {
                            if final_sha != sha {
                                repository.handle_rewrite_log_event(
                                    RewriteLogEvent::commit_amend(sha, final_sha),
                                    commit_author,
                                    supress_output,
                                    true,
                                );
                            }
                        }
                    } else {
                        crate::utils::debug_log(&format!("Failed to amend commit message: {:?}", output));
                    }
                }
            }
        }
    }
}

pub fn get_commit_default_author(repo: &Repository, args: &[String]) -> String {
    // According to git commit manual, --author flag overrides all other author information
    if let Some(author_spec) = extract_author_from_args(args)
        && let Ok(Some(resolved_author)) = repo.resolve_author_spec(&author_spec)
        && !resolved_author.trim().is_empty()
    {
        return resolved_author.trim().to_string();
    }

    // Use git_commit_author_identity() which resolves via `git var GIT_AUTHOR_IDENT`
    // (respects full author precedence: GIT_AUTHOR_NAME/EMAIL env > user.name/email config > system defaults)
    // then falls back to git config user.name/user.email.
    let identity = repo.git_commit_author_identity();
    let mut author_name = identity.name;
    let mut author_email = identity.email;

    // Check EMAIL environment variable as fallback for both name and email
    if (author_name.is_none() || author_email.is_none())
        && let Ok(email) = std::env::var("EMAIL")
        && !email.trim().is_empty()
    {
        // Extract name part from email if we don't have a name yet
        if author_name.is_none()
            && let Some(at_pos) = email.find('@')
        {
            let name_part = &email[..at_pos];
            if !name_part.is_empty() {
                author_name = Some(name_part.to_string());
            }
        }
        // Use as email if we don't have an email yet
        if author_email.is_none() {
            author_email = Some(email.trim().to_string());
        }
    }

    // Format the author string based on what we have
    match (author_name, author_email) {
        (Some(name), Some(email)) => format!("{} <{}>", name, email),
        (Some(name), None) => name,
        (None, Some(email)) => email,
        (None, None) => {
            eprintln!("Warning: No author information found. Using 'unknown' as author.");
            "unknown".to_string()
        }
    }
}

fn extract_author_from_args(args: &[String]) -> Option<String> {
    let mut i = 0;
    while i < args.len() {
        let arg = &args[i];

        // Handle --author=<author> format
        if let Some(author_value) = arg.strip_prefix("--author=") {
            return Some(author_value.to_string());
        }

        // Handle --author <author> format (separate arguments)
        if arg == "--author" && i + 1 < args.len() {
            return Some(args[i + 1].clone());
        }

        i += 1;
    }
    None
}
