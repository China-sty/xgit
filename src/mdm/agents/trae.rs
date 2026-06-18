use crate::error::GitAiError;
use crate::mdm::hook_installer::{HookCheckResult, HookInstaller, HookInstallerParams};
use crate::mdm::utils::{
    binary_exists, generate_diff, home_dir, is_git_ai_checkpoint_command, to_windows_git_bash_style_path,
    write_atomic,
};
use serde_json::{Value, json};
use std::fs;
use std::path::PathBuf;

// Command patterns for hooks
const TRAE_PRE_TOOL_CMD: &str = "checkpoint trae --hook-input stdin";
const TRAE_POST_TOOL_CMD: &str = "checkpoint trae --hook-input stdin";

pub struct TraeInstaller;

impl TraeInstaller {
    fn settings_paths() -> Vec<PathBuf> {
        vec![
            home_dir().join(".trae").join("hooks.json"),
            home_dir().join(".trae-cn").join("hooks.json")
        ]
    }
}

impl HookInstaller for TraeInstaller {
    fn name(&self) -> &str {
        "Trae"
    }

    fn id(&self) -> &str {
        "trae"
    }

    fn check_hooks(&self, _params: &HookInstallerParams) -> Result<HookCheckResult, GitAiError> {
        let has_binary = binary_exists("trae");
        let has_dotfiles = home_dir().join(".trae").exists() || home_dir().join(".trae-cn").exists();

        if !has_binary && !has_dotfiles {
            return Ok(HookCheckResult {
                tool_installed: false,
                hooks_installed: false,
                hooks_up_to_date: false,
            });
        }

        let paths = Self::settings_paths();
        let mut any_installed = false;
        let mut any_up_to_date = false;

        for settings_path in paths {
            if !settings_path.exists() {
                continue;
            }

            if let Ok(content) = fs::read_to_string(&settings_path) {
                let existing: Value = serde_json::from_str(&content).unwrap_or_else(|_| json!({}));
                let has_hooks = existing
                    .get("hooks")
                    .and_then(|h| h.get("PreToolUse"))
                    .and_then(|v| v.as_array())
                    .map(|arr| {
                        arr.iter().any(|item| {
                            item.get("hooks")
                                .and_then(|h| h.as_array())
                                .map(|hooks| {
                                    hooks.iter().any(|hook| {
                                        hook.get("command")
                                            .and_then(|c| c.as_str())
                                            .map(is_git_ai_checkpoint_command)
                                            .unwrap_or(false)
                                    })
                                })
                                .unwrap_or(false)
                        })
                    })
                    .unwrap_or(false);

                if has_hooks {
                    any_installed = true;
                    any_up_to_date = true; // If installed, assume up to date for now
                }
            }
        }

        Ok(HookCheckResult {
            tool_installed: true,
            hooks_installed: any_installed,
            hooks_up_to_date: any_up_to_date,
        })
    }

    fn process_names(&self) -> Vec<&str> {
        vec!["trae"]
    }

