#!/usr/bin/env python3
"""
Terminal /goal Auto-Resume Scheduler with Interactive Selector UI
Automates pressing Enter on designated Terminal windows at scheduled rate-limit reset times.
"""

import sys
import os
import re
import json
import time
import signal
import datetime
import threading
import subprocess
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

LOG_FILE = os.path.expanduser("~/.resume_terminals.log")
DEFAULT_PORT = 8765

def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{ts}] {msg}"
    print(formatted)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(formatted + "\n")
    except Exception:
        pass

# Global state
state_lock = threading.Lock()
scheduled_jobs = [] 
is_armed = False
caffeinate_proc = None
activity_logs = []

# Terminal caching
cached_terminals = []
cache_timestamp = 0
CACHE_TTL = 5.0 # seconds

def add_activity_log(msg):
    log(msg)
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    with state_lock:
        activity_logs.append(f"[{ts}] {msg}")
        if len(activity_logs) > 100:
            activity_logs.pop(0)

def classify_position(bounds):
    if not bounds or len(bounds) < 4:
        return "Desktop 3"
    left, top, right, bottom = bounds[0], bounds[1], bounds[2], bounds[3]
    if top < 300 and left >= 500:
        return "Top-Right Window (Desktop 3)"
    elif top >= 300 and left >= 500:
        return "Bottom-Right Window (Desktop 3)"
    elif left < 500:
        return "Bottom-Left Window (Desktop 3)"
    return "Desktop 3 Window"

def get_codex_terminals(force_refresh=False):
    """Detect all Terminal windows/tabs running Codex, identify accounts, positions and rate limits."""
    global cached_terminals, cache_timestamp
    now_ts = time.time()
    if not force_refresh and (now_ts - cache_timestamp < CACHE_TTL) and cached_terminals:
        return cached_terminals

    # 1. Get ps info for codex processes
    ps_proc = subprocess.run(["ps", "-eo", "pid,tty,command"], capture_output=True, text=True)
    pids_info = []
    for line in ps_proc.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) >= 3 and parts[2].startswith("codex"):
            pid, tty, cmd = parts[0], parts[1], parts[2]
            pids_info.append((pid, tty))

    terminals = []
    for pid, tty in pids_info:
        lsof_proc = subprocess.run(["lsof", "-p", pid], capture_output=True, text=True)
        out = lsof_proc.stdout
        acct = "Codex"
        acct_num = 1
        if ".codex-account3" in out:
            acct = "Codex 3 (Account 3)"
            acct_num = 3
        elif ".codex-account2" in out:
            acct = "Codex 2 (Account 2)"
            acct_num = 2
        elif ".codex" in out:
            acct = "Codex 1 (Account 1)"
            acct_num = 1
            
        dev_tty = f"/dev/{tty}" if not tty.startswith("/dev/") else tty

        # Query Terminal tab history and bounds
        tab_scpt = f"""
        tell application "Terminal"
            repeat with w in windows
                set wId to id of w
                set wName to name of w
                set wBounds to bounds of w
                repeat with t in tabs of w
                    if tty of t is "{dev_tty}" then
                        set bStr to (item 1 of wBounds as text) & "," & (item 2 of wBounds as text) & "," & (item 3 of wBounds as text) & "," & (item 4 of wBounds as text)
                        return (wId as text) & ":::" & (wName) & ":::" & bStr & ":::" & (history of t)
                    end if
                end repeat
            end repeat
            return ""
        end tell
        """
        tab_proc = subprocess.run(["osascript", "-e", tab_scpt], capture_output=True, text=True)
        tab_raw = tab_proc.stdout
        win_id = ""
        win_name = ""
        bounds = []
        hist = ""
        if ":::" in tab_raw:
            parts = tab_raw.split(":::", 3)
            win_id = parts[0].strip()
            win_name = parts[1].strip()
            try:
                bounds = [int(x.strip()) for x in parts[2].split(",")]
            except Exception:
                bounds = []
            hist = parts[3]
        else:
            hist = tab_raw

        lines = [l.strip() for l in hist.splitlines() if l.strip()]
        last_lines = lines[-6:] if len(lines) >= 6 else lines

        # Rate limit parse
        m = re.search(r"try again at ([A-Za-z]+ \d+(?:st|nd|rd|th)?, \d{4} \d+:\d+ [AP]M)", hist)
        reset_time_str = m.group(1) if m else None

        has_goal_resume = any("/goal resume" in l for l in last_lines)
        pos_desc = classify_position(bounds)

        terminals.append({
            "pid": pid,
            "tty": dev_tty,
            "win_id": win_id,
            "win_name": win_name,
            "bounds": bounds,
            "position": pos_desc,
            "account": acct,
            "account_num": acct_num,
            "reset_time_str": reset_time_str,
            "has_goal_resume": has_goal_resume,
            "last_lines": last_lines,
        })
        
    terminals.sort(key=lambda x: (0 if "Top-Right" in x["position"] else 1 if "Bottom-Right" in x["position"] else 2))
    cached_terminals = terminals
    cache_timestamp = time.time()
    return terminals

