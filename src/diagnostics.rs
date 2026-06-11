use crate::authorship::authorship_log_serialization::AuthorshipLog;
use crate::authorship::working_log::CheckpointKind;
use crate::diagnostic_sentinels::{
    DEBUG_SELF_CHECK_REMOTE_URL, debug_self_check_root, path_is_in_debug_self_check_root,
};
use crate::git::repository::discover_repository_in_path_no_git_exec;
use serde_json::Value;
use std::fs;
use std::path::Path;
use std::process::Command;
use std::time::{Duration, Instant};

const SELF_CHECK_FILE: &str = "git-ai-debug-self-check.txt";
const SELF_CHECK_CONTENT_UNTRACKED: &str = "Untracked line\n";
const SELF_CHECK_CONTENT_KNOWN_HUMAN: &str = "Untracked line\nKnown human line\n";
const SELF_CHECK_CONTENT_AI: &str = "Untracked line\nKnown human line\nAI line\n";
const CHECKPOINT_WAIT: Duration = Duration::from_secs(10);
const NOTE_WAIT: Duration = Duration::from_secs(20);
const POLL_INTERVAL: Duration = Duration::from_millis(100);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DiagnosticStatus {
    Passed,
    Failed,
}

impl DiagnosticStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            DiagnosticStatus::Passed => "passed",
            DiagnosticStatus::Failed => "failed",
        }
    }
}

#[derive(Debug, Clone)]
pub struct CommandRecord {
    pub command: String,
    pub cwd: Option<String>,
    pub status: Option<i32>,
    pub stdout: String,
    pub stderr: String,
}

impl CommandRecord {
    fn success(&self) -> bool {
        self.status == Some(0)
    }
}

#[derive(Debug, Clone)]
pub struct DiagnosticCheckResult {
    pub status: DiagnosticStatus,
    pub summary: String,
    pub details: Vec<String>,
    pub commands: Vec<CommandRecord>,
    pub trace2_json: Option<String>,
}

impl DiagnosticCheckResult {
    fn passed(
        summary: impl Into<String>,
        details: Vec<String>,
        commands: Vec<CommandRecord>,
    ) -> Self {
        Self {
            status: DiagnosticStatus::Passed,
            summary: summary.into(),
            details,
            commands,
            trace2_json: None,
        }
    }

    fn failed(
        summary: impl Into<String>,
        details: Vec<String>,
        commands: Vec<CommandRecord>,
    ) -> Self {
        Self {
            status: DiagnosticStatus::Failed,
            summary: summary.into(),
            details,
            commands,
            trace2_json: None,
        }
    }

    fn with_trace2_json(mut self, trace2_json: Option<String>) -> Self {
        self.trace2_json = trace2_json;
        self
    }
}

#[derive(Debug, Clone)]
pub struct GitDiagnosticTarget {
    pub label: String,
    pub program: String,
}

impl GitDiagnosticTarget {
    pub fn new(label: impl Into<String>, program: impl Into<String>) -> Self {
        Self {
            label: label.into(),
            program: program.into(),
        }
    }
}

