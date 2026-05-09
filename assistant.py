#!/usr/bin/env python3
import os
import sys
import re
import requests
import subprocess
import tempfile
import threading
import signal
import time
import json
import glob
import shutil

try:
    import speech_recognition as sr
    SPEECH_REC_AVAILABLE = True
except ImportError:
    SPEECH_REC_AVAILABLE = False

try:
    from openai import OpenAI as WhisperClient
    WHISPER_AVAILABLE = True
except ImportError:
    WHISPER_AVAILABLE = False

try:
    from gtts import gTTS
    GTTS_AVAILABLE = True
except ImportError:
    GTTS_AVAILABLE = False

try:
    import pygame
    PYGAME_AVAILABLE = True
except ImportError:
    PYGAME_AVAILABLE = False

API_BASE = "https://opencode.ai/zen/v1"
DEFAULT_MODEL = "big-pickle"
WAKE_WORD = "assistant"

FULL_SYSTEM_ACCESS = False

SAFE_PATHS = [
    os.path.expanduser("~"),
    os.path.expanduser("~/Desktop"),
    os.path.expanduser("~/Documents"),
    os.path.expanduser("~/Downloads"),
    os.path.expanduser("~/assistant"),
]

SYSTEM_PROMPT_SAFE = """You are a helpful AI assistant with system access.

Available tools you can use by responding in a special JSON format:

1. LAUNCH_APP - Open an application
   Format: {"tool": "launch_app", "app": "<app_name>"}
   Examples: firefox, code, chrome, nautilus, terminal, discord, steam
   For flatpak apps: use the flatpak name or partial name

2. WEB_SEARCH - Search the web in browser
   Format: {"tool": "web_search", "query": "<search_terms>"}
   Opens default browser with the search

3. READ_FILE - Read text from a file
   Format: {"tool": "read_file", "path": "<absolute_path>"}
   Path must be under user's home directory

4. WRITE_FILE - Create or write to a file
   Format: {"tool": "write_file", "path": "<absolute_path>", "content": "<text>"}
   Overwrites existing files, so be careful

5. LIST_DIR - List files in a directory
   Format: {"tool": "list_dir", "path": "<absolute_path>"}

6. DELETE_FILE - Delete a file (requires confirmation)
   Format: {"tool": "delete_file", "path": "<absolute_path>"}

7. RUN_CMD - Run a shell command (safe READ-ONLY commands only)
   Format: {"tool": "run_cmd", "cmd": "<command>"}
   
   MANY safe commands available including:
   - File listing: ls, find, tree, du, df
   - File reading: cat, head, tail, less, more, wc, nl, od, strings
   - File info: file, stat, du, df, lsblk
   - Text processing: grep, awk, sed, sort, uniq, cut, paste, tr, diff, wc
   - Process info: ps, top, htop, pstree, pgrep, w, who
   - System info: uname, hostname, uptime, free, vmstat, lscpu, lspci, lsusb
   - Network info: ss, netstat, ip, ping
   - Git info: git status, git log, git diff, git branch
   - And many more read-only commands

   DANGEROUS commands are BLOCKED (redirects, pipes, sudo, rm, chmod, etc.)

When you want to use a tool, respond ONLY with the JSON.
When the tool result comes back, explain it to the user in natural language.

Be careful with:
- File deletions
- Overwriting files
- Paths outside the user's home directory
"""

SYSTEM_PROMPT_FULL = """You are a helpful AI assistant with FULL SYSTEM ACCESS TO THE USER'S COMPUTER.

The user has enabled FULL SYSTEM MODE (-s flag). You have unrestricted access to:
- Run ANY shell command
- Read/write/delete ANY files
- Install packages
- Modify system configuration
- Full system administration

Available tools:

1. LAUNCH_APP - Open an application
   Format: {"tool": "launch_app", "app": "<app_name>"}

2. WEB_SEARCH - Search the web in browser
   Format: {"tool": "web_search", "query": "<search_terms>"}

3. READ_FILE - Read text from a file
   Format: {"tool": "read_file", "path": "<absolute_path>"}
   Can read ANY path

4. WRITE_FILE - Create or write to a file
   Format: {"tool": "write_file", "path": "<absolute_path>", "content": "<text>"}

5. LIST_DIR - List files in a directory
   Format: {"tool": "list_dir", "path": "<absolute_path>"}

6. DELETE_FILE - Delete a file or directory
   Format: {"tool": "delete_file", "path": "<absolute_path>"}

7. RUN_CMD - Run ANY shell command
   Format: {"tool": "run_cmd", "cmd": "<command>"}
   
   NO RESTRICTIONS. You can run:
   - Package management: apt, pacman, dnf, yum, pip, npm, etc.
   - System commands: rm, chmod, chown, cp, mv, etc.
   - Networking: curl, wget, ssh, etc.
   - Script execution, and more.

When you want to use a tool, respond ONLY with the JSON.
When the tool result comes back, explain it to the user in natural language.

IMPORTANT: The user trusts you with full system access.
Be careful and explain what you're doing when doing dangerous operations.
"""

def get_system_prompt():
    global FULL_SYSTEM_ACCESS
    if FULL_SYSTEM_ACCESS:
        return SYSTEM_PROMPT_FULL
    return SYSTEM_PROMPT_SAFE

STOP_PHRASES = ['shut up', 'stop talking', 'be quiet', 'stop it', 'please stop', 'enough', 'stop now']

