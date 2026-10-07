# Terminal /goal Auto-Resume Scheduler

A lightweight, automated macOS tool with an interactive Web Selector UI designed to resume long-running tasks in Terminal windows (such as OpenAI Codex `/goal resume` sessions) exactly when rate limits reset.


## Features

- **Automated Terminal Discovery**: Automatically scans running processes (`codex1`, `codex2`, `codex3`), identifies associated macOS Terminal.app windows, parses prompt buffers, and detects usage limit reset timestamps.
- **Visual Screen Layout Classification**: Automatically determines screen coordinates (e.g. `Top-Right`, `Bottom-Right`, `Bottom-Left` on Desktop 3).
- **Interactive Web UI (`http://localhost:8765`)**:
  - Live preview of terminal buffers and reset times.
  - **"🔍 Bring Window to Front"** button to instantly highlight and bring target windows forward.
  - **"↵ Test Enter Now"** button for immediate manual dry runs.
  - Select checkboxes and customizable target time fields.
  - Master **"🚀 Arm Scheduler Now"** and **"🛑 Disarm / Cancel"** buttons.
  - Live countdown timers and streaming activity logs.
- **Mac Sleep Prevention**: Keeps macOS awake overnight while armed via `caffeinate -disu`.
- **Clock-Skew Retry Protection**: At the scheduled trigger time, the window is brought to front and the Return key (`key code 36`) is dispatched. If remote servers have a slight clock difference, it verifies prompt acceptance and retries every 20 seconds (up to 5 attempts).
- **Zero External Dependencies**: Pure Python 3 standard library (`http.server`, `subprocess`, `threading`, `urllib`).

---

## Installation & Usage

### 1. Clone the repository
```bash
git clone https://github.com/Wandersport/terminal-auto-resume.git
cd terminal-auto-resume
```

### 2. Run the Web Selector UI
```bash
python3 resume_terminals.py
```
This starts the local web server and opens `http://localhost:8765` in your default browser.

### 3. CLI Auto-Arm Mode (Daemon)
To immediately auto-arm the scheduler in the background without manual configuration:
```bash
python3 resume_terminals.py --auto
```

### 4. Optional Symlink
To run `resume-terminals` directly from anywhere in your shell:
```bash
ln -sf "$(pwd)/resume_terminals.py" ~/.local/bin/resume-terminals
```

---

## License

MIT
