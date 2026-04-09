use crate::authorship::stats::stats_for_commit_stats;
use crate::commands::git_handlers::CommandHooksContext;
use crate::commands::hooks::commit_hooks::get_commit_default_author;
use crate::commands::upgrade;
use crate::git::cli_parser::{ParsedGitInvocation, is_dry_run};
use crate::git::repository::{Repository, disable_internal_git_hooks, find_repository};
use crate::git::rewrite_log::RewriteLogEvent;
use crate::git::sync_authorship::push_authorship_notes;
use crate::utils::debug_log;
use std::sync::mpsc;
use std::time::Duration;

fn amend_commit_with_ai_rate(repository: &Repository) {
    let Ok(head) = repository.head() else { return };
    let Ok(head_sha) = head.target() else { return };
    
    let repo_clone = repository.clone();
    let head_sha_clone = head_sha.clone();
    
    let (tx, rx) = mpsc::channel();
    
    std::thread::spawn(move || {
        let stats = stats_for_commit_stats(&repo_clone, &head_sha_clone, &[]);
        let _ = tx.send(stats);
    });
    
    let ai_rate = match rx.recv_timeout(Duration::from_secs(2)) {
        Ok(Ok(stats)) => {
            let total = stats.human_additions + stats.ai_additions;
            if total > 0 {
                ((stats.ai_additions as f64 / total as f64) * 100.0).round() as u32
            } else {
                0
            }
        },
        _ => 0,
    };
    
    let append_line = format!("ai代码渗透率：{}%", ai_rate);
    
    let mut args = repository.global_args_for_exec();
    args.push("log".to_string());
    args.push("-1".to_string());
    args.push("--format=%B".to_string());
    args.push(head_sha.clone());
    
    if let Ok(output) = crate::git::repository::exec_git(&args) {
        if let Ok(msg) = String::from_utf8(output.stdout) {
            let msg = msg.trim_end();
            if msg.contains(&append_line) {
                return;
            }
            
            let new_msg_lines: Vec<&str> = msg.lines().filter(|l| !l.starts_with("ai代码渗透率：")).collect();
            let mut new_msg = new_msg_lines.join("\n");
            new_msg.push_str("\n\n");
            new_msg.push_str(&append_line);
            new_msg.push_str("\n");
            
            let _disable_hooks_guard = disable_internal_git_hooks();
            let mut amend_args = repository.global_args_for_exec();
            amend_args.push("commit".to_string());
            amend_args.push("--amend".to_string());
            amend_args.push("-m".to_string());
            amend_args.push(new_msg);
            
            debug_log(&format!("Amending commit with AI rate: {}", ai_rate));
            if let Ok(_) = crate::git::repository::exec_git(&amend_args) {
                let mut repo_for_rewrite = repository.clone();
                if let Ok(new_head) = repo_for_rewrite.head() {
                    if let Ok(new_sha) = new_head.target() {
                        if new_sha != head_sha {
                            let commit_author = get_commit_default_author(&repo_for_rewrite, &[]);
                            repo_for_rewrite.handle_rewrite_log_event(
                                RewriteLogEvent::commit_amend(head_sha, new_sha),
                                commit_author,
                                true,
                                true,
                            );
                        }
                    }
                }
            }
        }
    }
}

pub fn push_pre_command_hook(
    parsed_args: &ParsedGitInvocation,
    repository: &Repository,
) -> Option<std::thread::JoinHandle<()>> {
    upgrade::maybe_schedule_background_update_check();

    // Early returns for cases where we shouldn't push authorship notes
    if should_skip_authorship_push(&parsed_args.command_args) {
        return None;
    }

    amend_commit_with_ai_rate(repository);

    // Intercept push to execute with "refs/notes/ai" first, as requested
    let mut pre_push_args = repository.global_args_for_exec();
    pre_push_args.push("push".to_string());
    pre_push_args.extend(parsed_args.command_args.clone());
    pre_push_args.push("refs/notes/ai".to_string());
    
    debug_log("Executing pre-push with refs/notes/ai");
    let _ = crate::git::repository::exec_git(&pre_push_args);

    let remote = resolve_push_remote(parsed_args, repository);

    if let Some(remote) = remote {
        debug_log(&format!(
            "started pushing authorship notes to remote: {}",
            remote
        ));
        // Clone what we need for the background thread
        let global_args = repository.global_args_for_exec();

        // Spawn background thread to push authorship notes in parallel with main push
        Some(std::thread::spawn(move || {
            // Recreate repository in the background thread
            if let Ok(repo) = find_repository(&global_args) {
                if let Err(e) = push_authorship_notes(&repo, &remote) {
                    debug_log(&format!("authorship push failed: {}", e));
                }
            } else {
                debug_log("failed to open repository for authorship push");
            }
        }))
    } else {
        // No remotes configured; skip silently
        debug_log("no remotes found for authorship push; skipping");
        None
    }
}

pub fn run_pre_push_hook_managed(parsed_args: &ParsedGitInvocation, repository: &Repository) {
    upgrade::maybe_schedule_background_update_check();

    if should_skip_authorship_push(&parsed_args.command_args) {
        return;
    }

    let Some(remote) = resolve_push_remote(parsed_args, repository) else {
        debug_log("no remotes found for authorship push; skipping");
        return;
    };

    debug_log(&format!(
        "started pushing authorship notes to remote: {}",
        remote
    ));

    if let Err(e) = push_authorship_notes(repository, &remote) {
        debug_log(&format!("authorship push failed: {}", e));
    }
}