PHRASE_CORRECTIONS_ORDERED = [
    (r'\bcreate me a web hit( me up)?\b', 'create me a website'),
    (r'\bcreate me a web hit( me)?\b', 'create me a website'),
    (r'\bcreate a web site\b', 'create a website'),
    (r'\bcreate me a web site\b', 'create me a website'),
    (r'\bweb site\b', 'website'),
    (r'\bweb hit\b', 'website'),
    (r'\bwebsite up\b', 'website'),
    (r'\b up\b', ''),
    (r'\bassistance\b', 'assistant'),
    (r'\bthe assistant\b', 'assistant'),
    (r'\bhey assistant\b', 'assistant'),
    (r'\bassistant can you\b', 'assistant'),
    (r'\bassistant could you\b', 'assistant'),
    (r'\bassistant web\b', 'assistant'),
    (r'\blist file\b', 'list files'),
    (r'\blist foler\b', 'list folder'),
    (r'\blist folers\b', 'list folders'),
    (r'\blist derectory\b', 'list directory'),
    (r'\blist derectories\b', 'list directories'),
    (r"\bwhat's\b", 'what is'),
    (r'\bweb page\b', 'webpage'),
    (r'\bhtm\b', 'html'),
    (r'\bhteml\b', 'html'),
    (r'\bhtlm\b', 'html'),
    (r'\bopen chorme\b', 'open chrome'),
    (r'\bopen fire fox\b', 'open firefox'),
    (r'\bopen fireFox\b', 'open firefox'),
]

try:
    from difflib import get_close_matches
    DIFFLIB_AVAILABLE = True
except:
    DIFFLIB_AVAILABLE = False

def fuzzy_match_phrase(text: str) -> str:
    text_lower = text.lower()
    result = text_lower
    
    for pattern, replacement in PHRASE_CORRECTIONS_ORDERED:
        new_result = re.sub(pattern, replacement, result)
        if new_result != result:
            print(f"[Corrected phrase: '{result}' -> '{new_result}']")
            result = new_result
    
    result = re.sub(r'\s+', ' ', result).strip()
    result = re.sub(r'\s+([.,!?])', r'\1', result)
    
    return result

def get_close_command(text: str) -> tuple:
    if not DIFFLIB_AVAILABLE:
        return text, 1.0
    
    common_phrases = [
        'list files', 'list my files', 'list directory', 'list folder',
        'list my downloads', 'list my documents', 'list my desktop',
        'open', 'open chrome', 'open firefox', 'open terminal',
        'launch', 'launch app', 'start', 'run',
        'read file', 'write file', 'create file',
        'delete file', 'remove file',
        'search', 'search the web', 'search for',
        'create website', 'make website', 'create html website',
        'create a website', 'make me a website',
        'what is', 'what time', 'how to',
    ]
    
    text_lower = text.lower()
    
    for phrase in common_phrases:
        if phrase in text_lower:
            return text_lower, 0.9
    
    matches = get_close_matches(text_lower, common_phrases, n=3, cutoff=0.6)
    
    if matches:
        best_match = matches[0]
        return best_match, 0.75
    
    return text_lower, 0.0

EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    "]+",
    flags=re.UNICODE
)

ESPPEAK_INSTALLED = None
MPLAYER_INSTALLED = None
_tts_stop_flag = threading.Event()
_tts_process = None
_tts_lock = threading.Lock()
_voice_mode_active = False

def check_installed(cmd):
    try:
        subprocess.run(['which', cmd], capture_output=True, check=True)
        return True
    except:
        return False

def init_checks():
    global ESPPEAK_INSTALLED, MPLAYER_INSTALLED
    ESPPEAK_INSTALLED = check_installed('espeak')
    MPLAYER_INSTALLED = check_installed('mplayer')

def is_safe_path(path: str) -> tuple:
    global FULL_SYSTEM_ACCESS
    if FULL_SYSTEM_ACCESS:
        return True, os.path.abspath(os.path.expanduser(path))
    
    path = os.path.abspath(os.path.expanduser(path))
    home = os.path.expanduser("~")
    if not path.startswith(home):
        return False, "Path outside home directory"
    if '/.ssh' in path or '/.gnupg' in path or '/.password' in path.lower():
        return False, "Path contains sensitive data"
    return True, path

def stop_tts():
    global _tts_process, _tts_stop_flag
    _tts_stop_flag.set()
    
    with _tts_lock:
        if _tts_process:
            try:
                if PYGAME_AVAILABLE and pygame.mixer.get_init():
                    pygame.mixer.music.stop()
                else:
                    _tts_process.terminate()
                    _tts_process.wait(timeout=1)
            except:
                try:
                    _tts_process.kill()
                except:
                    pass
            _tts_process = None
    
    print("\n[TTS stopped]")

def _signal_handler(sig, frame):
    if _voice_mode_active:
        stop_tts()
    else:
        print("\nExiting...")
        sys.exit(0)