def highlight_window(tty):
    """Brings the specific terminal tab/window to the foreground and unminimizes it."""
    scpt = f"""
    tell application "Terminal"
        repeat with w in windows
            repeat with t in tabs of w
                if tty of t is "{tty}" then
                    if miniaturized of w is true then
                        set miniaturized of w to false
                    end if
                    set selected of t to true
                    set index of w to 1
                    activate
                    return "ok"
                end if
            end repeat
        end repeat
        return "not_found"
    end tell
    """
    res = subprocess.run(["osascript", "-e", scpt], capture_output=True, text=True)
    return res.stdout.strip()

def send_enter_to_terminal(tty):
    """Focuses the terminal window and sends the Return key (key code 36)."""
    scpt = f"""
    tell application "Terminal"
        repeat with w in windows
            repeat with t in tabs of w
                if tty of t is "{tty}" then
                    if miniaturized of w is true then
                        set miniaturized of w to false
                        delay 0.5
                    end if
                    set selected of t to true
                    set index of w to 1
                    activate
                    exit repeat
                end if
            end repeat
        end repeat
    end tell
    delay 0.4
    tell application "System Events"
        tell process "Terminal"
            key code 36
        end tell
    end tell
    """
    subprocess.run(["osascript", "-e", scpt], capture_output=True, text=True)

def parse_target_datetime(time_str):
    """Parses time like '03:40', '3:40 AM', '04:13' into a datetime object for the next occurrence."""
    now = datetime.datetime.now()
    clean = time_str.strip()
    
    dt = None
    for fmt in ["%H:%M", "%I:%M %p", "%I:%M%p", "%H:%M:%S", "%I:%M:%S %p"]:
        try:
            parsed_time = datetime.datetime.strptime(clean, fmt).time()
            dt = datetime.datetime.combine(now.date(), parsed_time)
            break
        except ValueError:
            pass
            
    if not dt:
        raise ValueError(f"Could not parse time string: '{time_str}'")

    if dt <= now:
        dt += datetime.timedelta(days=1)
        
    return dt

def start_caffeinate():
    global caffeinate_proc
    if caffeinate_proc is None or caffeinate_proc.poll() is not None:
        try:
            caffeinate_proc = subprocess.Popen(["caffeinate", "-disu", "-w", str(os.getpid())])
            add_activity_log(f"caffeinate started (PID {caffeinate_proc.pid}) - system sleep prevented.")
        except Exception as e:
            add_activity_log(f"Failed to start caffeinate: {e}")

def stop_caffeinate():
    global caffeinate_proc
    if caffeinate_proc is not None:
        try:
            caffeinate_proc.terminate()
            add_activity_log("caffeinate stopped.")
        except Exception:
            pass
        caffeinate_proc = None