pub fn run_attribution_self_check(target: &GitDiagnosticTarget) -> DiagnosticCheckResult {
    let mut commands = Vec::new();
    let repo_path = debug_self_check_root().join(format!(
        "{}-{}",
        sanitize_label(&target.label),
        crate::uuid::generate_v4()
    ));
    let file_path = repo_path.join(SELF_CHECK_FILE);

    let result = (|| -> Result<Vec<String>, String> {
        fs::create_dir_all(&repo_path)
            .map_err(|e| format!("failed to create {}: {}", repo_path.display(), e))?;

        run_required(
            &mut commands,
            &target.program,
            &["init", "."],
            Some(&repo_path),
        )?;
        run_required(
            &mut commands,
            &target.program,
            &["config", "user.name", "Git AI Debug"],
            Some(&repo_path),
        )?;
        run_required(
            &mut commands,
            &target.program,
            &["config", "user.email", "debug-self-check@git-ai.invalid"],
            Some(&repo_path),
        )?;
        run_required(
            &mut commands,
            &target.program,
            &["remote", "add", "origin", DEBUG_SELF_CHECK_REMOTE_URL],
            Some(&repo_path),
        )?;

        fs::write(&file_path, SELF_CHECK_CONTENT_UNTRACKED)
            .map_err(|e| format!("failed to write {}: {}", file_path.display(), e))?;
        run_git_ai_checkpoint(&mut commands, &repo_path, "human")?;
        wait_for_checkpoint_count(&repo_path, 1)?;

        fs::write(&file_path, SELF_CHECK_CONTENT_KNOWN_HUMAN)
            .map_err(|e| format!("failed to write {}: {}", file_path.display(), e))?;
        run_git_ai_checkpoint(&mut commands, &repo_path, "mock_known_human")?;
        wait_for_checkpoint_count(&repo_path, 2)?;

        fs::write(&file_path, SELF_CHECK_CONTENT_AI)
            .map_err(|e| format!("failed to write {}: {}", file_path.display(), e))?;
        run_git_ai_checkpoint(&mut commands, &repo_path, "mock_ai")?;
        wait_for_checkpoint_count(&repo_path, 3)?;

        run_required(
            &mut commands,
            &target.program,
            &["add", SELF_CHECK_FILE],
            Some(&repo_path),
        )?;
        run_required(
            &mut commands,
            &target.program,
            &["commit", "-m", "git-ai debug self check"],
            Some(&repo_path),
        )?;

        let commit_sha = run_required(
            &mut commands,
            &target.program,
            &["rev-parse", "HEAD"],
            Some(&repo_path),
        )?
        .stdout
        .trim()
        .to_string();

        let note = poll_authorship_note(&mut commands, &target.program, &repo_path)?;
        let mut details = validate_self_check_authorship_note(&note)?;
        details.insert(0, format!("repo: {}", repo_path.display()));
        details.insert(1, format!("commit: {}", commit_sha));
        Ok(details)
    })();

    match result {
        Ok(details) => {
            let _ = fs::remove_dir_all(&repo_path);
            DiagnosticCheckResult::passed("attribution self-check completed", details, commands)
        }
        Err(err) => {
            let mut details = vec![format!("repo: {}", repo_path.display()), err];
            if path_is_in_debug_self_check_root(&repo_path) {
                details.push(
                    "failed self-check repository was left in place for inspection".to_string(),
                );
            }
            DiagnosticCheckResult::failed("attribution self-check failed", details, commands)
        }
    }
}

pub fn run_trace2_file_self_check(target: &GitDiagnosticTarget) -> DiagnosticCheckResult {
    let mut commands = Vec::new();
    let trace_dir = crate::mdm::utils::home_dir()
        .join(".git-ai")
        .join("internal")
        .join("daemon");
    let trace_path = trace_dir.join(format!(
        "trace2-debug-check-{}-{}.json",
        sanitize_label(&target.label),
        crate::uuid::generate_v4()
    ));

    let snapshot = match snapshot_global_trace2_event_target(&mut commands, &target.program) {
        Ok(snapshot) => snapshot,
        Err(err) => {
            return DiagnosticCheckResult::failed(
                "trace2 file self-check failed",
                vec![err],
                commands,
            );
        }
    };

    let mut changed_global_event_target = false;
    let result = (|| -> Result<(Vec<String>, String), String> {
        fs::create_dir_all(&trace_dir)
            .map_err(|e| format!("failed to create {}: {}", trace_dir.display(), e))?;
        let _ = fs::remove_file(&trace_path);
        let trace_path_string = trace_path.to_string_lossy().to_string();

        run_required(
            &mut commands,
            &target.program,
            &[
                "config",
                "--global",
                "--replace-all",
                "trace2.eventTarget",
                trace_path_string.as_str(),
            ],
            None,
        )?;
        changed_global_event_target = true;

        run_required(&mut commands, &target.program, &["version"], None)?;

        let trace2_json = fs::read_to_string(&trace_path)
            .map_err(|e| format!("failed to read {}: {}", trace_path.display(), e))?;
        let details = validate_trace2_version_command_events(&trace2_json)?;
        Ok((details, trace2_json))
    })();

    let restore_result = if changed_global_event_target {
        restore_global_trace2_event_target(&mut commands, &target.program, &snapshot)
    } else {
        Ok(())
    };
    let _ = fs::remove_file(&trace_path);

    match (result, restore_result) {
        (Ok((mut details, trace2_json)), Ok(())) => {
            details.insert(0, format!("trace2 file: {}", trace_path.display()));
            DiagnosticCheckResult::passed("trace2 file self-check completed", details, commands)
                .with_trace2_json(Some(trace2_json))
        }
        (Ok((mut details, trace2_json)), Err(restore_err)) => {
            details.push(format!("restore failed: {}", restore_err));
            DiagnosticCheckResult::failed("trace2 file self-check failed", details, commands)
                .with_trace2_json(Some(trace2_json))
        }
        (Err(err), Ok(())) => DiagnosticCheckResult::failed(
            "trace2 file self-check failed",
            vec![format!("trace2 file: {}", trace_path.display()), err],
            commands,
        ),
        (Err(err), Err(restore_err)) => DiagnosticCheckResult::failed(
            "trace2 file self-check failed",
            vec![
                format!("trace2 file: {}", trace_path.display()),
                err,
                format!("restore failed: {}", restore_err),
            ],
            commands,
        ),
    }
}

