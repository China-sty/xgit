use std::process::Command;

#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x08000000;

pub fn run() {
    println!("Starting update via PowerShell...");

    #[cfg(windows)]
    {
        // Construct the PowerShell command
        let command_str = "irm https://gist.githubusercontent.com/China-sty/649413cd6d108990f81638fc1837479f/raw/xgit.ps1 | iex";

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
        eprintln!("The 'update' command is only supported on Windows.");
        std::process::exit(1);
    }
}