def scheduler_thread_loop():
    global is_armed, scheduled_jobs
    while True:
        time.sleep(1)
        with state_lock:
            if not is_armed:
                continue
                
            now = datetime.datetime.now()
            all_done = True
            
            for job in scheduled_jobs:
                if job["status"] == "pending":
                    all_done = False
                    if now >= job["target_dt"]:
                        job["status"] = "executing"
                        add_activity_log(f"⏰ TARGET TIME REACHED for {job['label']} ({job['tty']})! Sending Enter...")
                        threading.Thread(target=execute_job, args=(job,), daemon=True).start()
                elif job["status"] == "executing" or job["status"] == "retrying":
                    all_done = False

            if all_done and len(scheduled_jobs) > 0 and all(j["status"] in ["completed", "failed"] for j in scheduled_jobs):
                add_activity_log("🎉 All scheduled terminal jobs have completed!")
                is_armed = False
                stop_caffeinate()

def execute_job(job):
    tty = job["tty"]
    label = job.get("label", tty)
    max_retries = job.get("max_retries", 5)
    
    # 1. Bring window to front and press enter
    add_activity_log(f"Activating {label} on {tty} and pressing Enter...")
    send_enter_to_terminal(tty)
    
    # 2. Verify response
    for attempt in range(max_retries):
        time.sleep(4)
        terminals = get_codex_terminals(force_refresh=True)
        target = next((t for t in terminals if t["tty"] == tty), None)
        
        if not target:
            add_activity_log(f"Warning: Could not read buffer for {tty}.")
            break
            
        last_lines = target.get("last_lines", [])
        still_limited = any("hit your usage limit" in l or "try again at" in l for l in last_lines)
        prompt_cleared = not any("› /goal resume" in l for l in last_lines)
        
        if prompt_cleared or not still_limited:
            add_activity_log(f"✅ SUCCESS: {label} on {tty} resumed! Prompt accepted.")
            with state_lock:
                job["status"] = "completed"
            return
        else:
            add_activity_log(f"⚠️ Notice: Rate limit still active on {label} (attempt {attempt+1}/{max_retries}). Server clock may have small skew. Waiting 20s to retry...")
            with state_lock:
                job["status"] = "retrying"
                job["retries"] = attempt + 1
            time.sleep(20)
            send_enter_to_terminal(tty)

    add_activity_log(f"Completed retry attempts for {label} on {tty}.")
    with state_lock:
        job["status"] = "completed"