fn run_git_ai_checkpoint(
    commands: &mut Vec<CommandRecord>,
    repo_path: &Path,
    preset: &str,
) -> Result<CommandRecord, String> {
    let git_ai = std::env::current_exe()
        .map_err(|e| format!("failed to resolve git-ai binary path: {}", e))?;
    let git_ai = git_ai.to_string_lossy().to_string();
    run_required(
        commands,
        &git_ai,
        &["checkpoint", preset, SELF_CHECK_FILE],
        Some(repo_path),
    )
}

fn run_required(
    commands: &mut Vec<CommandRecord>,
    program: &str,
    args: &[&str],
    cwd: Option<&Path>,
) -> Result<CommandRecord, String> {
    let record = run_logged_command(program, args, cwd);
    let success = record.success();
    let error = if success {
        None
    } else {
        Some(format!(
            "command failed: {} (status={})",
            record.command,
            format_status(record.status)
        ))
    };
    commands.push(record.clone());
    match error {
        Some(error) => Err(error),
        None => Ok(record),
    }
}

fn run_logged_command(program: &str, args: &[&str], cwd: Option<&Path>) -> CommandRecord {
    let output = Command::new(program)
        .args(args)
        .current_dir(cwd.unwrap_or_else(|| Path::new(".")))
        .output();
    let command = format_command(program, args);
    match output {
        Ok(output) => CommandRecord {
            command,
            cwd: cwd.map(|p| p.display().to_string()),
            status: output.status.code(),
            stdout: String::from_utf8_lossy(&output.stdout).trim().to_string(),
            stderr: String::from_utf8_lossy(&output.stderr).trim().to_string(),
        },
        Err(e) => CommandRecord {
            command,
            cwd: cwd.map(|p| p.display().to_string()),
            status: None,
            stdout: String::new(),
            stderr: format!("failed to execute: {}", e),
        },
    }
}

fn wait_for_checkpoint_count(repo_path: &Path, expected_min_count: usize) -> Result<(), String> {
    let start = Instant::now();
    let mut last_error = None;

    while start.elapsed() < CHECKPOINT_WAIT {
        match read_checkpoint_count(repo_path) {
            Ok(count) if count >= expected_min_count => return Ok(()),
            Ok(count) => {
                last_error = Some(format!(
                    "only {} checkpoint(s) visible, expected at least {}",
                    count, expected_min_count
                ));
            }
            Err(e) => last_error = Some(e),
        }
        std::thread::sleep(POLL_INTERVAL);
    }

    Err(format!(
        "timed out waiting for checkpoint persistence: {}",
        last_error.unwrap_or_else(|| "no checkpoint status available".to_string())
    ))
}

fn read_checkpoint_count(repo_path: &Path) -> Result<usize, String> {
    let repo = discover_repository_in_path_no_git_exec(repo_path).map_err(|e| e.to_string())?;
    let working_log = repo
        .storage
        .working_log_for_base_commit("initial")
        .map_err(|e| e.to_string())?;
    working_log
        .read_all_checkpoints()
        .map(|checkpoints| checkpoints.len())
        .map_err(|e| e.to_string())
}

fn poll_authorship_note(
    commands: &mut Vec<CommandRecord>,
    git_program: &str,
    repo_path: &Path,
) -> Result<String, String> {
    let start = Instant::now();
    let mut last_record = None;

    while start.elapsed() < NOTE_WAIT {
        let record = run_logged_command(
            git_program,
            &["notes", "--ref=ai", "show", "HEAD"],
            Some(repo_path),
        );
        if record.success() && !record.stdout.trim().is_empty() {
            let note = record.stdout.clone();
            commands.push(record);
            return Ok(note);
        }
        last_record = Some(record);
        std::thread::sleep(POLL_INTERVAL);
    }

    if let Some(record) = last_record {
        commands.push(record);
    }
    Err("timed out waiting for authorship note on HEAD".to_string())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum LineClassification {
    Untracked,
    KnownHuman,
    Ai,
    Unknown,
}

impl LineClassification {
    fn as_str(self) -> &'static str {
        match self {
            LineClassification::Untracked => "untracked",
            LineClassification::KnownHuman => "known_human",
            LineClassification::Ai => "ai",
            LineClassification::Unknown => "unknown",
        }
    }
}

