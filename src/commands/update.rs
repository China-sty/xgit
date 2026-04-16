use std::process::Command;

#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x08000000;

pub fn run() {
    #[cfg(windows)]
    {
        println!("Starting update via PowerShell...");
        // Get the update script URL from config or fallback to default
        let default_url = "https://gist.githubusercontent.com/China-sty/649413cd6d108990f81638fc1837479f/raw/xgit.ps1";
        let script_url = crate::config::Config::get().update_script_url().unwrap_or(default_url);

        // Construct the PowerShell command
        let command_str = format!("irm {} | iex", script_url);

        // We run a detached PowerShell process to avoid file locking issues when the script attempts to overwrite the current executable.
        let mut cmd = Command::new("powershell");
        cmd.arg("-NoProfile")
            .arg("-ExecutionPolicy")
            .arg("Bypass")
            .arg("-Command")
            .arg(command_str);

        // Hide the spawned console to prevent any host/UI bleed-through
        cmd.creation_flags(CREATE_NO_WINDOW);

        match cmd.spawn() {
            Ok(_) => {
                println!(
                    "\x1b[1;33mNote: The update script is running in the background.\x1b[0m"
                );
                println!("Fetching from: {}", script_url);
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
        println!("Starting update via bash...");
        // Get the update script URL from config or fallback to default
        let default_url = "https://gist.githubusercontent.com/China-sty/3977a3abe1aff04f5090908b8494751f/raw/xgit.sh";
        let script_url = crate::config::Config::get().update_script_url().unwrap_or(default_url);

        // Construct the bash command
        let command_str = format!("curl -sSL {} | bash", script_url);

        let mut cmd = Command::new("bash");
        cmd.arg("-c").arg(command_str);

        match cmd.spawn() {
            Ok(_) => {
                println!(
                    "\x1b[1;33mNote: The update script is running in the background.\x1b[0m"
                );
                println!("Fetching from: {}", script_url);
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