def clean_text_for_tts(text: str) -> str:
    text = EMOJI_PATTERN.sub('', text)
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'\*(.*?)\*', r'\1', text)
    text = re.sub(r'`{1,3}([^`]*)`{1,3}', r'\1', text)
    text = re.sub(r'~~(.*?)~~', r'\1', text)
    text = re.sub(r'^\#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = text.replace('*', ' ')
    text = text.replace('_', ' ')
    text = text.replace('~', ' ')
    text = text.replace('`', ' ')
    text = text.replace('#', ' ')
    text = text.replace('|', ', ')
    text = text.replace('---', ' ')
    text = text.replace('===', ' ')
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = text.strip()
    return text

def get_model() -> str:
    return os.environ.get("BIG_PICKLE_MODEL", DEFAULT_MODEL)

def get_available_apps() -> dict:
    apps = {}
    
    desktop_dirs = [
        '/usr/share/applications',
        '/usr/local/share/applications',
        os.path.expanduser('~/.local/share/applications')
    ]
    
    for d in desktop_dirs:
        if os.path.isdir(d):
            for f in glob.glob(os.path.join(d, '*.desktop')):
                try:
                    with open(f, 'r') as fp:
                        content = fp.read()
                        name_match = re.search(r'^Name=(.+)$', content, re.MULTILINE)
                        exec_match = re.search(r'^Exec=(.+)$', content, re.MULTILINE)
                        if name_match and exec_match:
                            name = name_match.group(1).lower()
                            exec_cmd = exec_match.group(1).replace('%U', '').replace('%F', '').strip()
                            if ' ' in name:
                                name_key = name.split()[0].lower()
                                if name_key not in apps:
                                    apps[name_key] = exec_cmd
                            apps[name] = exec_cmd
                            simple_name = os.path.basename(f).replace('.desktop', '').lower()
                            if simple_name not in apps:
                                apps[simple_name] = exec_cmd
                except:
                    pass
    
    try:
        result = subprocess.run(['flatpak', 'list', '--app', '--columns=application,description'], 
                               capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            for line in result.stdout.strip().split('\n'):
                parts = line.split('\t') if '\t' in line else line.split()
                if len(parts) >= 1:
                    full_id = parts[0].strip()
                    name_parts = full_id.split('.')
                    if len(name_parts) >= 2:
                        app_name = name_parts[-2].lower() + '-' + name_parts[-1].lower() if len(name_parts) > 2 else name_parts[-1].lower()
                    else:
                        app_name = full_id.lower().replace('.', '-')
                    
                    simple_name = name_parts[-1].lower() if len(name_parts) > 1 else full_id.lower()
                    
                    apps[simple_name] = f"flatpak run {full_id}"
                    apps[full_id.lower()] = f"flatpak run {full_id}"
                    apps[app_name] = f"flatpak run {full_id}"
    except:
        pass
    
    common_bins = ['firefox', 'chromium', 'chrome', 'google-chrome', 'code', 'codium',
                   'nautilus', 'thunar', 'dolphin', 'terminal', 'gnome-terminal',
                   'konsole', 'alacritty', 'kitty', 'discord', 'slack', 'spotify',
                   'steam', 'obs', 'krita', 'gimp', 'inkscape', 'blender',
                   'libreoffice', 'writer', 'calc', 'impress']
    
    for cmd in common_bins:
        if check_installed(cmd) and cmd not in apps:
            apps[cmd] = cmd
    
    return apps

APP_CACHE = None

def is_webapp_cmd(cmd: str) -> bool:
    webapp_patterns = ['omarchy-launch-webapp', 'xdg-open http', 'gtk-launch http']
    for pattern in webapp_patterns:
        if pattern in cmd:
            return True
    return False

def find_app(app_name: str) -> str:
    global APP_CACHE
    if APP_CACHE is None:
        APP_CACHE = get_available_apps()
    
    app_name_lower = app_name.lower().strip()
    
    if check_installed(app_name_lower):
        return app_name_lower
    
    if check_installed(app_name_lower.replace('-', '')):
        return app_name_lower.replace('-', '')
    
    if app_name_lower in APP_CACHE:
        cmd = APP_CACHE[app_name_lower]
        if not is_webapp_cmd(cmd):
            return cmd
    
    found_webapp = None
    for name, cmd in APP_CACHE.items():
        if app_name_lower in name or name in app_name_lower:
            if not is_webapp_cmd(cmd):
                return cmd
            elif found_webapp is None:
                found_webapp = cmd
    
    if found_webapp:
        return found_webapp
    
    return None

def launch_app(app_name: str) -> str:
    app_cmd = find_app(app_name)
    
    if not app_cmd:
        return f"ERROR: Could not find app '{app_name}'. Available apps include: firefox, chrome, code, terminal, nautilus, discord, steam, and many flatpak apps."
    
    try:
        cmd_parts = app_cmd.split()
        subprocess.Popen(cmd_parts, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        start_new_session=True)
        return f"SUCCESS: Launched '{app_name}'"
    except Exception as e:
        return f"ERROR: Failed to launch '{app_name}': {e}"

def web_search(query: str) -> str:
    search_url = f"https://www.google.com/search?q={requests.utils.quote(query)}"
    
    try:
        subprocess.Popen(['xdg-open', search_url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return f"SUCCESS: Opened web search for '{query}' in default browser"
    except Exception as e:
        return f"ERROR: Failed to open browser: {e}"

def read_file(path: str) -> str:
    safe, real_path = is_safe_path(path)
    if not safe:
        return f"ERROR: {real_path}"
    
    if not os.path.exists(real_path):
        return f"ERROR: File does not exist: {real_path}"
    
    if os.path.isdir(real_path):
        return f"ERROR: Path is a directory, not a file: {real_path}"
    
    try:
        with open(real_path, 'r') as f:
            content = f.read()
        
        if len(content) > 5000:
            content = content[:5000] + "\n... [truncated - file too long]"
        
        return f"SUCCESS: Content of {real_path}:\n\n{content}"
    except Exception as e:
        return f"ERROR: Could not read file: {e}"

def write_file(path: str, content: str) -> str:
    safe, real_path = is_safe_path(path)
    if not safe:
        return f"ERROR: {real_path}"
    
    try:
        dir_path = os.path.dirname(real_path)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)
        
        with open(real_path, 'w') as f:
            f.write(content)
        
        return f"SUCCESS: Wrote {len(content)} characters to {real_path}"
    except Exception as e:
        return f"ERROR: Could not write file: {e}"

def list_dir(path: str) -> str:
    safe, real_path = is_safe_path(path)
    if not safe:
        return f"ERROR: {real_path}"
    
    if not os.path.exists(real_path):
        return f"ERROR: Directory does not exist: {real_path}"
    
    if not os.path.isdir(real_path):
        return f"ERROR: Path is a file, not a directory: {real_path}"
    
    try:
        items = sorted(os.listdir(real_path))
        result = f"Contents of {real_path}:\n"
        
        dirs = []
        files = []
        for item in items:
            if item.startswith('.'):
                continue
            full_path = os.path.join(real_path, item)
            if os.path.isdir(full_path):
                dirs.append(f"📁 {item}/")
            else:
                size = os.path.getsize(full_path)
                files.append(f"📄 {item} ({size} bytes)")
        
        for d in dirs:
            result += f"  {d}\n"
        for f in files:
            result += f"  {f}\n"
        
        if not dirs and not files:
            result += "  (empty directory)\n"
        
        return result
    except Exception as e:
        return f"ERROR: Could not list directory: {e}"

def delete_file(path: str) -> str:
    safe, real_path = is_safe_path(path)
    if not safe:
        return f"ERROR: {real_path}"
    
    if not os.path.exists(real_path):
        return f"ERROR: File does not exist: {real_path}"
    
    try:
        if os.path.isdir(real_path):
            shutil.rmtree(real_path)
            return f"SUCCESS: Deleted directory: {real_path}"
        else:
            os.remove(real_path)
            return f"SUCCESS: Deleted file: {real_path}"
    except Exception as e:
        return f"ERROR: Could not delete: {e}"

SAFE_COMMANDS = {
    'ls', 'pwd', 'whoami', 'id', 'date', 'uptime', 'echo', 'printf',
    'cat', 'head', 'tail', 'less', 'more', 'wc', 'nl', 'od', 'strings',
    'file', 'stat', 'du', 'df', 'find', 'tree', 'lsblk', 'blkid',
    'ps', 'top', 'htop', 'pstree', 'pgrep', 'pidof', 'w', 'who', 'users',
    'uname', 'hostname', 'lscpu', 'lspci', 'lsusb', 'lshw', 'dmidecode',
    'free', 'vmstat', 'iostat', 'mpstat', 'netstat', 'ss', 'ip',
    'grep', 'egrep', 'fgrep', 'rg', 'awk', 'gawk', 'sed',
    'sort', 'uniq', 'cut', 'paste', 'join', 'tr', 'expand', 'unexpand',
    'fold', 'fmt', 'pr', 'head', 'tail', 'split', 'csplit',
    'sort', 'uniq', 'comm', 'diff', 'cmp', 'patch',
    'bc', 'dc', 'expr', 'test', 'true', 'false', 'yes',
    'which', 'whereis', 'whatis', 'man', 'apropos', 'help',
    'lsattr', 'chattr', 'getfacl', 'setfacl', 'getfattr', 'setfattr',
    'git', 'hg', 'svn', 'status', 'log', 'diff', 'branch', 'tag',
    'curl', 'wget', 'http', 'https',
    'jq', 'yq', 'xmlstarlet', 'xmllint',
    'base64', 'md5sum', 'sha1sum', 'sha256sum', 'sha512sum',
    'zipinfo', 'unzip', 'tar', 'gzip', 'gunzip', 'bzip2', 'bunzip2', 'xz', 'unxz',
    'zcat', 'bzcat', 'xzcat', 'less', 'more', 'most',
    'cal', 'ncal', 'date', 'timedatectl', 'hwclock',
    'locale', 'localectl', 'loadkeys', 'showkey', 'dumpkeys',
    'env', 'printenv', 'set', 'declare', 'typeset',
    'history', 'fc', 'alias', 'unalias', 'type', 'command',
    'tty', 'stty', 'setsid', 'nohup',
    'nice', 'ionice', 'renice', 'chrt', 'taskset',
    'lsb_release', 'os-release', 'hostnamectl', 'systemctl',
    'journalctl', 'dmesg', 'udevadm', 'lsusb', 'lspci',
}

DANGEROUS_PATTERNS = [
    'rm -rf', 'rm -fr', 'rm -r', 'rm -f',
    '>', '>>', ';', '&&', '||', '`', '$(',
    'chmod', 'chown', 'chgrp', 'mkfs', 'fdisk', 'parted',
    'dd ', 'dd if', 'kill', 'killall', 'pkill',
    'sudo', 'doas', 'pkexec', 'gksudo', 'kdesu',
    'shutdown', 'reboot', 'halt', 'poweroff', 'init',
    'systemctl start', 'systemctl stop', 'systemctl restart', 'systemctl enable', 'systemctl disable',
    'apt', 'apt-get', 'dnf', 'yum', 'pacman', 'zypper', 'pkg', 'brew',
    'pip install', 'npm install', 'cargo install', 'gem install',
    'curl |', 'curl | bash', 'wget -O',
    ':(){ :|:& };:', 'fork bomb',
    '| bash', '| sh', '| zsh', '| nc', '| netcat', '| curl', '| wget',
]

DANGEROUS_COMMANDS = {'rm', 'cp', 'mv', 'ln', 'chmod', 'chown', 'chgrp', 'mkdir', 'rmdir',
                      'dd', 'mkfs', 'fdisk', 'parted', 'gparted',
                      'kill', 'killall', 'pkill', 'xkill',
                      'shutdown', 'reboot', 'halt', 'poweroff',
                      'iptables', 'ip6tables', 'ufw', 'firewall-cmd',
                      'useradd', 'userdel', 'usermod', 'passwd',
                      'groupadd', 'groupdel', 'groupmod',
                      'visudo', 'sudoers',
                      '>', '>>', '|', '<', '<<',
                      'curl', 'wget'}

def is_safe_pipe(cmd: str) -> bool:
    if '|' not in cmd:
        return True
    
    parts = re.split(r'\s*\|\s*', cmd)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        cmd_base = part.split()[0] if part.split() else ''
        if cmd_base in DANGEROUS_COMMANDS:
            return False
    return True

def contains_dangerous_pattern(cmd: str) -> bool:
    global FULL_SYSTEM_ACCESS
    if FULL_SYSTEM_ACCESS:
        return False
    
    cmd_lower = cmd.lower()
    for pattern in DANGEROUS_PATTERNS:
        if pattern in cmd_lower:
            return True
    
    if '|' in cmd_lower and not is_safe_pipe(cmd):
        return True
    
    if re.search(r'\|\s*bash', cmd_lower) or re.search(r'\|\s*sh', cmd_lower):
        return True
    if re.search(r'\|\s*nc\b', cmd_lower) or re.search(r'\|\s*netcat\b', cmd_lower):
        return True
    if re.search(r'\|\s*curl\b', cmd_lower) or re.search(r'\|\s*wget\b', cmd_lower):
        return True
    
    return False

def run_cmd(cmd: str) -> str:
    global FULL_SYSTEM_ACCESS
    
    cmd_base = cmd.split()[0] if cmd.strip() else ''
    cmd_lower = cmd.lower()
    
    if not FULL_SYSTEM_ACCESS:
        if contains_dangerous_pattern(cmd):
            return f"ERROR: Command contains potentially dangerous patterns. Use -s flag for full system access."
        
        if cmd_base and cmd_base not in SAFE_COMMANDS:
            return f"ERROR: Command '{cmd_base}' not in safe list. Use -s flag for full system access. Available safe commands include: ls, cat, grep, find, ps, top, df, du, file, stat, wc, sort, uniq, diff, etc."
    
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        output = result.stdout
        if result.stderr:
            output = output + "\n[stderr]: " + result.stderr if output else "[stderr]: " + result.stderr
        
        if len(output) > 12000:
            output = output[:12000] + f"\n... [truncated - showing first 12000 chars of {len(output)} total]"
        
        return f"Command: {cmd}\nExit code: {result.returncode}\n\nOutput:\n{output}"
    except subprocess.TimeoutExpired:
        return f"ERROR: Command timed out after 30 seconds"
    except Exception as e:
        return f"ERROR: Command failed: {e}"

def extract_json_objects(text: str) -> list:
    results = []
    stack = []
    start_idx = None
    
    for i, char in enumerate(text):
        if char == '{':
            if not stack:
                start_idx = i
            stack.append(char)
        elif char == '}':
            if stack:
                stack.pop()
                if not stack and start_idx is not None:
                    json_str = text[start_idx:i+1]
                    try:
                        obj = json.loads(json_str)
                        if 'tool' in obj:
                            results.append(obj)
                    except:
                        pass
                    start_idx = None
    
    return results

def extract_tool_call(text: str) -> tuple:
    json_objects = extract_json_objects(text)
    
    if json_objects:
        tool_obj = json_objects[0]
        
        first_json_start = text.find('{')
        last_json_end = text.rfind('}') + 1
        
        remaining = text[:first_json_start].strip()
        if last_json_end < len(text):
            remaining = remaining + ' ' + text[last_json_end:].strip()
        remaining = remaining.strip()
        
        return tool_obj, remaining
    
    return None, text

def is_tool_call(text: str) -> dict:
    tool, _ = extract_tool_call(text)
    return tool

def execute_tool(tool_data: dict) -> str:
    tool = tool_data.get('tool', '')
    
    if tool == 'launch_app':
        app = tool_data.get('app', '')
        if not app:
            return "ERROR: No app name specified"
        return launch_app(app)
    
    elif tool == 'web_search':
        query = tool_data.get('query', '')
        if not query:
            return "ERROR: No search query specified"
        return web_search(query)
    
    elif tool == 'read_file':
        path = tool_data.get('path', '')
        if not path:
            return "ERROR: No path specified"
        return read_file(path)
    
    elif tool == 'write_file':
        path = tool_data.get('path', '')
        content = tool_data.get('content', '')
        if not path:
            return "ERROR: No path specified"
        return write_file(path, content)
    
    elif tool == 'list_dir':
        path = tool_data.get('path', os.path.expanduser('~'))
        return list_dir(path)
    
    elif tool == 'delete_file':
        path = tool_data.get('path', '')
        if not path:
            return "ERROR: No path specified"
        return delete_file(path)
    
    elif tool == 'run_cmd':
        cmd = tool_data.get('cmd', '')
        if not cmd:
            return "ERROR: No command specified"
        return run_cmd(cmd)
    
    return f"ERROR: Unknown tool: {tool}"

def ask_with_tools(question: str, model: str = None, messages_context: list = None, use_tts: bool = False, tts_gtts: bool = False) -> tuple:
    if model is None:
        model = get_model()
    
    headers = {"Content-Type": "application/json"}
    
    api_key = os.environ.get("OPENCODE_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    
    if messages_context is None:
        messages_context = []
    
    if not messages_context:
        messages_context.append({"role": "system", "content": get_system_prompt()})
    
    messages_context.append({"role": "user", "content": question})
    
    for attempt in range(3):
        data = {
            "model": model,
            "messages": messages_context,
            "temperature": 0.7
        }
        
        response = requests.post(
            f"{API_BASE}/chat/completions",
            headers=headers,
            json=data
        )
        
        if response.status_code != 200:
            raise Exception(f"API error {response.status_code}: {response.text}")
        
        result = response.json()
        reply = result["choices"][0]["message"]["content"]
        
        tool_call, text_remainder = extract_tool_call(reply)
        if tool_call:
            print(f"\n[Tool Call: {tool_call.get('tool')}]")
            
            if tool_call.get('tool') == 'delete_file':
                path = tool_call.get('path', '')
                safe, real_path = is_safe_path(path)
                if safe:
                    confirm = input(f"[Confirm delete: {real_path}] (y/N): ").strip().lower()
                    if confirm != 'y':
                        print("[Delete cancelled by user]")
                        messages_context.append({"role": "assistant", "content": reply})
                        messages_context.append({"role": "user", "content": "User cancelled the delete operation."})
                        continue
            
            tool_result = execute_tool(tool_call)
            print(f"[Tool Result: {tool_result[:100]}...]" if len(tool_result) > 100 else f"[Tool Result: {tool_result}]")
            
            messages_context.append({"role": "assistant", "content": reply})
            messages_context.append({"role": "user", "content": f"Tool result: {tool_result}\n\nNow please explain this result to the user in natural language. If it was an error, help them troubleshoot. If it was a success, confirm what happened."})
            continue
        
        messages_context.append({"role": "assistant", "content": reply})
        return reply, messages_context
    
    messages_context.append({"role": "assistant", "content": "I encountered an issue. Let me try to help directly."})
    return "I tried to use a tool but had some trouble. How else can I help you?", messages_context

def ask(question: str, model: str = None, messages_context: list = None) -> tuple:
    if model is None:
        model = get_model()
    
    headers = {"Content-Type": "application/json"}
    
    api_key = os.environ.get("OPENCODE_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    
    if messages_context is None:
        messages_context = []
    
    messages_context.append({"role": "user", "content": question})
    
    data = {
        "model": model,
        "messages": messages_context,
        "temperature": 0.7
    }
    
    response = requests.post(
        f"{API_BASE}/chat/completions",
        headers=headers,
        json=data
    )
    
    if response.status_code != 200:
        raise Exception(f"API error {response.status_code}: {response.text}")
    
    result = response.json()
    reply = result["choices"][0]["message"]["content"]
    messages_context.append({"role": "assistant", "content": reply})
    
    return reply, messages_context

def _speak_blocking(clean_text: str, use_gtts: bool):
    global _tts_process
    
    if not clean_text.strip():
        return
    
    with _tts_lock:
        if _tts_stop_flag.is_set():
            return
        
        if use_gtts and GTTS_AVAILABLE:
            try:
                with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
                    tts = gTTS(text=clean_text, lang='en')
                    tts.save(f.name)
                    mp3_path = f.name
                
                if _tts_stop_flag.is_set():
                    os.unlink(mp3_path)
                    return
                
                if PYGAME_AVAILABLE:
                    pygame.mixer.init()
                    pygame.mixer.music.load(mp3_path)
                    pygame.mixer.music.play()
                    while pygame.mixer.music.get_busy() and not _tts_stop_flag.is_set():
                        pygame.time.Clock().tick(10)
                    if pygame.mixer.music.get_busy():
                        pygame.mixer.music.stop()
                elif MPLAYER_INSTALLED:
                    _tts_process = subprocess.Popen(['mplayer', '-really-quiet', mp3_path])
                    _tts_process.wait()
                elif check_installed('ffplay'):
                    _tts_process = subprocess.Popen(['ffplay', '-nodisp', '-autoexit', mp3_path])
                    _tts_process.wait()
                
                os.unlink(mp3_path)
                return
            except:
                pass
        
        if ESPPEAK_INSTALLED:
            _tts_process = subprocess.Popen(['espeak', '-v', 'en-us', clean_text])
            _tts_process.wait()
        elif check_installed('say'):
            _tts_process = subprocess.Popen(['say', clean_text])
            _tts_process.wait()
        else:
            print("[Warning: No TTS engine found. Install espeak or gTTS]")

def speak(text: str, use_gtts: bool = False, stop_check_callback=None):
    global _tts_stop_flag, _tts_process
    
    clean_text = clean_text_for_tts(text)
    print(f"[TTS]: {clean_text}")
    
    if not clean_text.strip():
        return
    
    _tts_stop_flag.clear()
    
    tts_thread = threading.Thread(target=_speak_blocking, args=(clean_text, use_gtts), daemon=True)
    tts_thread.start()
    
    while tts_thread.is_alive():
        if _tts_stop_flag.is_set():
            break
        if stop_check_callback:
            if stop_check_callback():
                stop_tts()
                break
        time.sleep(0.1)
    
    tts_thread.join(timeout=2)
    _tts_process = None

def check_wake_word(text: str) -> tuple:
    text_lower = text.lower()
    
    pattern = r'\b' + re.escape(WAKE_WORD) + r'\b'
    match = re.search(pattern, text_lower)
    
    if match:
        after_wake = text[match.end():].strip()
        if after_wake and after_wake[0] in [',', '.', '!', '?', ':']:
            after_wake = after_wake[1:].strip()
        return True, after_wake
    return False, ""

def check_stop_phrase(text: str) -> bool:
    text_lower = text.lower()
    for phrase in STOP_PHRASES:
        if phrase in text_lower:
            return True
    return False

def listen_once(recognizer, source, use_whisper: bool = False, phrase_time_limit: int = 10, 
                stop_callback=None, use_fuzzy: bool = True) -> str:
    try:
        print("[Listening...]")
        audio = recognizer.listen(source, timeout=5, phrase_time_limit=phrase_time_limit)
        print("[Processing...]")
        
        if stop_callback and stop_callback():
            return ""
        
        text = ""
        confidence = 0.0
        
        if use_whisper and WHISPER_AVAILABLE:
            try:
                with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
                    f.write(audio.get_wav_data())
                    wav_path = f.name
                
                api_key = os.environ.get("OPENCODE_API_KEY")
                if api_key:
                    client = WhisperClient(api_key=api_key)
                else:
                    client = WhisperClient(base_url="https://opencode.ai/zen/v1", api_key="dummy")
                
                with open(wav_path, 'rb') as audio_file:
                    transcription = client.audio.transcriptions.create(
                        model="whisper-1",
                        file=audio_file
                    )
                
                os.unlink(wav_path)
                text = transcription.text
                confidence = 0.95
            except:
                pass
        
        if not text:
            try:
                text = recognizer.recognize_google(audio)
                confidence = 0.7
            except sr.UnknownValueError:
                try:
                    text = recognizer.recognize_sphinx(audio)
                    confidence = 0.5
                except:
                    return ""
            except sr.RequestError:
                try:
                    text = recognizer.recognize_sphinx(audio)
                    confidence = 0.5
                except:
                    return ""
        
        if text and use_fuzzy:
            original_text = text
            text = fuzzy_match_phrase(text)
            
            if original_text.lower() != text.lower():
                print(f"[Corrected: '{original_text}' -> '{text}']")
        
        return text
                
    except sr.WaitTimeoutError:
        return ""
    except:
        return ""

def voice_mode(use_gtts: bool = False, use_whisper: bool = False, energy_threshold: int = 300):
    global _voice_mode_active
    _voice_mode_active = True
    init_checks()
    
    signal.signal(signal.SIGINT, _signal_handler)
    
    if not SPEECH_REC_AVAILABLE:
        print("Error: speech_recognition library not installed.")
        print("Run: pip install SpeechRecognition pyaudio")
        sys.exit(1)
    
    model = get_model()
    messages = []
    stop_requested = False
    
    def check_stop_flag():
        return stop_requested
    
    print("=" * 60)
    print("  Big Pickle AI Assistant - VOICE MODE")
    print("=" * 60)
    print(f"Model: {model}")
    print(f"Wake word: '{WAKE_WORD}'")
    print("")
    print("Say 'Assistant' followed by your request.")
    print("Capabilities: launch apps, search web, read/write files, list dirs")
    print("")
    print("Say 'shut up' or 'stop talking' to interrupt TTS.")
    print("Say 'quit' or 'exit' to stop. Press Ctrl+C at any time.")
    print("-" * 60)
    
    if not ESPPEAK_INSTALLED and not GTTS_AVAILABLE and not check_installed('say'):
        print("\n[Warning: No TTS engine found. Install espeak or gTTS]")
    
    recognizer = sr.Recognizer()
    recognizer.energy_threshold = energy_threshold
    recognizer.dynamic_energy_threshold = True
    
    try:
        with sr.Microphone() as source:
            print("[Calibrating microphone for 2 seconds...]")
            recognizer.adjust_for_ambient_noise(source, duration=2)
            print(f"[Energy threshold set to: {recognizer.energy_threshold:.0f}]")
            print("[Ready - say 'Assistant' to get my attention!]")
            
            speak("Voice mode activated. Say Assistant followed by your question.", use_gtts)
            
            while True:
                try:
                    text = listen_once(recognizer, source, use_whisper=use_whisper, phrase_time_limit=15)
                    
                    if not text:
                        continue
                    
                    print(f"\n[Heard]: {text}")
                    
                    if check_stop_phrase(text):
                        print("[Stop phrase detected!]")
                        stop_tts()
                        continue
                    
                    has_wake, query = check_wake_word(text)
                    
                    if has_wake:
                        print(f"[Wake word detected!]")
                        
                        query_lower = query.lower()
                        if any(word in query_lower for word in ['quit', 'exit', 'goodbye', 'bye', 'stop']):
                            print("[Exit command received]")
                            speak("Goodbye!", use_gtts)
                            break
                        
                        if not query or len(query.strip()) < 2:
                            speak("Yes? How can I help you?", use_gtts)
                            print("\n[Listening for your question...]")
                            text = listen_once(recognizer, source, use_whisper=use_whisper, phrase_time_limit=20)
                            if text:
                                print(f"[Heard]: {text}")
                                query = text
                            else:
                                print("[No question received]")
                                continue
                        
                        query_lower = query.lower()
                        if any(word in query_lower for word in ['quit', 'exit', 'goodbye', 'bye', 'stop']):
                            print("[Exit command received]")
                            speak("Goodbye!", use_gtts)
                            break
                        
                        print(f"\n[Query]: {query}")
                        print("[Thinking...]")
                        
                        try:
                            reply, messages = ask_with_tools(query, model, messages, use_tts=True, tts_gtts=use_gtts)
                            
                            print(f"\n[Assistant]: {reply}")
                            print("-" * 60)
                            
                            stop_requested = False
                            speak(reply, use_gtts)
                            
                        except Exception as e:
                            print(f"[Error: {e}]")
                            speak("Sorry, there was an error processing your request.", use_gtts)
                        
                except KeyboardInterrupt:
                    print("\n\n[Exit requested by user]")
                    stop_tts()
                    speak("Goodbye!", use_gtts)
                    break
                    
    except OSError as e:
        if "Invalid input device" in str(e) or "No Default Input Device" in str(e):
            print("Error: No microphone found or microphone not accessible.")
            print("")
            print("Troubleshooting:")
            print("1. Check if your microphone is connected and working")
            print("2. On Arch Linux: sudo pacman -S portaudio")
            print("3. Install pyaudio: pip install pyaudio")
            sys.exit(1)
        else:
            raise
    finally:
        _voice_mode_active = False

def interactive_mode():
    model = get_model()
    print("=" * 50)
    print("  Big Pickle AI Assistant (Interactive Mode)")
    print("=" * 50)
    print(f"Model: {model}")
    print("")
    print("Capabilities:")
    print("  - Ask questions and chat")
    print("  - Launch apps: 'launch firefox', 'open discord'")
    print("  - Web search: 'search the web for python tutorial'")
    print("  - Read files: 'read ~/test.txt'")
    print("  - Write files: 'write hello to ~/test.txt'")
    print("  - List files: 'list my documents'")
    print("  - Delete files: 'delete ~/test.txt' (with confirmation)")
    print("")
    print("Type 'exit' or 'quit' to end.")
    print("-" * 50)
    
    messages = []
    
    while True:
        try:
            user_input = input("\nYou: ").strip()
            
            if user_input.lower() in ['exit', 'quit']:
                print("Goodbye!")
                break
            
            if user_input.lower() == 'model':
                print(f"\nCurrent model: {model}")
                print(f"Available free models: big-pickle, nemotron-3-super-free, hy3-preview-free,")
                print(f"                       ling-2.6-flash-free, minimax-m2.5-free, trinity-large-preview-free")
                new_model = input("Enter new model name (or press Enter to keep current): ").strip()
                if new_model:
                    model = new_model
                continue
            
            if user_input.lower() == 'apps':
                print("\n[Refreshing app list...]")
                global APP_CACHE
                APP_CACHE = get_available_apps()
                print(f"Found {len(APP_CACHE)} apps. Some examples:")
                for app in list(APP_CACHE.keys())[:20]:
                    print(f"  - {app}")
                continue
            
            if not user_input:
                continue
            
            print("\n[Thinking...]")
            
            try:
                reply, messages = ask_with_tools(user_input, model, messages)
                print(f"\nAssistant: {reply}")
            except Exception as e:
                print(f"\nError: {e}")
            
        except KeyboardInterrupt:
            print("\n\nExiting...")
            break
        except Exception as e:
            print(f"\nError: {e}")

def print_help():
    print("Big Pickle AI Assistant")
    print("")
    print("Usage:")
    print("  python big_pickle.py <your question>    # Single question")
    print("  python big_pickle.py -i                  # Interactive text chat mode")
    print("  python big_pickle.py -v                  # Voice mode (wake word: 'Assistant')")
    print("  python big_pickle.py -v -s               # Voice mode with FULL SYSTEM ACCESS")
    print("  python big_pickle.py -i -s               # Interactive mode with FULL SYSTEM ACCESS")
    print("  python big_pickle.py -v --gtts           # Voice mode using Google TTS (online)")
    print("  python big_pickle.py -v --whisper        # Voice mode using Whisper for STT")
    print("  python big_pickle.py -v --energy 500     # Voice mode with custom mic sensitivity")
    print("")
    print("FLAGS:")
    print("  -s, --system   FULL SYSTEM ACCESS MODE (UNRESTRICTED)")
    print("                 - Run ANY shell command (sudo, rm, pacman, apt, pip, etc.)")
    print("                 - Read/write/delete ANY file")
    print("                 - No safety restrictions")
    print("                 WARNING: Use with caution!")
    print("")
    print("STOP TTS WHILE SPEAKING:")
    print("  - Press Ctrl+C at any time")
    print("  - Say: 'shut up', 'stop talking', 'be quiet', 'stop it'")
    print("")
    print("Install voice dependencies on Arch Linux:")
    print("  sudo pacman -S espeak portaudio python-pip")
    print("  pip install SpeechRecognition pyaudio")
    print("")
    print("Free models (set via BIG_PICKLE_MODEL env var):")
    print("  big-pickle, nemotron-3-super-free, hy3-preview-free,")
    print("  ling-2.6-flash-free, minimax-m2.5-free, trinity-large-preview-free")
    sys.exit(0)

def main():
    global FULL_SYSTEM_ACCESS
    
    signal.signal(signal.SIGINT, _signal_handler)
    
    args = sys.argv[1:]
    
    flags = {'-s', '--system', '-h', '--help', '-v', '-i', '--gtts', '--whisper', '--energy'}
    
    flag_args = [a for a in args if a in flags or (a.isdigit() and len(args) > args.index(a) and args[args.index(a)-1] == '--energy')]
    question_args = [a for a in args if a not in flags and not (a.isdigit() and len(args) > args.index(a) and args[args.index(a)-1] == '--energy')]
    
    if '-s' in args or '--system' in args:
        FULL_SYSTEM_ACCESS = True
        print("\n" + "=" * 60)
        print("  ⚠️  FULL SYSTEM ACCESS MODE ENABLED")
        print("=" * 60)
        print("  - All shell commands allowed")
        print("  - No file path restrictions")
        print("  - Use with caution!")
        print("=" * 60 + "\n")
        time.sleep(1)
    
    if '-h' in args or '--help' in args:
        print_help()
    
    if '-v' in args:
        use_gtts = '--gtts' in args
        use_whisper = '--whisper' in args
        
        energy_threshold = 300
        if '--energy' in args:
            idx = args.index('--energy')
            if idx + 1 < len(args):
                try:
                    energy_threshold = int(args[idx + 1])
                except:
                    pass
        
        voice_mode(use_gtts=use_gtts, use_whisper=use_whisper, energy_threshold=energy_threshold)
    elif '-i' in args:
        interactive_mode()
    elif question_args:
        question = " ".join(question_args)
        messages = []
        result, _ = ask_with_tools(question, messages_context=messages)
        print(result)
    else:
        if not sys.stdin.isatty():
            question = sys.stdin.read().strip()
            if question:
                result, _ = ask_with_tools(question)
                print(result)
            else:
                print("Error: No input received from stdin")
                sys.exit(1)
        else:
            print_help()

if __name__ == "__main__":
    main()