fn validate_self_check_authorship_note(note: &str) -> Result<Vec<String>, String> {
    let authorship = AuthorshipLog::deserialize_from_string(note)
        .map_err(|e| format!("failed to parse authorship note: {}", e))?;

    let expected = [
        (1, LineClassification::Untracked),
        (2, LineClassification::KnownHuman),
        (3, LineClassification::Ai),
    ];
    let mut details = Vec::new();

    for (line, expected_class) in expected {
        let actual = classify_line(&authorship, SELF_CHECK_FILE, line);
        details.push(format!(
            "line {}: {} (expected {})",
            line,
            actual.as_str(),
            expected_class.as_str()
        ));
        if actual != expected_class {
            return Err(format!(
                "unexpected attribution for line {}: got {}, expected {}\n{}",
                line,
                actual.as_str(),
                expected_class.as_str(),
                note
            ));
        }
    }

    Ok(details)
}

fn classify_line(authorship: &AuthorshipLog, file: &str, line: u32) -> LineClassification {
    let Some(file_attestation) = authorship
        .attestations
        .iter()
        .find(|attestation| attestation.file_path == file)
    else {
        return LineClassification::Untracked;
    };

    for entry in file_attestation.entries.iter().rev() {
        if !entry.line_ranges.iter().any(|range| range.contains(line)) {
            continue;
        }

        if entry.hash.starts_with("h_") && authorship.metadata.humans.contains_key(&entry.hash) {
            return LineClassification::KnownHuman;
        }

        if authorship
            .metadata
            .prompts
            .get(&entry.hash)
            .is_some_and(|prompt| prompt.agent_id.tool == "mock_ai")
        {
            return LineClassification::Ai;
        }

        if entry.hash.starts_with("s_") {
            let session_key = entry.hash.split("::").next().unwrap_or(&entry.hash);
            if authorship
                .metadata
                .sessions
                .get(session_key)
                .is_some_and(|session| session.agent_id.tool == "mock_ai")
            {
                return LineClassification::Ai;
            }
        }

        if entry.hash == CheckpointKind::Human.to_str() {
            return LineClassification::Untracked;
        }

        return LineClassification::Unknown;
    }

    LineClassification::Untracked
}

#[derive(Debug, Clone, Default)]
struct Trace2EventTargetSnapshot {
    values: Vec<String>,
}

fn snapshot_global_trace2_event_target(
    commands: &mut Vec<CommandRecord>,
    git_program: &str,
) -> Result<Trace2EventTargetSnapshot, String> {
    let record = run_logged_command(
        git_program,
        &[
            "config",
            "--global",
            "--no-includes",
            "--get-all",
            "trace2.eventTarget",
        ],
        None,
    );
    let snapshot = if record.success() {
        record
            .stdout
            .lines()
            .map(str::trim)
            .filter(|line| !line.is_empty())
            .map(ToOwned::to_owned)
            .collect()
    } else if record.status == Some(1) {
        Vec::new()
    } else {
        let err = format!(
            "failed to snapshot global trace2.eventTarget: status={}, stderr={}",
            format_status(record.status),
            record.stderr
        );
        commands.push(record);
        return Err(err);
    };
    commands.push(record);
    Ok(Trace2EventTargetSnapshot { values: snapshot })
}

fn restore_global_trace2_event_target(
    commands: &mut Vec<CommandRecord>,
    git_program: &str,
    snapshot: &Trace2EventTargetSnapshot,
) -> Result<(), String> {
    let remove = run_logged_command(
        git_program,
        &["config", "--global", "--unset-all", "trace2.eventTarget"],
        None,
    );
    let remove_ok = remove.success() || remove.status == Some(5);
    let remove_error = if remove_ok {
        None
    } else {
        Some(format!(
            "failed to remove temporary trace2.eventTarget: status={}, stderr={}",
            format_status(remove.status),
            remove.stderr
        ))
    };
    commands.push(remove);
    if let Some(error) = remove_error {
        return Err(error);
    }

    for value in &snapshot.values {
        let record = run_logged_command(
            git_program,
            &["config", "--global", "--add", "trace2.eventTarget", value],
            None,
        );
        let error = if record.success() {
            None
        } else {
            Some(format!(
                "failed to restore trace2.eventTarget: status={}, stderr={}",
                format_status(record.status),
                record.stderr
            ))
        };
        commands.push(record);
        if let Some(error) = error {
            return Err(error);
        }
    }

    Ok(())
}