    fn install_hooks(
        &self,
        params: &HookInstallerParams,
        dry_run: bool,
    ) -> Result<Option<String>, GitAiError> {
        let paths = Self::settings_paths();
        let mut diffs = Vec::new();
        let mut changed_any = false;

        for settings_path in paths {
            // Read existing content as string
            let existing_content = if settings_path.exists() {
                fs::read_to_string(&settings_path).unwrap_or_default()
            } else {
                // If it doesn't exist, we only create it if the parent directory exists
                // (which means the user has installed that version of Trae)
                if let Some(parent) = settings_path.parent() {
                    if !parent.exists() {
                        continue; // Skip this version if not installed
                    }
                }
                String::new()
            };

            // Parse existing JSON if present, else start with empty object
            let existing: Value = if existing_content.trim().is_empty() {
                json!({})
            } else {
                serde_json::from_str(&existing_content).unwrap_or_else(|_| json!({}))
            };

            // Build commands with absolute path
            let binary_path_str = to_windows_git_bash_style_path(&params.binary_path);
            let pre_tool_cmd = format!("{} {}", binary_path_str, TRAE_PRE_TOOL_CMD);
            let post_tool_cmd = format!("{} {}", binary_path_str, TRAE_POST_TOOL_CMD);

            let desired_hooks = json!({
                "PreToolUse": {
                    "matcher": "Write|Edit|MultiEdit",
                    "desired_cmd": pre_tool_cmd,
                },
                "PostToolUse": {
                    "matcher": "Write|Edit|MultiEdit",
                    "desired_cmd": post_tool_cmd,
                }
            });

            // Merge desired into existing
            let mut merged = existing.clone();
            let mut hooks_obj = merged.get("hooks").cloned().unwrap_or_else(|| json!({}));

            // Process both PreToolUse and PostToolUse
            for hook_type in &["PreToolUse", "PostToolUse"] {
                let desired_matcher = desired_hooks[hook_type]["matcher"].as_str().unwrap();
                let desired_cmd = desired_hooks[hook_type]["desired_cmd"].as_str().unwrap();

                // Get or create the hooks array for this type
                let mut hook_type_array = hooks_obj
                    .get(*hook_type)
                    .and_then(|v| v.as_array())
                    .cloned()
                    .unwrap_or_default();

                // Find existing matcher block for Write|Edit|MultiEdit
                let mut found_matcher_idx: Option<usize> = None;
                for (idx, item) in hook_type_array.iter().enumerate() {
                    if let Some(matcher) = item.get("matcher").and_then(|m| m.as_str())
                        && matcher == desired_matcher
                    {
                        found_matcher_idx = Some(idx);
                        break;
                    }
                }

                let matcher_idx = match found_matcher_idx {
                    Some(idx) => idx,
                    None => {
                        // Create new matcher block
                        hook_type_array.push(json!({
                            "matcher": desired_matcher,
                            "hooks": []
                        }));
                        hook_type_array.len() - 1
                    }
                };

                // Get the hooks array within this matcher block
                let mut hooks_array = hook_type_array[matcher_idx]
                    .get("hooks")
                    .and_then(|h| h.as_array())
                    .cloned()
                    .unwrap_or_default();

                // Update outdated git-ai checkpoint commands
                let mut found_idx: Option<usize> = None;
                let mut needs_update = false;

                for (idx, hook) in hooks_array.iter().enumerate() {
                    if let Some(cmd) = hook.get("command").and_then(|c| c.as_str())
                        && is_git_ai_checkpoint_command(cmd)
                        && found_idx.is_none()
                    {
                        found_idx = Some(idx);
                        if cmd != desired_cmd {
                            needs_update = true;
                        }
                    }
                }

                match found_idx {
                    Some(idx) => {
                        if needs_update {
                            hooks_array[idx] = json!({
                                "type": "command",
                                "command": desired_cmd
                            });
                        }
                        // Remove any duplicate git-ai checkpoint commands
                        let keep_idx = idx;
                        let mut current_idx = 0;
                        hooks_array.retain(|hook| {
                            if current_idx == keep_idx {
                                current_idx += 1;
                                true
                            } else if let Some(cmd) = hook.get("command").and_then(|c| c.as_str()) {
                                let is_dup = is_git_ai_checkpoint_command(cmd);
                                current_idx += 1;
                                !is_dup
                            } else {
                                current_idx += 1;
                                true
                            }
                        });
                    }
                    None => {
                        // No existing command found, add new one
                        hooks_array.push(json!({
                            "type": "command",
                            "command": desired_cmd
                        }));
                    }
                }

                // Write back the hooks array to the matcher block
                if let Some(matcher_block) = hook_type_array[matcher_idx].as_object_mut() {
                    matcher_block.insert("hooks".to_string(), Value::Array(hooks_array));
                }

                // Write back the updated hook_type_array
                if let Some(obj) = hooks_obj.as_object_mut() {
                    obj.insert(hook_type.to_string(), Value::Array(hook_type_array));
                }
            }

            // Write back hooks to merged
            if let Some(root) = merged.as_object_mut() {
                root.insert("hooks".to_string(), hooks_obj);
            }

            // Check if there are semantic changes (compare JSON values, not strings)
            if existing == merged {
                continue;
            }

            // Generate new content
            let new_content = serde_json::to_string_pretty(&merged).unwrap_or_default();

            // Generate diff
            let diff_output = generate_diff(&settings_path, &existing_content, &new_content);
            diffs.push(diff_output);
            changed_any = true;

            // Write if not dry-run
            if !dry_run {
                if let Some(dir) = settings_path.parent() {
                    fs::create_dir_all(dir)?;
                }
                write_atomic(&settings_path, new_content.as_bytes())?;
            }
        }

        if !changed_any {
            Ok(None)
        } else {
            Ok(Some(diffs.join("\n\n")))
        }
    }

    fn uninstall_hooks(
        &self,
        _params: &HookInstallerParams,
        dry_run: bool,
    ) -> Result<Option<String>, GitAiError> {
        let paths = Self::settings_paths();
        let mut diffs = Vec::new();
        let mut changed_any = false;

        for settings_path in paths {
            if !settings_path.exists() {
                continue;
            }

            let existing_content = fs::read_to_string(&settings_path).unwrap_or_default();
            if existing_content.trim().is_empty() {
                continue;
            }

            let existing: Value = serde_json::from_str(&existing_content).unwrap_or_else(|_| json!({}));

            let mut merged = existing.clone();
            let mut hooks_obj = match merged.get("hooks").cloned() {
                Some(h) => h,
                None => continue,
            };

            let mut changed = false;

            for hook_type in &["PreToolUse", "PostToolUse"] {
                if let Some(hook_type_array) =
                    hooks_obj.get_mut(*hook_type).and_then(|v| v.as_array_mut())
                {
                    for matcher_block in hook_type_array.iter_mut() {
                        if let Some(hooks_array) = matcher_block
                            .get_mut("hooks")
                            .and_then(|h| h.as_array_mut())
                        {
                            let original_len = hooks_array.len();
                            hooks_array.retain(|hook| {
                                if let Some(cmd) = hook.get("command").and_then(|c| c.as_str()) {
                                    !is_git_ai_checkpoint_command(cmd)
                                } else {
                                    true
                                }
                            });
                            if hooks_array.len() != original_len {
                                changed = true;
                            }
                        }
                    }
                }
            }

            if !changed {
                continue;
            }

            if let Some(root) = merged.as_object_mut() {
                root.insert("hooks".to_string(), hooks_obj);
            }

            let new_content = serde_json::to_string_pretty(&merged).unwrap_or_default();
            let diff_output = generate_diff(&settings_path, &existing_content, &new_content);
            diffs.push(diff_output);
            changed_any = true;

            if !dry_run {
                write_atomic(&settings_path, new_content.as_bytes())?;
            }
        }

        if !changed_any {
            Ok(None)
        } else {
            Ok(Some(diffs.join("\n\n")))
        }
    }
}