pub fn push_post_command_hook(
    _repository: &Repository,
    _parsed_args: &ParsedGitInvocation,
    _exit_status: std::process::ExitStatus,
    command_hooks_context: &mut CommandHooksContext,
) {
    // Always wait for the authorship push thread to complete if it was started,
    // regardless of whether the main push succeeded or failed.
    // This ensures proper cleanup of the background thread.
    if let Some(handle) = command_hooks_context.push_authorship_handle.take() {
        let _ = handle.join();
    }
}

fn should_skip_authorship_push(command_args: &[String]) -> bool {
    is_dry_run(command_args)
        || command_args.iter().any(|a| a == "-d" || a == "--delete")
        || command_args.iter().any(|a| a == "--mirror")
}

fn resolve_push_remote(
    parsed_args: &ParsedGitInvocation,
    repository: &Repository,
) -> Option<String> {
    let remotes = repository.remotes().ok();
    let remote_names: Vec<String> = remotes
        .as_ref()
        .map(|r| {
            (0..r.len())
                .filter_map(|i| r.get(i).map(|s| s.to_string()))
                .collect()
        })
        .unwrap_or_default();
    let upstream_remote = repository.upstream_remote().ok().flatten();
    let default_remote = repository.get_default_remote().ok().flatten();

    resolve_push_remote_from_parts(
        &parsed_args.command_args,
        &remote_names,
        upstream_remote,
        default_remote,
    )
}

fn resolve_push_remote_from_parts(
    command_args: &[String],
    known_remotes: &[String],
    upstream_remote: Option<String>,
    default_remote: Option<String>,
) -> Option<String> {
    let positional_remote = extract_remote_from_push_args(command_args, known_remotes);

    let specified_remote = positional_remote.or_else(|| {
        command_args
            .iter()
            .find(|arg| known_remotes.iter().any(|remote| remote == *arg))
            .cloned()
    });

    specified_remote.or(upstream_remote).or(default_remote)
}

fn extract_remote_from_push_args(args: &[String], known_remotes: &[String]) -> Option<String> {
    let mut i = 0;
    while i < args.len() {
        let arg = &args[i];
        if arg == "--" {
            return args.get(i + 1).cloned();
        }
        if arg.starts_with('-') {
            if let Some((flag, value)) = is_push_option_with_inline_value(arg) {
                if flag == "--repo" {
                    return Some(value.to_string());
                }
                i += 1;
                continue;
            }

            if option_consumes_separate_value(arg.as_str()) {
                if arg == "--repo" {
                    return args.get(i + 1).cloned();
                }
                i += 2;
                continue;
            }

            i += 1;
            continue;
        }
        return Some(arg.clone());
    }

    known_remotes
        .iter()
        .find(|r| args.iter().any(|arg| arg == *r))
        .cloned()
}

fn is_push_option_with_inline_value(arg: &str) -> Option<(&str, &str)> {
    if let Some((flag, value)) = arg.split_once('=') {
        Some((flag, value))
    } else if (arg.starts_with("-C") || arg.starts_with("-c")) && arg.len() > 2 {
        // Treat -C<path> or -c<name>=<value> as inline values
        let flag = &arg[..2];
        let value = &arg[2..];
        Some((flag, value))
    } else {
        None
    }
}

fn option_consumes_separate_value(arg: &str) -> bool {
    matches!(
        arg,
        "--repo" | "--receive-pack" | "--exec" | "-o" | "--push-option" | "-c" | "-C"
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn strings(args: &[&str]) -> Vec<String> {
        args.iter().map(|arg| (*arg).to_string()).collect()
    }

    #[test]
    fn skip_authorship_push_when_dry_run() {
        assert!(should_skip_authorship_push(&strings(&["--dry-run"])));
    }

    #[test]
    fn skip_authorship_push_when_delete() {
        assert!(should_skip_authorship_push(&strings(&["--delete"])));
        assert!(should_skip_authorship_push(&strings(&["-d"])));
    }

    #[test]
    fn skip_authorship_push_when_mirror() {
        assert!(should_skip_authorship_push(&strings(&["--mirror"])));
    }

    #[test]
    fn resolve_push_remote_prefers_positional_remote() {
        let args = strings(&["origin", "main"]);
        let remote = resolve_push_remote_from_parts(
            &args,
            &strings(&["origin", "upstream"]),
            Some("upstream".to_string()),
            Some("origin".to_string()),
        );
        assert_eq!(remote.as_deref(), Some("origin"));
    }

    #[test]
    fn resolve_push_remote_prefers_repo_flag() {
        let args = strings(&["--repo", "upstream", "HEAD"]);
        let remote = resolve_push_remote_from_parts(
            &args,
            &strings(&["origin", "upstream"]),
            Some("origin".to_string()),
            None,
        );
        assert_eq!(remote.as_deref(), Some("upstream"));
    }

    #[test]
    fn resolve_push_remote_falls_back_to_upstream_then_default() {
        let args = Vec::<String>::new();
        let with_upstream = resolve_push_remote_from_parts(
            &args,
            &strings(&["origin"]),
            Some("upstream".to_string()),
            Some("origin".to_string()),
        );
        assert_eq!(with_upstream.as_deref(), Some("upstream"));

        let with_default = resolve_push_remote_from_parts(
            &args,
            &strings(&["origin"]),
            None,
            Some("origin".to_string()),
        );
        assert_eq!(with_default.as_deref(), Some("origin"));
    }
}