fn validate_trace2_version_command_events(trace2_json: &str) -> Result<Vec<String>, String> {
    let mut events = Vec::new();
    for (idx, line) in trace2_json.lines().enumerate() {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        let value: Value = serde_json::from_str(trimmed)
            .map_err(|e| format!("invalid trace2 JSON on line {}: {}", idx + 1, e))?;
        events.push(value);
    }

    if events.is_empty() {
        return Err("trace2 file was empty".to_string());
    }

    let has_version = events
        .iter()
        .any(|event| event.get("event").and_then(Value::as_str) == Some("version"));
    let has_start = events
        .iter()
        .any(|event| event.get("event").and_then(Value::as_str) == Some("start"));
    let has_cmd_name_version = events.iter().any(|event| {
        event.get("event").and_then(Value::as_str) == Some("cmd_name")
            && event.get("name").and_then(Value::as_str) == Some("version")
    });
    let has_exit_zero = events.iter().any(|event| {
        event.get("event").and_then(Value::as_str) == Some("exit")
            && event.get("code").and_then(Value::as_i64) == Some(0)
    });
    let has_atexit_zero = events.iter().any(|event| {
        event.get("event").and_then(Value::as_str) == Some("atexit")
            && event.get("code").and_then(Value::as_i64) == Some(0)
    });

    let failures = [
        (has_version, "missing version event"),
        (has_start, "missing start event"),
        (has_cmd_name_version, "missing cmd_name event for version"),
        (has_exit_zero, "missing exit event with code 0"),
        (has_atexit_zero, "missing atexit event with code 0"),
    ]
    .into_iter()
    .filter_map(|(ok, msg)| (!ok).then_some(msg))
    .collect::<Vec<_>>();

    if !failures.is_empty() {
        return Err(format!("unexpected trace2 events: {}", failures.join(", ")));
    }

    Ok(vec![
        format!("events: {}", events.len()),
        "validated: version/start/cmd_name(version)/exit(0)/atexit(0)".to_string(),
    ])
}

fn sanitize_label(label: &str) -> String {
    let sanitized = label
        .chars()
        .map(|ch| {
            if ch.is_ascii_alphanumeric() || ch == '-' || ch == '_' {
                ch
            } else {
                '-'
            }
        })
        .collect::<String>();
    let trimmed = sanitized.trim_matches('-');
    if trimmed.is_empty() {
        "git".to_string()
    } else {
        trimmed.to_lowercase()
    }
}

fn format_command(program: &str, args: &[&str]) -> String {
    std::iter::once(program)
        .chain(args.iter().copied())
        .map(shell_quote_for_display)
        .collect::<Vec<_>>()
        .join(" ")
}

fn shell_quote_for_display(value: &str) -> String {
    if value
        .chars()
        .all(|ch| ch.is_ascii_alphanumeric() || "-_./:=@".contains(ch))
    {
        value.to_string()
    } else {
        format!("'{}'", value.replace('\'', "'\\''"))
    }
}

fn format_status(status: Option<i32>) -> String {
    status
        .map(|code| code.to_string())
        .unwrap_or_else(|| "unavailable".to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_validate_trace2_version_command_events_accepts_expected_events() {
        let trace = r#"{"event":"version"}
{"event":"start","argv":["git","version"]}
{"event":"cmd_name","name":"version"}
{"event":"exit","code":0}
{"event":"atexit","code":0}
"#;

        let details = validate_trace2_version_command_events(trace).unwrap();
        assert!(details.iter().any(|detail| detail == "events: 5"));
    }

    #[test]
    fn test_validate_trace2_version_command_events_rejects_missing_cmd_name() {
        let trace = r#"{"event":"version"}
{"event":"start","argv":["git","version"]}
{"event":"exit","code":0}
{"event":"atexit","code":0}
"#;

        let err = validate_trace2_version_command_events(trace).unwrap_err();
        assert!(err.contains("missing cmd_name event for version"));
    }
}
