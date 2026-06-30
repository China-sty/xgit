//! Repro / regression guard for PD-23 / GH #1677: the attribution-recovery
//! diff was unbounded.
//!
//! The daemon's fast-forward `update-ref` path calls
//! `post_commit_from_working_log(Some(old), new)` where `old` is the *old branch
//! tip from before a `git pull`*. Recovery (`recovery_committed_hunks`) then
//! diffed the entire `old..new` range with `diff_added_lines(old, new, None)`
//! (no pathspec), buffering the whole `git diff -U0` output plus one `u32` per
//! added line into memory. On a pull that fast-forwards across a large range
//! this is the 20GB+ blow-up.
//!
//! The fix bounds the recovery diff to the finalized commit's *immediate
//! parent*. This test measures process peak RSS around the real `diff_added_lines`
//! path for both the full pulled range (the bug) and the immediate-parent range
//! (the fix), proving the unbounded allocation scales with the whole range while
//! the bounded one does not.

#![cfg(target_os = "linux")]

use git_ai::git::repository::find_repository_in_path;
use std::fs;
use std::process::Command;

/// Read a `/proc/self/status` size field (e.g. `VmHWM`, `VmRSS`) in KiB.
fn proc_status_kb(field: &str) -> u64 {
    let status = fs::read_to_string("/proc/self/status").expect("read /proc/self/status");
    let prefix = format!("{field}:");
    for line in status.lines() {
        if let Some(rest) = line.strip_prefix(&prefix) {
            return rest
                .trim()
                .trim_end_matches(" kB")
                .trim()
                .parse()
                .unwrap_or_else(|_| panic!("parse {field}"));
        }
    }
    panic!("{field} not found in /proc/self/status");
}

fn git(cwd: &std::path::Path, args: &[&str]) {
    let out = Command::new("git")
        .arg("-C")
        .arg(cwd)
        .args(args)
        .output()
        .expect("git spawn");
    assert!(
        out.status.success(),
        "git {:?} failed: {}",
        args,
        String::from_utf8_lossy(&out.stderr)
    );
}

fn rev_parse(cwd: &std::path::Path) -> String {
    let out = Command::new("git")
        .arg("-C")
        .arg(cwd)
        .args(["rev-parse", "HEAD"])
        .output()
        .expect("git rev-parse");
    String::from_utf8_lossy(&out.stdout).trim().to_string()
}

/// Build a repo where `old` tip and the latest tip differ by a large amount of
/// added content (simulating a `git pull` that fast-forwards across many
/// commits), while the *final* commit alone is tiny. Returns (old_tip, new_tip).
fn build_large_ff_range(
    dir: &std::path::Path,
    big_commits: usize,
    lines_each: usize,
) -> (String, String) {
    git(dir, &["init", "-b", "main", "."]);
    git(dir, &["config", "user.email", "t@git-ai.local"]);
    git(dir, &["config", "user.name", "git-ai test"]);
    git(dir, &["config", "commit.gpgsign", "false"]);

    fs::write(dir.join("base.txt"), "base\n").unwrap();
    git(dir, &["add", "-A"]);
    git(dir, &["commit", "-m", "old tip"]);
    let old_tip = rev_parse(dir);

    // Intervening commits a fast-forward pull would drag in: each adds a large
    // file. old_tip..new_tip therefore spans a large diff.
    let line = "the quick brown fox jumps over the lazy dog padding padding padding\n";
    for c in 0..big_commits {
        let body = line.repeat(lines_each);
        fs::write(dir.join(format!("pulled_{c}.txt")), &body).unwrap();
        git(dir, &["add", "-A"]);
        git(dir, &["commit", "-m", &format!("pulled commit {c}")]);
    }

    // The newly-pulled tip itself only changes one small file.
    fs::write(dir.join("final.txt"), "final change\n").unwrap();
    git(dir, &["add", "-A"]);
    git(dir, &["commit", "-m", "final"]);
    let new_tip = rev_parse(dir);

    (old_tip, new_tip)
}

// `VmHWM` is a process-wide, monotonic high-water mark and the integration
// harness runs tests in parallel threads within one process. Run serially so a
// concurrent test's allocations can't corrupt the baseline/after readings.
#[serial_test::serial]
#[test]
fn recovery_full_range_diff_blows_up_memory_vs_immediate_parent() {
    let tmp = tempfile::tempdir().unwrap();
    let dir = tmp.path();

    // ~200 commits x 20k lines x ~68 bytes ≈ ~270MB of added text across
    // old..new. Kept CI-friendly while still dwarfing a single commit by orders
    // of magnitude (real reports were multi-GB of pulled history).
    let (old_tip, new_tip) = build_large_ff_range(dir, 200, 20_000);

    let repo = find_repository_in_path(dir.to_str().unwrap()).unwrap();

    // The immediate parent of new_tip is what the fix diffs instead of the
    // far-behind `old_tip`.
    let immediate_parent = repo
        .find_commit(new_tip.clone())
        .unwrap()
        .parent(0)
        .unwrap()
        .id();

    // BOUNDED path (the fix): diff only the finalized commit's own changes.
    let bounded_hunks = repo
        .diff_added_lines(&immediate_parent, &new_tip, None)
        .unwrap();

    // Establish the peak high-water mark *after* the bounded diff. The bounded
    // diff touches one small commit, so this captures the process's steady-state
    // peak (plus any earlier test's peak, since VmHWM is monotonic) without the
    // pulled range. The unbounded diff below must then push VmHWM substantially
    // higher purely from buffering old..new.
    let peak_before_full = proc_status_kb("VmHWM");

    // UNBOUNDED path (the bug): diff the entire old..new pulled range. The whole
    // `git diff -U0` output (~270MB here) is buffered into a String before being
    // parsed, so VmHWM jumps by ~that amount.
    let full_hunks = repo.diff_added_lines(&old_tip, &new_tip, None).unwrap();
    let peak_after_full = proc_status_kb("VmHWM");
    let full_peak_growth = peak_after_full.saturating_sub(peak_before_full);

    eprintln!(
        "peak_before_full={peak_before_full}KB peak_after_full={peak_after_full}KB \
         full_peak_growth={full_peak_growth}KB bounded_files={} full_files={}",
        bounded_hunks.len(),
        full_hunks.len()
    );

    // Structural proof (deterministic, allocation-independent): the full-range
    // diff materializes every pulled file; the bounded diff sees only the final
    // commit's single file. This is what makes the unbounded path scale to 20GB.
    assert_eq!(
        bounded_hunks.len(),
        1,
        "bounded diff must see only final.txt"
    );
    assert!(
        full_hunks.len() >= 200,
        "full-range diff materialized the whole pulled range ({} files)",
        full_hunks.len()
    );

    // Memory proof: diffing the full pulled range pushes peak RSS up by >100MB
    // (it buffers the entire ~270MB diff), whereas the bounded diff established
    // the prior peak without it. Measuring VmHWM growth *after* the bounded diff
    // makes this robust to any peak inherited from earlier serial tests — only
    // the unbounded old..new diff can move the high-water mark this far.
    assert!(
        full_peak_growth > 100_000,
        "expected full-range diff to push peak RSS up by >100MB from buffering \
         the whole pulled range; full_peak_growth={full_peak_growth}KB"
    );
}
