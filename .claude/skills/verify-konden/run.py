#!/usr/bin/env python3
"""Start, inspect and stop one isolated konden Web verification run.

A run owns one data directory, one port and one process. Ready means doctor.py
exits 0; the uvicorn log line is the same moment, not a substitute.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
DOCTOR = Path(__file__).with_name("doctor.py")
RUN_PREFIX = "konden-verify-"
EVIDENCE_ROOT = "konden-verify-evidence"
TEMP_ROOT = Path(tempfile.gettempdir()).resolve()


def project_python() -> Path:
    python = ROOT / "konden-env" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        raise SystemExit(
            f"{python} is missing. Run scripts/setup.sh (scripts\\setup.ps1 on Windows) once, then retry."
        )
    return python


def require_bundle() -> None:
    bundle = ROOT / "web" / "dist" / "index.html"
    if not bundle.is_file():
        raise SystemExit(
            f"{bundle} is missing. An isolated port must serve the built bundle, not the Vite dev server: "
            "run ( cd web && npm ci && npm run build )."
        )


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def remove_tree(path: Path) -> None:
    """Delete a run directory, including the read-only objects git writes into the workspace."""

    def force(function, target, error):
        Path(target).chmod(stat.S_IWRITE)
        function(target)

    for attempt in range(5):
        try:
            shutil.rmtree(path, onexc=force)
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt == 4:
                raise
            time.sleep(0.2)


def seed_workspace(workspace: Path) -> None:
    (workspace / "note.txt").write_text("verify\n", encoding="utf-8")
    identity = ["-c", "user.name=verify", "-c", "user.email=verify@example.invalid"]
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    subprocess.run(["git", "-C", str(workspace), "add", "note.txt"], check=True)
    subprocess.run(["git", "-C", str(workspace), *identity, "commit", "-qm", "init"], check=True)


def spawn(python: Path, home: Path, port: int, log: Path, error_log: Path) -> int:
    environment = {**os.environ, "KONDEN_HOME": str(home), "PYTHONUNBUFFERED": "1"}
    command = [str(python), "web_chat.py", "--host", "127.0.0.1", "--port", str(port)]
    options: dict = {"cwd": ROOT, "env": environment, "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        options["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        # Own session, so this process never receives the driving terminal's Ctrl+C.
        options["start_new_session"] = True
    with log.open("wb") as out, error_log.open("wb") as err:
        process = subprocess.Popen(command, stdout=out, stderr=err, **options)
    return process.pid


def doctor(record: dict, timeout: str = "2", require_model: bool = False) -> tuple[int, dict]:
    command = [
        sys.executable, str(DOCTOR),
        "--url", record["url"],
        "--home", record["home"],
        "--pid-file", record["pid_file"],
        "--timeout", timeout,
    ]
    if require_model:
        command.append("--require-model")
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    try:
        body = json.loads(result.stdout)
    except json.JSONDecodeError:
        body = {"ok": False, "blockers": [f"doctor.py printed no report: {result.stdout}{result.stderr}"]}
    return result.returncode, body


def stop_process(record: dict) -> int | None:
    try:
        pid = int(Path(record["pid_file"]).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 15)
    except OSError:
        return None
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not process_alive(pid):
            return pid
        time.sleep(0.2)
    with contextlib.suppress(OSError):
        os.kill(pid, 9)
    return pid


def process_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def load_record(run_dir: Path) -> dict:
    directory = run_dir.expanduser().resolve()
    if not directory.name.startswith(RUN_PREFIX) or directory.parent != TEMP_ROOT:
        raise SystemExit(f"{directory} is not a run directory created by this script.")
    record = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    if Path(record["run_dir"]).resolve() != directory:
        raise SystemExit(f"{directory}/run.json describes a different run directory.")
    return record


def emit(payload: dict) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def start(args) -> int:
    python = project_python()
    require_bundle()
    run_id = time.strftime("%Y%m%d%H%M%S")
    run_dir = TEMP_ROOT / f"{RUN_PREFIX}{run_id}"
    home = run_dir / "home"
    workspace = run_dir / "workspace"
    evidence = TEMP_ROOT / EVIDENCE_ROOT / run_id
    for path in (home, workspace, evidence):
        path.mkdir(parents=True, exist_ok=False)
    seed_workspace(workspace)
    port = free_port()
    record = {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "home": str(home),
        "workspace": str(workspace),
        "evidence_dir": str(evidence),
        "url": f"http://127.0.0.1:{port}",
        "port": port,
        "pid_file": str(run_dir / "web.pid"),
        "log": str(run_dir / "web.log"),
        "error_log": str(run_dir / "web.err"),
    }
    record["pid"] = spawn(python, home, port, run_dir / "web.log", run_dir / "web.err")
    Path(record["pid_file"]).write_text(str(record["pid"]), encoding="utf-8")
    (run_dir / "run.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    code, report = 1, {}
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        code, report = doctor(record, require_model=args.require_model)
        if code == 0:
            break
        time.sleep(0.4)
    if code != 0:
        tail = (run_dir / "web.log").read_text(encoding="utf-8", errors="replace")[-2000:]
        stop_process(record)
        payload = {**record, "ok": False, "doctor": report, "log_tail": tail}
        try:
            remove_tree(run_dir)
        except OSError as error:
            payload["cleanup_error"] = str(error)
        emit(payload)
        return 1
    emit({**record, "ok": True, "doctor": report})
    return 0


def status(args) -> int:
    record = load_record(Path(args.run_dir))
    code, report = doctor(record, timeout="10", require_model=args.require_model)
    emit({**record, "ok": code == 0, "doctor": report})
    return code


def stop(args) -> int:
    record = load_record(Path(args.run_dir))
    pid = stop_process(record)
    run_dir = Path(record["run_dir"])
    if Path(record["evidence_dir"]).resolve().is_relative_to(run_dir.resolve()):
        raise SystemExit("refusing to delete a run directory that also holds this run's evidence.")
    remove_tree(run_dir)
    emit({"ok": True, "stopped_pid": pid, "run_dir": record["run_dir"], "evidence_dir": record["evidence_dir"]})
    return 0


def evidence(args) -> int:
    record = load_record(Path(args.run_dir))
    directory = Path(record["evidence_dir"]) / args.feature
    directory.mkdir(parents=True, exist_ok=True)
    emit({"ok": True, "feature": args.feature, "path": str(directory)})
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    start_parser = commands.add_parser("start", help="create a run, start web_chat.py, wait until Doctor passes")
    start_parser.add_argument("--timeout", type=float, default=60, help="seconds to wait for readiness")
    start_parser.add_argument("--require-model", action="store_true",
                              help="treat a missing default-model provider as a blocker")
    start_parser.set_defaults(handler=start)

    status_parser = commands.add_parser("status", help="rerun Doctor against an existing run")
    status_parser.add_argument("--run-dir", required=True)
    status_parser.add_argument("--require-model", action="store_true")
    status_parser.set_defaults(handler=status)

    stop_parser = commands.add_parser("stop", help="stop the recorded process and delete the run directory")
    stop_parser.add_argument("--run-dir", required=True)
    stop_parser.set_defaults(handler=stop)

    evidence_parser = commands.add_parser("evidence", help="create this run's directory for one feature")
    evidence_parser.add_argument("--run-dir", required=True)
    evidence_parser.add_argument("--feature", required=True)
    evidence_parser.set_defaults(handler=evidence)

    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
