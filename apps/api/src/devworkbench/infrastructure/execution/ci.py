from __future__ import annotations

import os, shlex, subprocess, sys, time
from pathlib import Path
from typing import Any

ALLOWED = {x.strip().lower() for x in os.environ.get('DEVWB_CI_ALLOWLIST', 'python,pytest,node,npm,uv,ruff').split(',') if x.strip()}

def run_command(command: str, cwd: str, output: Path, timeout: int = 120) -> dict[str, Any]:
    if not command.strip(): raise ValueError('CI command cannot be empty')
    if any(x in command for x in ('&&', '||', ';', '|', '>', '<', '`', '$(', '&')): raise ValueError('shell operators are not allowed')
    args = shlex.split(command, posix=False)
    if not args: raise ValueError('CI command cannot be empty')
    executable = args[0].strip(chr(34))
    if (not executable or Path(executable).name != executable or '/' in executable or '\\' in executable
            or Path(executable).is_absolute() or (len(executable) >= 2 and executable[1] == ':')
            or '..' in Path(executable).parts):
        raise ValueError('CI executable must be an allowlisted command name')
    if any(not arg or arg in ('.', '..') or Path(arg.strip(chr(34))).is_absolute() or arg.startswith(('/', '\\')) or (len(arg) >= 3 and arg[1] == ':' and arg[2] in '/\\') or '..' in Path(arg.strip(chr(34))).parts for arg in args[1:]):
        raise ValueError('absolute paths and parent traversal are not allowed in CI arguments')
    exe = Path(args[0].strip(chr(34))).name.lower()
    if exe.endswith('.exe'): exe = exe[:-4]
    if exe not in ALLOWED: raise ValueError('CI command not allowlisted: ' + exe)
    if exe == 'python': args[0] = sys.executable
    output.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    try:
        proc = subprocess.run(args, cwd=str(Path(cwd).resolve()), capture_output=True, text=True, timeout=min(max(timeout, 1), 300), check=False)
        code = proc.returncode; text = (proc.stdout or '') + ('\n' + proc.stderr if proc.stderr else '')
    except subprocess.TimeoutExpired as exc:
        code = 124; text = 'TIMEOUT after ' + str(timeout) + 's\n' + str(exc.stdout or '') + '\n' + str(exc.stderr or '')
    output.write_text(text, encoding='utf-8')
    return {'status': 'passed' if code == 0 else 'failed', 'exit_code': code, 'duration_ms': round((time.time() - started) * 1000), 'stdout_path': str(output), 'command': command}