# HTML Template
HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Codex /goal Auto-Resume Scheduler</title>
<style>
  :root {
    --bg: #0b0f19;
    --card: #151d2f;
    --border: #25334d;
    --text: #f8fafc;
    --muted: #94a3b8;
    --accent: #38bdf8;
    --accent-hover: #0284c7;
    --success: #22c55e;
    --warning: #f59e0b;
    --danger: #ef4444;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
  body { background: var(--bg); color: var(--text); padding: 24px; min-height: 100vh; }
  .container { max-width: 960px; margin: 0 auto; }
  header { margin-bottom: 20px; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 16px; }
  h1 { font-size: 24px; font-weight: 700; display: flex; align-items: center; gap: 10px; }
  .badge { font-size: 12px; padding: 5px 12px; border-radius: 9999px; font-weight: 700; letter-spacing: 0.5px; }
  .badge-armed { background: rgba(34, 197, 94, 0.2); color: var(--success); border: 1px solid var(--success); }
  .badge-idle { background: rgba(148, 163, 184, 0.2); color: var(--muted); border: 1px solid var(--muted); }
  
  .screenshot-box { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 14px; margin-bottom: 24px; }
  .screenshot-header { display: flex; justify-content: space-between; align-items: center; cursor: pointer; }
  .screenshot-img { width: 100%; border-radius: 8px; margin-top: 12px; border: 1px solid #1e293b; display: block; }

  .card-grid { display: grid; grid-template-columns: 1fr; gap: 16px; margin-bottom: 24px; }
  .terminal-card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 20px; transition: all 0.2s; position: relative; }
  .terminal-card.selected { border-color: var(--accent); box-shadow: 0 0 0 2px rgba(56, 189, 248, 0.25); }
  
  .card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
  .card-title { font-size: 18px; font-weight: 700; display: flex; align-items: center; gap: 10px; }
  .pos-pill { font-size: 12px; background: rgba(56, 189, 248, 0.15); color: var(--accent); padding: 4px 10px; border-radius: 6px; font-weight: 700; border: 1px solid rgba(56, 189, 248, 0.3); }
  .tty-pill { font-family: monospace; font-size: 13px; background: #070a12; padding: 4px 9px; border-radius: 6px; color: var(--muted); border: 1px solid #1e293b; }
  
  .meta-row { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 14px; font-size: 14px; }
  .meta-item { display: flex; align-items: center; gap: 6px; background: rgba(0,0,0,0.3); padding: 5px 12px; border-radius: 6px; }
  .meta-label { color: var(--muted); font-size: 13px; }
  .meta-val { font-weight: 600; font-family: monospace; }
  
  .buffer-preview { background: #070a12; border: 1px solid #1e293b; border-radius: 8px; padding: 12px; font-family: monospace; font-size: 12px; line-height: 1.5; color: #cbd5e1; max-height: 140px; overflow-y: auto; margin-bottom: 16px; white-space: pre-wrap; }
  
  .card-actions { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
  .btn { cursor: pointer; padding: 9px 15px; border-radius: 8px; font-weight: 600; font-size: 13px; border: none; transition: 0.15s; display: inline-flex; align-items: center; gap: 6px; user-select: none; }
  .btn-primary { background: var(--accent); color: #070a12; }
  .btn-primary:hover { background: var(--accent-hover); }
  .btn-secondary { background: #1f293d; color: var(--text); border: 1px solid var(--border); }
  .btn-secondary:hover { background: #2d3b55; }
  .btn-success { background: var(--success); color: #070a12; }
  .btn-danger { background: var(--danger); color: #fff; }
  
  .time-input-group { display: flex; align-items: center; gap: 8px; margin-left: auto; background: rgba(0,0,0,0.25); padding: 4px 10px; border-radius: 8px; border: 1px solid var(--border); }
  .time-input-group label { font-size: 13px; color: var(--muted); font-weight: 600; }
  .time-input { background: #070a12; border: 1px solid var(--border); color: var(--accent); padding: 6px 10px; border-radius: 6px; font-family: monospace; font-size: 14px; font-weight: bold; width: 100px; text-align: center; }
  
  .master-panel { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 22px; margin-bottom: 24px; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 4px 20px rgba(0,0,0,0.2); }
  .countdown-box { display: flex; gap: 28px; }
  .cd-item { display: flex; flex-direction: column; }
  .cd-label { font-size: 12px; color: var(--muted); text-transform: uppercase; font-weight: 700; margin-bottom: 4px; letter-spacing: 0.5px; }
  .cd-val { font-family: monospace; font-size: 26px; font-weight: 800; color: var(--accent); }
  
  .log-panel { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 18px; }
  .log-title { font-size: 14px; font-weight: 700; color: var(--muted); margin-bottom: 12px; text-transform: uppercase; letter-spacing: 0.5px; }
  .log-box { background: #070a12; border-radius: 8px; padding: 14px; height: 160px; overflow-y: auto; font-family: monospace; font-size: 12px; color: #94a3b8; border: 1px solid #1e293b; }
  .log-line { margin-bottom: 5px; }
  .log-line:last-child { color: #f8fafc; font-weight: bold; }
</style>
</head>
<body>
<div class="container">
  <header>
    <div>
      <h1>⚡ Codex /goal Auto-Resume (Desktop 3)</h1>
      <p style="color: var(--muted); font-size: 14px; margin-top: 4px;">Presses Enter on your terminals when usage limits reset. Keeps Mac awake via caffeinate.</p>
    </div>
    <div id="statusBadge" class="badge badge-idle">IDLE</div>
  </header>

  <div class="master-panel">
    <div class="countdown-box" id="countdownBox">
      <div class="cd-item">
        <span class="cd-label">Next Trigger Time</span>
        <span class="cd-val" id="nextTriggerTime">--:--:--</span>
      </div>
      <div class="cd-item">
        <span class="cd-label">Countdown</span>
        <span class="cd-val" id="timeRemaining">--:--:--</span>
      </div>
    </div>
    <div style="display:flex; gap:12px;">
      <button class="btn btn-primary" id="btnArm" onclick="armScheduler()" style="padding: 12px 24px; font-size: 15px;">
        🚀 Arm Scheduler Now
      </button>
      <button class="btn btn-danger" id="btnCancel" onclick="cancelScheduler()" style="display:none; padding: 12px 24px; font-size: 15px;">
        🛑 Disarm / Cancel
      </button>
    </div>
  </div>

  <h2 style="font-size: 15px; margin-bottom: 14px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; font-weight: 700;">
    Select Terminals to Target
  </h2>
  <div class="card-grid" id="terminalsList">
    <!-- Populated by JS -->
  </div>

  <div class="log-panel">
    <div class="log-title">Live Activity Log & Verification</div>
    <div class="log-box" id="logBox">
      <!-- Populated by JS -->
    </div>
  </div>
</div>

<script>
let state = {};

async function fetchStatus() {
  try {
    const res = await fetch('/api/status');
    state = await res.json();
    render();
  } catch (e) {
    console.error(e);
  }
}

function render() {
  const badge = document.getElementById('statusBadge');
  const btnArm = document.getElementById('btnArm');
  const btnCancel = document.getElementById('btnCancel');
  
  if (state.is_armed) {
    badge.className = 'badge badge-armed';
    badge.textContent = 'ARMED & SCHEDULED';
    btnArm.style.display = 'none';
    btnCancel.style.display = 'inline-flex';
  } else {
    badge.className = 'badge badge-idle';
    badge.textContent = 'IDLE';
    btnArm.style.display = 'inline-flex';
    btnCancel.style.display = 'none';
  }

  // Countdowns
  if (state.is_armed && state.jobs && state.jobs.length > 0) {
    const activeJobs = state.jobs.filter(j => j.status === 'pending');
    if (activeJobs.length > 0) {
      document.getElementById('nextTriggerTime').textContent = activeJobs[0].target_time_str;
      const remSec = activeJobs[0].remaining_seconds || 0;
      const hrs = Math.floor(remSec / 3600);
      const mins = Math.floor((remSec % 3600) / 60);
      const secs = remSec % 60;
      document.getElementById('timeRemaining').textContent = 
        `${String(hrs).padStart(2,'0')}:${String(mins).padStart(2,'0')}:${String(secs).padStart(2,'0')}`;
    } else {
      document.getElementById('nextTriggerTime').textContent = 'Completed';
      document.getElementById('timeRemaining').textContent = '00:00:00';
    }
  } else {
    document.getElementById('nextTriggerTime').textContent = '--:--:--';
    document.getElementById('timeRemaining').textContent = '--:--:--';
  }

  // Terminals
  const list = document.getElementById('terminalsList');
  if (!list.dataset.rendered || list.children.length !== state.terminals.length) {
    list.innerHTML = '';
    state.terminals.forEach(t => {
      let defTime = "03:40";
      if (t.position.includes("Top-Right")) defTime = "04:13";
      else if (t.position.includes("Bottom-Right")) defTime = "03:40";
      else if (t.position.includes("Bottom-Left")) defTime = "01:25";
      
      const isSelected = t.has_goal_resume || t.position.includes("Top-Right") || t.position.includes("Bottom-Right");

      const card = document.createElement('div');
      card.className = `terminal-card ${isSelected ? 'selected' : ''}`;
      card.id = `card-${t.tty.replace(/[^a-zA-Z0-9]/g, '_')}`;
      card.innerHTML = `
        <div class="card-header">
          <div class="card-title">
            <input type="checkbox" id="check-${card.id}" ${isSelected ? 'checked' : ''} style="width:19px; height:19px; cursor:pointer;" onchange="toggleCardSelection('${card.id}')">
            <span style="cursor:pointer;" onclick="toggleCardSelection('${card.id}')">${t.position}</span>
            <span class="pos-pill">${t.account}</span>
            <span class="tty-pill">${t.tty}</span>
          </div>
          <span style="font-size: 13px; color: ${t.has_goal_resume ? 'var(--success)' : 'var(--muted)'}; font-weight:700;">
            ${t.has_goal_resume ? '✔ /goal resume ready' : 'Idle'}
          </span>
        </div>
        <div class="meta-row">
          <div class="meta-item">
            <span class="meta-label">Window:</span>
            <span class="meta-val">${t.win_name || ('Window ID ' + t.win_id)}</span>
          </div>
          <div class="meta-item">
            <span class="meta-label">Limit Resets:</span>
            <span class="meta-val" style="color:var(--warning);">${t.reset_time_str || 'None'}</span>
          </div>
          <div class="meta-item">
            <span class="meta-label">PID:</span>
            <span class="meta-val">${t.pid}</span>
          </div>
        </div>
        <div class="buffer-preview">${(t.last_lines || []).join('\\n')}</div>
        <div class="card-actions">
          <button class="btn btn-secondary" onclick="highlightTerminal('${t.tty}')">
            🔍 Bring Window to Front
          </button>
          <button class="btn btn-secondary" onclick="triggerImmediate('${t.tty}')">
            ↵ Test Enter Now
          </button>
          <div class="time-input-group">
            <label>Target Time:</label>
            <input type="text" class="time-input" id="time-${card.id}" value="${defTime}" placeholder="03:40">
          </div>
        </div>
      `;
      list.appendChild(card);
    });
    list.dataset.rendered = "true";
  }

  // Logs
  const logBox = document.getElementById('logBox');
  logBox.innerHTML = (state.logs || []).map(l => `<div class="log-line">${l}</div>`).join('');
  logBox.scrollTop = logBox.scrollHeight;
}

function toggleCardSelection(cardId) {
  const card = document.getElementById(cardId);
  const cb = document.getElementById(`check-${cardId}`);
  if (event.target !== cb) {
    cb.checked = !cb.checked;
  }
  if (cb.checked) card.classList.add('selected');
  else card.classList.remove('selected');
}

async function highlightTerminal(tty) {
  await fetch('/api/highlight', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({tty})
  });
}

async function triggerImmediate(tty) {
  if (confirm(`Send Enter key immediately to terminal ${tty}?`)) {
    await fetch('/api/trigger', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({tty})
    });
    fetchStatus();
  }
}

async function armScheduler() {
  const selected = [];
  state.terminals.forEach(t => {
    const cardId = `card-${t.tty.replace(/[^a-zA-Z0-9]/g, '_')}`;
    const cb = document.getElementById(`check-${cardId}`);
    const timeInput = document.getElementById(`time-${cardId}`);
    if (cb && cb.checked) {
      selected.push({
        tty: t.tty,
        label: t.position,
        time_str: timeInput ? timeInput.value : "03:40"
      });
    }
  });

  if (selected.length === 0) {
    alert("Please select at least one terminal!");
    return;
  }

  const res = await fetch('/api/schedule', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({targets: selected})
  });
  const data = await res.json();
  if (data.error) {
    alert("Error: " + data.error);
  } else {
    fetchStatus();
  }
}

async function cancelScheduler() {
  await fetch('/api/cancel', {method: 'POST'});
  fetchStatus();
}

setInterval(fetchStatus, 1000);
fetchStatus();
</script>
</body>
</html>
"""

class RequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/" or url.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))

        elif url.path == "/api/status":
            terminals = get_codex_terminals()
            with state_lock:
                now = datetime.datetime.now()
                jobs_copy = []
                for j in scheduled_jobs:
                    rem = int((j["target_dt"] - now).total_seconds()) if j.get("target_dt") else 0
                    jobs_copy.append({
                        "id": j["id"],
                        "tty": j["tty"],
                        "label": j.get("label", j["tty"]),
                        "target_time_str": j["target_time_str"],
                        "remaining_seconds": max(0, rem),
                        "status": j["status"],
                        "retries": j["retries"]
                    })
                data = {
                    "is_armed": is_armed,
                    "terminals": terminals,
                    "jobs": jobs_copy,
                    "logs": activity_logs
                }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        url = urlparse(self.path)
        content_length = int(self.headers.get("Content-Length", 0))
        post_body = self.rfile.read(content_length)
        body = json.loads(post_body.decode("utf-8")) if post_body else {}

        if url.path == "/api/highlight":
            tty = body.get("tty")
            if tty:
                highlight_window(tty)
                add_activity_log(f"Highlighted window for {tty}.")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        elif url.path == "/api/trigger":
            tty = body.get("tty")
            if tty:
                add_activity_log(f"Manual Enter key triggered for {tty}!")
                send_enter_to_terminal(tty)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        elif url.path == "/api/schedule":
            global is_armed, scheduled_jobs
            targets = body.get("targets", [])
            new_jobs = []
            try:
                for idx, t in enumerate(targets):
                    target_dt = parse_target_datetime(t["time_str"])
                    new_jobs.append({
                        "id": idx + 1,
                        "tty": t["tty"],
                        "label": t.get("label", t["tty"]),
                        "target_time_str": t["time_str"],
                        "target_dt": target_dt,
                        "status": "pending",
                        "retries": 0,
                        "max_retries": 5
                    })
                new_jobs.sort(key=lambda j: j["target_dt"])
                with state_lock:
                    scheduled_jobs = new_jobs
                    is_armed = True
                start_caffeinate()
                for j in new_jobs:
                    add_activity_log(f"Armed job for {j['label']} ({j['tty']}) at {j['target_dt'].strftime('%Y-%m-%d %H:%M:%S')}")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))

        elif url.path == "/api/cancel":
            with state_lock:
                is_armed = False
                for j in scheduled_jobs:
                    if j["status"] == "pending":
                        j["status"] = "cancelled"
                stop_caffeinate()
                add_activity_log("Scheduler disarmed by user.")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        return

def arm_default_schedule():
    """Directly arm the default schedule for Top-Right at 04:13 and Bottom-Right at 03:40."""
    global is_armed, scheduled_jobs
    terminals = get_codex_terminals(force_refresh=True)
    tr = next((t for t in terminals if "Top-Right" in t["position"]), None)
    br = next((t for t in terminals if "Bottom-Right" in t["position"]), None)

    if not tr or not br:
        log("Error: Could not locate both Top-Right and Bottom-Right terminals on Desktop 3.")
        return False

    now = datetime.datetime.now()
    dt_br = parse_target_datetime("03:40")
    dt_tr = parse_target_datetime("04:13")

    with state_lock:
        scheduled_jobs = [
            {
                "id": 1,
                "tty": br["tty"],
                "label": "Bottom-Right Window (Desktop 3)",
                "target_time_str": "03:40",
                "target_dt": dt_br,
                "status": "pending",
                "retries": 0,
                "max_retries": 5
            },
            {
                "id": 2,
                "tty": tr["tty"],
                "label": "Top-Right Window (Desktop 3)",
                "target_time_str": "04:13",
                "target_dt": dt_tr,
                "status": "pending",
                "retries": 0,
                "max_retries": 5
            }
        ]
        scheduled_jobs.sort(key=lambda j: j["target_dt"])
        is_armed = True
    start_caffeinate()
    add_activity_log(f"Auto-armed Bottom-Right ({br['tty']}) for {dt_br.strftime('%Y-%m-%d %H:%M:%S')}")
    add_activity_log(f"Auto-armed Top-Right ({tr['tty']}) for {dt_tr.strftime('%Y-%m-%d %H:%M:%S')}")
    return True

def main():
    threading.Thread(target=scheduler_thread_loop, daemon=True).start()

    # If --auto or --daemon is passed
    if "--auto" in sys.argv or "--daemon" in sys.argv:
        arm_default_schedule()

    # Start HTTP server
    port = DEFAULT_PORT
    server = None
    for p in range(port, port + 10):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", p), RequestHandler)
            port = p
            break
        except OSError:
            continue

    if not server:
        print("Error: Could not bind to any port.")
        sys.exit(1)

    url = f"http://localhost:{port}"
    add_activity_log(f"Interactive Web Selector running at {url}")
    print(f"\n=======================================================")
    print(f"  Codex Auto-Resume Web Selector is live at:")
    print(f"  --> {url}")
    print(f"=======================================================\n")

    # Automatically open browser unless --no-open
    if "--no-open" not in sys.argv:
        subprocess.run(["open", url])

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
        stop_caffeinate()

if __name__ == "__main__":
    main()
