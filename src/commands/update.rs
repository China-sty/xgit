use std::process::Command;

#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x08000000;

pub fn run() {
    #[cfg(windows)]
    {
        use std::fs;
        println!("Starting update via PowerShell...");
        // Get the update script URL from config or fallback to default
        let default_url = "https://gist.githubusercontent.com/China-sty/649413cd6d108990f81638fc1837479f/raw/xgit.ps1";
        let script_url = crate::config::Config::get().update_script_url().unwrap_or(default_url);

        let pid = std::process::id();
        let log_dir = dirs::home_dir()
            .unwrap_or_else(|| std::path::PathBuf::from("."))
            .join(".git-ai")
            .join("update-logs");

        // Ensure the log directory exists
        let _ = fs::create_dir_all(&log_dir);

        let log_file = log_dir.join(format!("update-{}.log", pid));
        let log_path_str = log_file.to_string_lossy().to_string();

        let _ = fs::write(&log_file, format!("Starting update at PID {}\n", pid));

        // Construct the PowerShell command
        let ps_wrapper = format!(
            "$logFile = '{}'; \
             Start-Transcript -Path $logFile -Append -Force | Out-Null; \
             Write-Host 'Running update script from {}...'; \
             try {{ \
                 $ErrorActionPreference = 'Continue'; \
                 irm '{}' | iex; \
                 Write-Host 'Update script completed'; \
             }} catch {{ \
                 Write-Host \"Error: $_\"; \
                 Write-Host \"Stack trace: $($_.ScriptStackTrace)\"; \
             }} finally {{ \
                 Stop-Transcript | Out-Null; \
             }}",
            log_path_str, script_url, script_url
        );

        // We run a detached PowerShell process to avoid file locking issues when the script attempts to overwrite the current executable.
        let mut cmd = Command::new("powershell");
        cmd.arg("-NoProfile")
            .arg("-ExecutionPolicy")
            .arg("Bypass")
            .arg("-Command")
            .arg(ps_wrapper);

        // Hide the spawned console to prevent any host/UI bleed-through
        cmd.creation_flags(CREATE_NO_WINDOW);

        match cmd.spawn() {
            Ok(_) => {
                println!(
                    "\x1b[1;33mNote: The update script is running in the background.\x1b[0m"
                );
                println!("Fetching from: {}", script_url);
                println!("Check the log file for progress: {}", log_path_str);
                println!("This allows the current git-ai process to exit and release file locks.");
                // Immediately exit so this process doesn't lock the executable
                std::process::exit(0);
            }
            Err(e) => {
                eprintln!("Failed to run update script: {}", e);
                std::process::exit(1);
            }
        }
    }

    #[cfg(not(windows))]
    {
        use std::fs;
        println!("Starting update via bash...");
        // Get the update script URL from config or fallback to default
        let default_url = "http://10.99.33.39:8080/release_linux/script/install_inner.sh";
        let script_url = crate::config::Config::get().update_script_url().unwrap_or(default_url);

        let pid = std::process::id();
        let log_dir = dirs::home_dir()
            .unwrap_or_else(|| std::path::PathBuf::from("."))
            .join(".git-ai")
            .join("update-logs");

        // Ensure the log directory exists
        let _ = fs::create_dir_all(&log_dir);

        let log_file = log_dir.join(format!("update-{}.log", pid));
        let log_path_str = log_file.to_string_lossy().to_string();

        let _ = fs::write(&log_file, format!("Starting update at PID {}\n", pid));

        // Construct the bash command
        let command_str = format!(
            "{{ echo 'Running update script from {}...'; curl -s '{}' | bash; echo 'Update script completed'; }} >> '{}' 2>&1",
            script_url, script_url, log_path_str
        );

        let mut cmd = Command::new("bash");
        cmd.arg("-c").arg(command_str);

        match cmd.spawn() {
            Ok(_) => {
                println!(
                    "\x1b[1;33mNote: The update script is running in the background.\x1b[0m"
                );
                println!("Fetching from: {}", script_url);
                println!("Check the log file for progress: {}", log_path_str);
                println!("This allows the current git-ai process to exit.");
                // Immediately exit
                std::process::exit(0);
            }
            Err(e) => {
                eprintln!("Failed to run update script: {}", e);
                std::process::exit(1);
            }
        }
    }
}
