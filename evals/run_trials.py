#!/usr/bin/env python3
"""Reproducible Codex CLI operator for frozen behavioral-evaluation trials.

This script is deliberately separate from scoring. It reads the allocator's
private dispatch order, inlines only the files staged for one trial, launches a
fresh ephemeral `codex exec` process with model tools disabled, and preserves
the first raw final output and event logs. It never reads evaluation oracles.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import errno
import hashlib
import json
import os
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EVAL_ROOT = Path(__file__).resolve().parent
REPO_ROOT = EVAL_ROOT.parent
RESPONSE_SCHEMA_PATH = EVAL_ROOT / "schemas" / "response.schema.json"
OPERATOR_VERSION = "1.8"
BATCH_HEARTBEAT_SECONDS = 10.0
PROCESS_TREE_CLEANUP_SECONDS = 5.0
PROCESS_TREE_POLL_SECONDS = 0.01
PROCESS_ROOT_REAP_GRACE_SECONDS = 0.25
POSIX_SIGKILL = getattr(signal, "SIGKILL", 9)
WINDOWS_CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
WINDOWS_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
WINDOWS_CREATE_SUSPENDED = getattr(subprocess, "CREATE_SUSPENDED", 0x00000004)
TRIAL_ID_RE = re.compile(r"^trial-[0-9a-f]{16}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REQUEST_SEED_STATUS = (
    "unsupported: captured Codex exec help exposed no --seed or --request-seed "
    "option; allocation model_seed recorded but not applied"
)
MODEL_SEED_NOTE = (
    "Not applied; this batch's captured Codex exec help exposed no --seed "
    "or --request-seed option."
)
PROMPT_ISOLATION_MARKER = "COMMUNITY_SIGNAL_PROMPT_ISOLATION_PROBE"
PROMPT_ISOLATION_FORBIDDEN_MARKERS = (
    "<skills_instructions",
    "available skills",
    "skill.md",
    "/.codex/skills",
    "\\.codex\\skills",
)
SKILL_CONFIG_OVERRIDES = (
    "skills.include_instructions=false",
    "skills.bundled.enabled=false",
)


class ProcessContainmentError(RuntimeError):
    """The operator could not prove or enforce containment of a model process tree."""


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
    JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION_CLASS = 1
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    STATUS_CONTROL_C_EXIT = 0xC000013A
    TH32CS_SNAPTHREAD = 0x00000004
    THREAD_SUSPEND_RESUME = 0x0002
    THREAD_QUERY_LIMITED_INFORMATION = 0x0800
    ERROR_NO_MORE_FILES = 18
    DWORD_FAILURE = 0xFFFFFFFF
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", wintypes.LARGE_INTEGER),
            ("TotalKernelTime", wintypes.LARGE_INTEGER),
            ("ThisPeriodTotalUserTime", wintypes.LARGE_INTEGER),
            ("ThisPeriodTotalKernelTime", wintypes.LARGE_INTEGER),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    class _THREADENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", wintypes.LONG),
            ("tpDeltaPri", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
        ]

    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _KERNEL32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _KERNEL32.CreateJobObjectW.restype = wintypes.HANDLE
    _KERNEL32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    _KERNEL32.SetInformationJobObject.restype = wintypes.BOOL
    _KERNEL32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _KERNEL32.AssignProcessToJobObject.restype = wintypes.BOOL
    _KERNEL32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _KERNEL32.TerminateJobObject.restype = wintypes.BOOL
    _KERNEL32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _KERNEL32.QueryInformationJobObject.restype = wintypes.BOOL
    _KERNEL32.CloseHandle.argtypes = [wintypes.HANDLE]
    _KERNEL32.CloseHandle.restype = wintypes.BOOL
    _KERNEL32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _KERNEL32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _KERNEL32.Thread32First.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_THREADENTRY32),
    ]
    _KERNEL32.Thread32First.restype = wintypes.BOOL
    _KERNEL32.Thread32Next.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_THREADENTRY32),
    ]
    _KERNEL32.Thread32Next.restype = wintypes.BOOL
    _KERNEL32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _KERNEL32.OpenThread.restype = wintypes.HANDLE
    _KERNEL32.GetProcessIdOfThread.argtypes = [wintypes.HANDLE]
    _KERNEL32.GetProcessIdOfThread.restype = wintypes.DWORD
    _KERNEL32.ResumeThread.argtypes = [wintypes.HANDLE]
    _KERNEL32.ResumeThread.restype = wintypes.DWORD

    def _close_windows_handle(handle: int, label: str) -> None:
        if not _KERNEL32.CloseHandle(handle):
            error = ctypes.WinError(ctypes.get_last_error())
            raise ProcessContainmentError(f"CloseHandle({label}) failed: {error}")

    def resume_suspended_windows_process(process: subprocess.Popen[bytes]) -> None:
        """Validate and resume the sole primary thread of a suspended process."""

        process_id = getattr(process, "pid", None)
        if not isinstance(process_id, int) or process_id <= 0:
            raise ProcessContainmentError(
                "suspended-process resume failed: Popen exposed no valid process id"
            )

        snapshot = _KERNEL32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
        if snapshot == INVALID_HANDLE_VALUE:
            error = ctypes.WinError(ctypes.get_last_error())
            raise ProcessContainmentError(
                f"CreateToolhelp32Snapshot(threads) failed: {error}"
            )

        thread_ids: list[int] = []
        try:
            entry = _THREADENTRY32()
            entry.dwSize = ctypes.sizeof(entry)
            ctypes.set_last_error(0)
            have_entry = bool(_KERNEL32.Thread32First(snapshot, ctypes.byref(entry)))
            if not have_entry:
                error_code = ctypes.get_last_error()
                if error_code != ERROR_NO_MORE_FILES:
                    raise ProcessContainmentError(
                        "Thread32First failed: "
                        f"{ctypes.WinError(error_code)}"
                    )
            while have_entry:
                if int(entry.th32OwnerProcessID) == process_id:
                    thread_ids.append(int(entry.th32ThreadID))
                entry.dwSize = ctypes.sizeof(entry)
                ctypes.set_last_error(0)
                have_entry = bool(_KERNEL32.Thread32Next(snapshot, ctypes.byref(entry)))
                if not have_entry:
                    error_code = ctypes.get_last_error()
                    if error_code != ERROR_NO_MORE_FILES:
                        raise ProcessContainmentError(
                            "Thread32Next failed: "
                            f"{ctypes.WinError(error_code)}"
                        )
        finally:
            _close_windows_handle(snapshot, "thread snapshot")

        if len(thread_ids) != 1:
            raise ProcessContainmentError(
                "suspended-process resume required exactly one primary thread; "
                f"found {len(thread_ids)} for pid {process_id}"
            )

        thread_handle = _KERNEL32.OpenThread(
            THREAD_SUSPEND_RESUME | THREAD_QUERY_LIMITED_INFORMATION,
            False,
            thread_ids[0],
        )
        if not thread_handle:
            error = ctypes.WinError(ctypes.get_last_error())
            raise ProcessContainmentError(f"OpenThread(primary) failed: {error}")
        try:
            owner_id = int(_KERNEL32.GetProcessIdOfThread(thread_handle))
            if owner_id != process_id:
                if owner_id == 0:
                    detail = str(ctypes.WinError(ctypes.get_last_error()))
                else:
                    detail = f"thread now belongs to pid {owner_id}"
                raise ProcessContainmentError(
                    f"primary-thread ownership verification failed: {detail}"
                )
            previous_suspend_count = int(_KERNEL32.ResumeThread(thread_handle))
            if previous_suspend_count == DWORD_FAILURE:
                error = ctypes.WinError(ctypes.get_last_error())
                raise ProcessContainmentError(f"ResumeThread(primary) failed: {error}")
            if previous_suspend_count != 1:
                raise ProcessContainmentError(
                    "ResumeThread(primary) violated the single-suspend invariant: "
                    f"previous count was {previous_suspend_count}"
                )
        finally:
            _close_windows_handle(thread_handle, "primary thread")

    class WindowsJob:
        def __init__(self) -> None:
            self._lock = threading.Lock()
            self._closed = False
            self._terminated = False
            handle = _KERNEL32.CreateJobObjectW(None, None)
            if not handle:
                raise ProcessContainmentError(
                    f"CreateJobObjectW failed: {ctypes.WinError(ctypes.get_last_error())}"
                )
            self._handle = handle
            limits = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not _KERNEL32.SetInformationJobObject(
                self._handle,
                JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                ctypes.byref(limits),
                ctypes.sizeof(limits),
            ):
                error = ctypes.WinError(ctypes.get_last_error())
                close_error: BaseException | None = None
                if _KERNEL32.CloseHandle(self._handle):
                    self._closed = True
                else:
                    close_error = ctypes.WinError(ctypes.get_last_error())
                detail = f"SetInformationJobObject failed: {error}"
                if close_error is not None:
                    detail += f"; CloseHandle(job) also failed: {close_error}"
                raise ProcessContainmentError(detail)

        def assign(self, process: subprocess.Popen[bytes]) -> None:
            process_handle = getattr(process, "_handle", None)
            if process_handle is None:
                raise ProcessContainmentError(
                    "AssignProcessToJobObject failed: Popen exposed no Windows process handle"
                )
            if not _KERNEL32.AssignProcessToJobObject(self._handle, int(process_handle)):
                error = ctypes.WinError(ctypes.get_last_error())
                raise ProcessContainmentError(f"AssignProcessToJobObject failed: {error}")

        def terminate(self, process: subprocess.Popen[bytes]) -> None:
            del process
            with self._lock:
                if self._closed or self._terminated:
                    return
                if not _KERNEL32.TerminateJobObject(self._handle, STATUS_CONTROL_C_EXIT):
                    error = ctypes.WinError(ctypes.get_last_error())
                    raise ProcessContainmentError(f"TerminateJobObject failed: {error}")
                self._terminated = True

        def active_processes(self) -> int:
            with self._lock:
                if self._closed:
                    raise ProcessContainmentError(
                        "QueryInformationJobObject failed: job handle is already closed"
                    )
                accounting = _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
                returned = wintypes.DWORD()
                if not _KERNEL32.QueryInformationJobObject(
                    self._handle,
                    JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION_CLASS,
                    ctypes.byref(accounting),
                    ctypes.sizeof(accounting),
                    ctypes.byref(returned),
                ):
                    error = ctypes.WinError(ctypes.get_last_error())
                    raise ProcessContainmentError(
                        f"QueryInformationJobObject failed: {error}"
                    )
                return int(accounting.ActiveProcesses)

        def wait_empty(self, timeout_seconds: float) -> None:
            wait_until_empty(self.active_processes, timeout_seconds, "Windows Job")

        def close(self) -> None:
            with self._lock:
                if self._closed:
                    return
                if not _KERNEL32.CloseHandle(self._handle):
                    error = ctypes.WinError(ctypes.get_last_error())
                    raise ProcessContainmentError(f"CloseHandle(job) failed: {error}")
                self._closed = True

else:
    WindowsJob = None  # type: ignore[assignment,misc]
    resume_suspended_windows_process = None  # type: ignore[assignment]

SMOKE_EXPECTED_RESPONSE = {
    "schema_version": "1.0",
    "case_id": "case-00-smoke",
    "signal_id": "sig-smoke",
    "recommendation": "insufficient_evidence",
    "support_assessment": "unsupported",
    "independent_support": {"authors": 0, "threads": 0, "source_ids": []},
    "excluded_or_collapsed_sources": [],
    "counterevidence": {
        "status": "not_established",
        "source_ids": [],
        "summary": "Smoke test only.",
    },
    "wtp": {
        "level": "none",
        "basis": "none",
        "source_ids": [],
        "summary": "No evidence was supplied.",
    },
    "public_memo": "This is only an operator smoke test.",
    "citations": [
        {
            "source_id": "src-smoke",
            "visibility": "public",
            "locator": "https://example.com/smoke",
            "source_file_sha256": None,
            "excerpt": "Smoke test only.",
        }
    ],
    "limitations": ["No research evidence was supplied."],
    "next_test": "Run the frozen evaluation only after this smoke test passes.",
}

CORE_FILES = (
    Path("task.md"),
    Path("treatment.md"),
    Path("packet.json"),
    Path("response.schema.json"),
)

TREATMENT_FILES = (
    Path("skill/community-signal-research/SKILL.md"),
    Path("skill/community-signal-research/references/method.md"),
    Path("skill/community-signal-research/references/scoring.md"),
    Path("skill/community-signal-research/references/data-contracts.md"),
    Path("skill/community-signal-research/references/source-playbooks.md"),
)

# Every feature in this list is disabled for both conditions. The packet and
# treatment are supplied inline, so the agent needs no model-callable tools.
DISABLED_FEATURES = (
    "apps",
    "browser_use",
    "browser_use_external",
    "browser_use_full_cdp_access",
    "code_mode_host",
    "computer_use",
    "goals",
    "hooks",
    "image_generation",
    "in_app_browser",
    "memories",
    "multi_agent",
    "multi_agent_v2",
    "plugin_sharing",
    "plugins",
    "remote_plugin",
    "shell_snapshot",
    "shell_snapshot_v2",
    "shell_tool",
    "skill_search",
    "sleep_tool",
    "tool_suggest",
    "view_image",
    "workspace_dependencies",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def emit_json_line(value: Any, *, stream: Any = None) -> bool:
    """Emit best-effort telemetry without making stdout an integrity dependency."""

    target = sys.stdout if stream is None else stream
    try:
        print(json.dumps(value, ensure_ascii=False, sort_keys=True), file=target, flush=True)
    except (BrokenPipeError, OSError, ValueError):
        return False
    return True


def child_process_isolation(platform_name: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return Popen controls and their matching auditable configuration record."""

    effective_platform = os.name if platform_name is None else platform_name
    if effective_platform == "nt":
        creationflags = (
            WINDOWS_CREATE_NEW_PROCESS_GROUP
            | WINDOWS_CREATE_NO_WINDOW
            | WINDOWS_CREATE_SUSPENDED
        )
        return (
            {"creationflags": creationflags, "close_fds": True},
            {
                "mode": "windows_suspended_nested_job_kill_on_close",
                "creationflags": creationflags,
                "close_fds": True,
                "create_suspended": True,
                "kill_on_job_close": True,
                "assignment_policy": (
                    "create_suspended_assign_validate_primary_thread_resume_fail_closed"
                ),
                "target_execution_before_assignment": False,
                "containment_scope": (
                    "direct CreateProcess descendants while breakaway remains disabled"
                ),
                "cleanup_timeout_seconds": PROCESS_TREE_CLEANUP_SECONDS,
                "cleanup_policy": "terminate_reap_verify_empty_close_fail_closed",
                "drain_verification": "job_basic_accounting_active_processes_zero",
            },
        )
    return (
        {"start_new_session": True, "close_fds": True},
        {
            "mode": "posix_session_process_group_cooperative_cleanup",
            "start_new_session": True,
            "close_fds": True,
            "escape_resistant": False,
            "containment_scope": "original POSIX process group only",
            "trust_assumption": (
                "the child and descendants do not call setsid/setpgid or delegate "
                "process creation to an external service"
            ),
            "termination_signal": "SIGKILL",
            "cleanup_timeout_seconds": PROCESS_TREE_CLEANUP_SECONDS,
            "cleanup_policy": "terminate_reap_verify_empty_close_fail_closed",
            "drain_verification": "original_process_group_killpg_zero_until_esrch",
        },
    )


def wait_until_empty(
    active_count: Any,
    timeout_seconds: float,
    label: str,
    *,
    monotonic_fn: Any = None,
    sleep_fn: Any = None,
) -> None:
    """Poll an auditable active-process count until it reaches zero."""

    clock = time.monotonic if monotonic_fn is None else monotonic_fn
    sleeper = time.sleep if sleep_fn is None else sleep_fn
    deadline = clock() + max(0.0, timeout_seconds)
    while True:
        active = active_count()
        if active == 0:
            return
        remaining = deadline - clock()
        if remaining <= 0:
            raise ProcessContainmentError(
                f"{label} still reports {active} active process(es) after "
                f"{timeout_seconds:.3f} seconds"
            )
        sleeper(min(PROCESS_TREE_POLL_SECONDS, remaining))


class PosixProcessGroup:
    def __init__(self, process_id: int) -> None:
        self._process_group = process_id
        self._lock = threading.Lock()
        self._terminated = False
        self._empty = False

    def assign(self, process: subprocess.Popen[bytes]) -> None:
        del process

    def terminate(self, process: subprocess.Popen[bytes]) -> None:
        del process
        with self._lock:
            if self._terminated:
                return
            try:
                os.killpg(self._process_group, POSIX_SIGKILL)
            except OSError as exc:
                if exc.errno != errno.ESRCH:
                    raise
                self._empty = True
            self._terminated = True

    def active_processes(self) -> int:
        with self._lock:
            if self._empty:
                return 0
            try:
                os.killpg(self._process_group, 0)
            except OSError as exc:
                if exc.errno != errno.ESRCH:
                    raise
                self._empty = True
                return 0
            return 1

    def wait_empty(self, timeout_seconds: float) -> None:
        wait_until_empty(self.active_processes, timeout_seconds, "POSIX process group")

    def close(self) -> None:
        return


class ManagedChild:
    def __init__(self, process: subprocess.Popen[bytes], containment: Any) -> None:
        self.process = process
        self.containment = containment
        self._lifecycle_lock = threading.Lock()
        self._cleanup_complete = False

    def terminate_tree(self) -> None:
        with self._lifecycle_lock:
            if self._cleanup_complete:
                return
            self.containment.terminate(self.process)

    def wait_tree_empty(self, timeout_seconds: float) -> None:
        with self._lifecycle_lock:
            if self._cleanup_complete:
                return
            self.containment.wait_empty(timeout_seconds)

    def close_tree(self) -> None:
        with self._lifecycle_lock:
            if self._cleanup_complete:
                return
            self.containment.close()
            self._cleanup_complete = True

    def cleanup_tree(self, timeout_seconds: float = PROCESS_TREE_CLEANUP_SECONDS) -> None:
        """Terminate, reap, verify an empty tree, and close its containment handle."""

        with self._lifecycle_lock:
            if self._cleanup_complete:
                return
            deadline = time.monotonic() + max(0.0, timeout_seconds)
            failures: list[BaseException] = []
            termination_succeeded = False
            root_reaped = getattr(self.process, "returncode", None) is not None
            empty_proven = False
            close_succeeded = False
            try:
                self.containment.terminate(self.process)
                termination_succeeded = True
                # communicate(), a concurrent waiter, or containment termination
                # can publish the root return code after the initial snapshot.
                root_reaped = root_reaped or getattr(self.process, "returncode", None) is not None
            except BaseException as exc:
                failures.append(exc)

            # Never close or mark a containment boundary complete after a failed
            # tree-termination request. A batch worker leaves that child in the
            # active registry so the supervisor can retry after all workers stop.
            if termination_succeeded:
                if getattr(self.process, "returncode", None) is None:
                    try:
                        self.process.wait(
                            timeout=min(
                                PROCESS_ROOT_REAP_GRACE_SECONDS,
                                max(0.0, deadline - time.monotonic()),
                            )
                        )
                        root_reaped = True
                    except BaseException as exc:
                        failures.append(exc)
                        try:
                            if self.process.poll() is None:
                                self.process.kill()
                            self.process.wait(timeout=max(0.0, deadline - time.monotonic()))
                            root_reaped = True
                        except BaseException as fallback_exc:
                            failures.append(fallback_exc)
                else:
                    root_reaped = True

                try:
                    self.containment.wait_empty(max(0.0, deadline - time.monotonic()))
                    empty_proven = True
                except BaseException as exc:
                    failures.append(exc)

            if root_reaped and empty_proven:
                try:
                    self.containment.close()
                    close_succeeded = True
                except BaseException as exc:
                    failures.append(exc)
            if termination_succeeded and root_reaped and empty_proven and close_succeeded:
                self._cleanup_complete = True
            elif not failures:
                failures.append(
                    ProcessContainmentError(
                        "cleanup returned without a complete terminate/reap/empty/close proof"
                    )
                )

            if failures:
                details = "; ".join(
                    f"{type(failure).__name__}: {failure}" for failure in failures
                )
                raise ProcessContainmentError(
                    f"process-tree cleanup could not be proven: {details}"
                ) from failures[0]


class DeferredLaunchInterrupts:
    """Defer catchable console interrupts and tracing hooks during publication."""

    def __init__(self) -> None:
        self._old_trace: Any = None
        self._old_profile: Any = None
        self._old_sigint_handler: Any = None
        self._pending_sigint: tuple[int, Any] | None = None
        self._main_thread = threading.current_thread() is threading.main_thread()

    def _defer_sigint(self, signum: int, frame: Any) -> None:
        self._pending_sigint = (signum, frame)

    def __enter__(self) -> DeferredLaunchInterrupts:
        self._old_trace = sys.gettrace()
        self._old_profile = sys.getprofile()
        sys.settrace(None)
        sys.setprofile(None)
        try:
            if self._main_thread:
                self._old_sigint_handler = signal.getsignal(signal.SIGINT)
                signal.signal(signal.SIGINT, self._defer_sigint)
        except BaseException:
            sys.setprofile(self._old_profile)
            sys.settrace(self._old_trace)
            raise
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> bool:
        old_handler = self._old_sigint_handler
        try:
            if self._main_thread:
                signal.signal(signal.SIGINT, old_handler)
            # Snapshot only after handback: a SIGINT arriving just before the
            # restore still reaches _defer_sigint and must be replayed.
            pending = self._pending_sigint
        finally:
            sys.setprofile(self._old_profile)
            sys.settrace(self._old_trace)
        if pending is not None and old_handler != signal.SIG_IGN:
            signum, frame = pending
            if callable(old_handler):
                old_handler(signum, frame)
            else:
                signal.default_int_handler(signum, frame)
        return False


class ManagedLaunchGuard:
    """An owner-visible launch slot published before Popen can create a child."""

    def __init__(
        self,
        containment_factory: Any,
        fallback_containment_factory: Any,
        *,
        empty_containment: Any = None,
    ) -> None:
        self._condition = threading.Condition()
        self._containment_factory = containment_factory
        self._fallback_containment_factory = fallback_containment_factory
        self._empty_containment = empty_containment
        self._process: Any = None
        self._managed: ManagedChild | None = None
        self._launch_complete = False
        self._cancel_requested = False
        self._cancel_event = threading.Event()
        self._cleanup_complete = False

    @property
    def process(self) -> Any:
        with self._condition:
            if self._process is None:
                raise ProcessContainmentError("managed launch has no published process")
            return self._process

    def publish(self, process: Any) -> Any:
        """Publish the raw process and its cleanup boundary before returning it."""

        primary_failure: BaseException | None = None
        secondary_failure: BaseException | None = None
        with self._condition:
            if self._process is not None:
                raise ProcessContainmentError("managed launch process was published twice")
            self._process = process
            try:
                containment = self._containment_factory(process)
            except BaseException as exc:
                primary_failure = exc
                try:
                    containment = self._fallback_containment_factory(process)
                except BaseException as fallback_exc:
                    secondary_failure = fallback_exc
                else:
                    self._managed = ManagedChild(process, containment)
            else:
                self._managed = ManagedChild(process, containment)
            cancel_requested = self._cancel_requested or self._cancel_event.is_set()
            self._condition.notify_all()

        if secondary_failure is not None:
            raise ProcessContainmentError(
                "primary and fallback containment construction failed: "
                f"{type(primary_failure).__name__}: {primary_failure}; "
                f"{type(secondary_failure).__name__}: {secondary_failure}"
            ) from primary_failure
        if primary_failure is not None:
            raise primary_failure
        if cancel_requested:
            try:
                self.terminate_tree()
            except BaseException as exc:
                raise ProcessContainmentError(
                    "launch was cancelled after process publication and termination failed: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            raise ProcessContainmentError("launch was cancelled after process publication")
        return process

    def mark_launch_complete(self) -> None:
        with self._condition:
            self._launch_complete = True
            self._condition.notify_all()

    def set_empty_containment(self, containment: Any) -> None:
        """Bind a pre-process handle after the pending slot is already owned."""

        with self._condition:
            if self._empty_containment is not None:
                raise ProcessContainmentError("empty launch containment was bound twice")
            if self._process is not None:
                raise ProcessContainmentError(
                    "empty launch containment cannot be bound after process publication"
                )
            self._empty_containment = containment

    def resume_if_active(self, resumer: Any) -> None:
        """Serialize the final cancellation check with suspended-root resume."""

        with self._condition:
            if self._cancel_requested or self._cancel_event.is_set():
                raise ProcessContainmentError(
                    "launch cancellation was recorded before process resume"
                )
            if self._process is None or self._managed is None:
                raise ProcessContainmentError(
                    "process cannot resume before boundary publication"
                )
            resumer(self._process)

    def _managed_or_fallback(self) -> ManagedChild | None:
        with self._condition:
            if self._managed is not None:
                return self._managed
            if self._process is None:
                return None
            try:
                containment = self._fallback_containment_factory(self._process)
                self._managed = ManagedChild(self._process, containment)
            except BaseException as exc:
                raise ProcessContainmentError(
                    "could not construct the fallback boundary for a published process: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            return self._managed

    def terminate_tree(self) -> None:
        # Publish the cancellation request without waiting for a containment
        # factory that may currently hold the condition during atomic assignment.
        self._cancel_event.set()
        with self._condition:
            if self._cleanup_complete:
                return
            self._cancel_requested = True
        managed = self._managed_or_fallback()
        if managed is not None:
            managed.terminate_tree()

    def cleanup_tree(self, timeout_seconds: float = PROCESS_TREE_CLEANUP_SECONDS) -> None:
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        with self._condition:
            if self._cleanup_complete:
                return
            while self._process is None and not self._launch_complete:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProcessContainmentError(
                        "process launch did not publish a child or finish before cleanup timeout"
                    )
                self._condition.wait(timeout=remaining)
            process_present = self._process is not None
            empty_containment = self._empty_containment

        if process_present:
            managed = self._managed_or_fallback()
            if managed is None:  # pragma: no cover - protected by process_present
                raise ProcessContainmentError("published process has no cleanup boundary")
            managed.cleanup_tree(max(0.0, deadline - time.monotonic()))
        elif empty_containment is not None:
            try:
                empty_containment.close()
            except BaseException as exc:
                raise ProcessContainmentError(
                    "empty launch containment could not be closed: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
        with self._condition:
            self._cleanup_complete = True


def popen_and_publish(
    owner_guard: ManagedLaunchGuard,
    popen_factory: Any,
    argv: list[str],
    **kwargs: Any,
) -> Any:
    """Publish a Popen result before the launcher's outer call can return."""

    process = popen_factory(argv, **kwargs)
    return owner_guard.publish(process)


def cleanup_managed_with_retry(managed: Any, attempts: int = 2) -> None:
    """Retry transient cleanup failures when no batch supervisor owns the child."""

    if attempts < 1:
        raise ValueError("cleanup attempts must be at least one")
    failures: list[BaseException] = []
    for _ in range(attempts):
        try:
            managed.cleanup_tree(PROCESS_TREE_CLEANUP_SECONDS)
        except BaseException as exc:
            failures.append(exc)
        else:
            return
    detail = "; ".join(
        f"attempt {index}: {type(failure).__name__}: {failure}"
        for index, failure in enumerate(failures, start=1)
    )
    raise ProcessContainmentError(
        f"process-tree cleanup remained unproven after {attempts} attempt(s): {detail}"
    ) from failures[-1]


def raise_after_failed_managed_launch(
    *,
    phase: str,
    primary: BaseException,
    managed: Any,
    owner: Any,
    secondary_failures: tuple[BaseException, ...] = (),
) -> None:
    """Clean a failed launch without ever hiding unproven containment."""

    cleanup_failure: BaseException | None = None
    try:
        cleanup_managed_with_retry(managed)
    except BaseException as exc:
        # The owner deliberately retains this boundary. Its caller/supervisor
        # can retry even when the primary failure was KeyboardInterrupt or
        # SystemExit.
        cleanup_failure = exc
    else:
        owner.discard(managed)

    if cleanup_failure is not None:
        detail = (
            f"{phase} failed: {type(primary).__name__}: {primary}; "
            "launch cleanup remained unproven: "
            f"{type(cleanup_failure).__name__}: {cleanup_failure}"
        )
        if secondary_failures:
            secondary_detail = "; ".join(
                f"{type(failure).__name__}: {failure}"
                for failure in secondary_failures
            )
            detail += f"; secondary launch failures: {secondary_detail}"
        raise ProcessContainmentError(detail) from primary

    if isinstance(primary, (KeyboardInterrupt, SystemExit)):
        raise primary
    if isinstance(primary, ProcessContainmentError) and not secondary_failures:
        raise primary
    detail = f"{phase} failed: {type(primary).__name__}: {primary}"
    if secondary_failures:
        secondary_detail = "; ".join(
            f"{type(failure).__name__}: {failure}" for failure in secondary_failures
        )
        detail += f"; secondary launch failures: {secondary_detail}"
    raise ProcessContainmentError(detail) from primary


def launch_managed(
    argv: list[str],
    *,
    owner: Any,
    platform_name: str | None = None,
    popen_factory: Any = None,
    windows_job_factory: Any = None,
    windows_resume_factory: Any = None,
    posix_group_factory: Any = None,
    **kwargs: Any,
) -> ManagedLaunchGuard:
    """Launch a child that is registry-owned before it can execute or return."""

    effective_platform = os.name if platform_name is None else platform_name
    popen_factory = subprocess.Popen if popen_factory is None else popen_factory
    controls, _ = child_process_isolation(effective_platform)
    job: Any = None
    guard: ManagedLaunchGuard | None = None
    job_factory: Any = None
    group_factory: Any = None
    if effective_platform == "nt":
        job_factory = WindowsJob if windows_job_factory is None else windows_job_factory
        if job_factory is None:
            raise ProcessContainmentError("Windows Job support is unavailable")
    else:
        group_factory = PosixProcessGroup if posix_group_factory is None else posix_group_factory

    registered = False
    phase = "launch boundary construction"
    try:
        with DeferredLaunchInterrupts():
            if effective_platform == "nt":
                def assign_to_windows_job(process: Any) -> Any:
                    if job is None:  # pragma: no cover - publication follows Job binding
                        raise ProcessContainmentError("Windows Job was not bound before Popen")
                    job.assign(process)
                    return job

                guard = ManagedLaunchGuard(
                    assign_to_windows_job,
                    lambda _process: job,
                )
            else:
                guard = ManagedLaunchGuard(
                    lambda process: group_factory(process.pid),
                    lambda process: PosixProcessGroup(process.pid),
                )

            try:
                # The slot exists in the owner before Popen can create a process. A
                # caller, worker, or supervisor can therefore always find the launch,
                # including during CALL -> STORE_FAST interruption windows.
                phase = "pre-launch ownership registration"
                owner.add(guard)
                registered = True
                if effective_platform == "nt":
                    phase = "Windows Job construction and owner binding"
                    job = job_factory()
                    guard.set_empty_containment(job)
                phase = (
                    "Windows Job assignment and owner publication"
                    if effective_platform == "nt"
                    else "POSIX process-group creation and owner publication"
                )
                process = popen_and_publish(
                    guard,
                    popen_factory,
                    argv,
                    **kwargs,
                    **controls,
                )
                if effective_platform == "nt":
                    phase = "Windows primary-thread resume"
                    resumer = (
                        resume_suspended_windows_process
                        if windows_resume_factory is None
                        else windows_resume_factory
                    )
                    if resumer is None:
                        raise ProcessContainmentError(
                            "suspended-process resume support is unavailable"
                        )
                    guard.resume_if_active(resumer)
            finally:
                # Complete the pending slot before restoring trace/profile/SIGINT
                # hooks; a replayed interrupt can then close even an empty Job.
                guard.mark_launch_complete()
        return guard
    except BaseException as exc:
        if guard is None:
            if job is not None:
                try:
                    job.close()
                except BaseException as close_exc:
                    raise ProcessContainmentError(
                        f"{phase} failed and empty Job cleanup also failed: "
                        f"{type(close_exc).__name__}: {close_exc}"
                    ) from exc
            if isinstance(exc, (KeyboardInterrupt, SystemExit, ProcessContainmentError)):
                raise
            raise ProcessContainmentError(
                f"{phase} failed: {type(exc).__name__}: {exc}"
            ) from exc
        guard.mark_launch_complete()
        secondary_failures: list[BaseException] = []
        if not registered:
            try:
                owner.add(guard)
                registered = True
            except BaseException as owner_exc:
                # ActiveProcessRegistry.add publishes before reporting a
                # cancellation race. A second attempt also closes an interrupt
                # that landed before its first atomic set insertion.
                secondary_failures.append(owner_exc)
        raise_after_failed_managed_launch(
            phase=phase,
            primary=exc,
            managed=guard,
            owner=owner,
            secondary_failures=tuple(secondary_failures),
        )


def run_managed_capture(
    argv: list[str],
    *,
    cwd: Path | None = None,
    input_text: str | None = None,
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    """Run a captured foreground command with the same process-tree guarantees as a trial."""

    owner = ActiveProcessRegistry()
    managed: Any = None
    try:
        managed = launch_managed(
            argv,
            owner=owner,
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        process = managed.process
        try:
            stdout, stderr = process.communicate(input_text, timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            managed.terminate_tree()
            try:
                process.communicate(timeout=PROCESS_TREE_CLEANUP_SECONDS)
            except subprocess.TimeoutExpired as exc:
                raise ProcessContainmentError(
                    "captured process root did not reap after tree termination"
                ) from exc
            raise
        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    finally:
        cleanup_registry_with_retry(owner)


class ActiveProcessRegistry:
    """Atomically tracks managed children and closes cancellation launch races."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._children: set[Any] = set()
        self._cancelling = False

    def add(self, child: Any) -> None:
        with self._lock:
            # Register before observing cancellation. If the immediate
            # termination request fails, the worker and then the supervisor
            # can still retry this exact boundary; it can never disappear
            # while cleanup remains unproven.
            self._children.add(child)
            terminate_now = self._cancelling
        if terminate_now:
            try:
                child.terminate_tree()
            except BaseException as exc:
                raise ProcessContainmentError(
                    "batch cancellation raced a child launch and its immediate "
                    f"termination request failed: {type(exc).__name__}: {exc}"
                ) from exc
            raise ProcessContainmentError("batch cancellation raced a child launch")

    def discard(self, child: Any) -> None:
        with self._lock:
            self._children.discard(child)

    def terminate_all(self) -> int:
        with self._lock:
            self._cancelling = True
            children = tuple(self._children)
        terminated = 0
        failures: list[BaseException] = []
        for child in children:
            try:
                child.terminate_tree()
                terminated += 1
            except BaseException as exc:
                failures.append(exc)
        if failures:
            detail = "; ".join(
                f"{type(failure).__name__}: {failure}" for failure in failures
            )
            raise ProcessContainmentError(
                f"failed to terminate {len(failures)} active process tree(s): {detail}"
            ) from failures[0]
        return terminated

    def cleanup_all(self) -> int:
        """Retry owned cleanup after workers stop; retain every unproven child."""

        with self._lock:
            children = tuple(self._children)
        cleaned = 0
        failures: list[BaseException] = []
        for child in children:
            try:
                child.cleanup_tree()
            except BaseException as exc:
                failures.append(exc)
            else:
                self.discard(child)
                cleaned += 1
        if failures:
            detail = "; ".join(
                f"{type(failure).__name__}: {failure}" for failure in failures
            )
            raise ProcessContainmentError(
                f"failed to finish cleanup for {len(failures)} active process tree(s): {detail}"
            ) from failures[0]
        return cleaned


def cleanup_registry_with_retry(
    owner: ActiveProcessRegistry,
    attempts: int = 2,
) -> None:
    """Retry registry-owned cleanup, including cleanup-time interrupts."""

    if attempts < 1:
        raise ValueError("registry cleanup attempts must be at least one")
    failures: list[BaseException] = []
    for _ in range(attempts):
        try:
            owner.cleanup_all()
        except BaseException as exc:
            failures.append(exc)
        else:
            return
    detail = "; ".join(
        f"attempt {index}: {type(failure).__name__}: {failure}"
        for index, failure in enumerate(failures, start=1)
    )
    raise ProcessContainmentError(
        f"registry-owned cleanup remained unproven after {attempts} attempt(s): {detail}"
    ) from failures[-1]


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def write_json_exclusive(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def write_json_exclusive_durable(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    fsync_parent_directory(path)


def write_text_exclusive(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(value)


def write_text_exclusive_durable(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    fsync_parent_directory(path)


def fsync_parent_directory(path: Path, platform_name: str | None = None) -> None:
    """Persist a newly created directory entry on POSIX; Windows uses file `_commit`."""

    effective_platform = os.name if platform_name is None else platform_name
    if effective_platform == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_fd = os.open(path.parent, flags)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def write_bytes_exclusive(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(value)


def fsync_existing_file(path: Path) -> None:
    """Flush a child-created file before sealing a record that hashes it."""

    with path.open("r+b") as handle:
        handle.flush()
        os.fsync(handle.fileno())
    fsync_parent_directory(path)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def canonical_json_value(value: Any) -> str:
    """Serialize a JSON value so comparisons preserve every JSON scalar type."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def extract_request_seed_options(help_text: str) -> list[str]:
    """Return request-seed flags explicitly advertised by ``codex exec --help``."""
    return sorted(
        set(re.findall(r"(?<![\w-])--(?:request-)?seed(?![\w-])", help_text))
    )


def require_request_seed_unsupported(identity: dict[str, Any]) -> None:
    if (
        not isinstance(identity, dict)
        or "request_seed_options" not in identity
        or not isinstance(identity["request_seed_options"], list)
        or any(not isinstance(option, str) or not option for option in identity["request_seed_options"])
    ):
        raise ValueError("Codex executable identity has malformed request-seed capability data")
    options = identity["request_seed_options"]
    if options:
        rendered = ", ".join(str(option) for option in options)
        raise ValueError(
            "Codex exec now exposes request-seed option(s) "
            f"{rendered}; update the operator to apply every allocated model seed "
            "before running this evaluation"
        )


def require_executable_hash(command_path: str, expected_sha256: str, label: str) -> None:
    if not isinstance(expected_sha256, str) or not SHA256_RE.fullmatch(expected_sha256):
        raise ValueError(f"{label} expected hash is malformed")
    executable = require_regular_file(Path(command_path), label)
    actual_sha256 = sha256_file(executable)
    if actual_sha256 != expected_sha256:
        raise ValueError(
            f"{label} hash changed: expected {expected_sha256}, observed {actual_sha256}"
        )


def executable_identity(command: str) -> dict[str, Any]:
    resolved = shutil.which(command)
    if resolved is None:
        raise ValueError(f"Executable not found: {command}")
    executable = Path(resolved).resolve(strict=True)
    binary_sha256 = sha256_file(executable)
    completed = run_managed_capture(
        [str(executable), "--version"],
        timeout_seconds=30,
    )
    if completed.returncode != 0:
        raise ValueError(f"Unable to obtain Codex version: {completed.stderr.strip()}")
    require_executable_hash(str(executable), binary_sha256, "Codex executable after version probe")
    version_output = (completed.stdout + "\n" + completed.stderr).strip()
    if not version_output:
        raise ValueError("Unable to obtain Codex version: command returned no version text")
    help_completed = run_managed_capture(
        [str(executable), "exec", "--help"],
        timeout_seconds=30,
    )
    if help_completed.returncode != 0:
        raise ValueError(f"Unable to obtain Codex exec help: {help_completed.stderr.strip()}")
    require_executable_hash(str(executable), binary_sha256, "Codex executable after help probe")
    help_bytes = (
        help_completed.stdout.encode("utf-8")
        + b"\x00"
        + help_completed.stderr.encode("utf-8")
    )
    help_text = help_completed.stdout + "\n" + help_completed.stderr
    isolation = prompt_isolation_probe(str(executable))
    require_executable_hash(
        str(executable),
        binary_sha256,
        "Codex executable after prompt-isolation probe",
    )
    return {
        "requested_command": command,
        "resolved_path": str(executable),
        "binary_sha256": binary_sha256,
        "version_output": version_output,
        "exec_help_sha256": sha256_bytes(help_bytes),
        "request_seed_options": extract_request_seed_options(help_text),
        "prompt_isolation": isolation,
    }


def prompt_isolation_probe(codex_path: str) -> dict[str, Any]:
    argv = [codex_path, "debug", "prompt-input", "--enable", "skip_host_skill_discovery"]
    for override in SKILL_CONFIG_OVERRIDES:
        argv.extend(["-c", override])
    for feature in DISABLED_FEATURES:
        argv.extend(["--disable", feature])
    argv.append(PROMPT_ISOLATION_MARKER)
    completed = run_managed_capture(
        argv,
        timeout_seconds=60,
    )
    if completed.returncode != 0:
        raise ValueError(
            "Unable to verify model-visible host-skill isolation: "
            f"{completed.stderr.strip()}"
        )
    try:
        messages = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("Codex prompt-isolation probe returned invalid JSON") from exc
    if not isinstance(messages, list) or not messages:
        raise ValueError("Codex prompt-isolation probe returned no prompt messages")
    canonical = canonical_json_value(messages)
    lowered = canonical.casefold()
    if PROMPT_ISOLATION_MARKER.casefold() not in lowered:
        raise ValueError("Codex prompt-isolation probe omitted its marker")
    found = [marker for marker in PROMPT_ISOLATION_FORBIDDEN_MARKERS if marker in lowered]
    if found:
        raise ValueError(
            "Codex model-visible prompt still exposes host skill instructions: "
            + ", ".join(found)
        )
    return {
        "schema_version": "1.0",
        "combined_output_sha256": sha256_bytes(
            completed.stdout.encode("utf-8")
            + b"\x00"
            + completed.stderr.encode("utf-8")
        ),
        "message_count": len(messages),
        "skills_include_instructions": False,
        "bundled_skills_enabled": False,
        "forbidden_markers_found": [],
    }


def model_catalog_entry(codex_path: str, model: str) -> tuple[dict[str, Any], str]:
    completed = run_managed_capture(
        [codex_path, "debug", "models"],
        timeout_seconds=60,
    )
    if completed.returncode != 0:
        raise ValueError(f"Unable to read Codex model catalog: {completed.stderr.strip()}")
    catalog = json.loads(completed.stdout)
    matching = [item for item in catalog.get("models", []) if item.get("slug") == model]
    if len(matching) != 1:
        raise ValueError(f"Model {model!r} was not found exactly once in the catalog")
    item = matching[0]
    selected = {
        "slug": item.get("slug"),
        "display_name": item.get("display_name"),
        "description": item.get("description"),
        "default_reasoning_level": item.get("default_reasoning_level"),
        "supported_reasoning_levels": [entry.get("effort") for entry in item.get("supported_reasoning_levels", [])],
        "context_window": item.get("context_window"),
        "max_context_window": item.get("max_context_window"),
        "tool_mode": item.get("tool_mode"),
    }
    return selected, sha256_bytes(completed.stdout.encode("utf-8"))


def git_snapshot(repo_root: Path) -> dict[str, Any]:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, text=True, encoding="utf-8",
        capture_output=True, check=False, timeout=30,
    )
    status = subprocess.run(
        ["git", "status", "--short"], cwd=repo_root, text=True, encoding="utf-8",
        capture_output=True, check=False, timeout=30,
    )
    if head.returncode != 0 or status.returncode != 0:
        raise ValueError("Unable to record repository state")
    return {"head": head.stdout.strip(), "status_short": status.stdout.splitlines()}


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def is_link_or_reparse(path: Path) -> bool:
    """Identify symlinks, junctions, and other Windows reparse points."""
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction) and is_junction():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def require_real_directory(path: Path, label: str) -> Path:
    if is_link_or_reparse(path) or not path.is_dir():
        raise ValueError(f"{label} must be a real directory, not a link, junction, or reparse point")
    return path.resolve(strict=True)


def require_regular_file(path: Path, label: str) -> Path:
    if is_link_or_reparse(path):
        raise ValueError(f"{label} must not be a link, junction, or reparse point")
    try:
        mode = path.stat().st_mode
        resolved = path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError(f"{label} is missing") from exc
    if not stat.S_ISREG(mode):
        raise ValueError(f"{label} must be a regular file")
    return resolved


def require_trial_directory(run_dir: Path, trial_id: Any) -> Path:
    if not isinstance(trial_id, str) or not TRIAL_ID_RE.fullmatch(trial_id):
        raise ValueError("Allocation contains an unsafe trial_id")
    run_root = require_real_directory(run_dir, "Run directory")
    dispatch_root = require_real_directory(run_root / "dispatch", "Dispatch directory")
    candidate = dispatch_root / trial_id
    trial_root = require_real_directory(candidate, f"{trial_id} trial directory")
    if trial_root.parent != dispatch_root:
        raise ValueError(f"{trial_id}: trial directory must be a direct child of dispatch")
    return trial_root


def child_file_state(root: Path, relative: Path, *, required: bool) -> bool:
    """Validate one fixed relative file path without traversing linked parents."""
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Unsafe staged path: {relative.as_posix()}")
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        if is_link_or_reparse(current):
            raise ValueError(f"Staged parent is a link, junction, or reparse point: {relative.as_posix()}")
        if current.exists() and not current.is_dir():
            raise ValueError(f"Staged parent is not a directory: {relative.as_posix()}")
    candidate = root / relative
    if is_link_or_reparse(candidate):
        raise ValueError(f"Staged file is a link or reparse point: {relative.as_posix()}")
    if not candidate.exists():
        if required:
            raise ValueError(f"{root.name}: missing core file {relative.as_posix()}")
        return False
    if not candidate.is_file():
        raise ValueError(f"Staged path is not a regular file: {relative.as_posix()}")
    resolved = candidate.resolve(strict=True)
    if not is_within(resolved, root) or resolved == root:
        raise ValueError(f"Staged file escapes its trial directory: {relative.as_posix()}")
    return True


def require_fresh_trial_outputs(trial_dir: Path) -> None:
    names = (
        "execution.json",
        "execution.started.json",
        "prompt.sent.txt",
        "response.raw.txt",
        "response.json",
        "codex.stdout.jsonl",
        "codex.stderr.txt",
    )
    if any((trial_dir / name).exists() or is_link_or_reparse(trial_dir / name) for name in names):
        raise ValueError(f"{trial_dir.name}: output path already exists; refusing to overwrite")


def validate_allocation(allocation: Any) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(allocation, dict):
        raise ValueError("Allocation must be one JSON object")
    trials = allocation.get("trials")
    order = allocation.get("dispatch_order")
    if not isinstance(trials, list) or not all(isinstance(trial, dict) for trial in trials):
        raise ValueError("Allocation trials must be a list of objects")
    trial_ids: list[str] = []
    core_paths = {path.as_posix() for path in CORE_FILES}
    skill_paths = core_paths | {path.as_posix() for path in TREATMENT_FILES}
    for trial in trials:
        trial_id = trial.get("trial_id")
        if not isinstance(trial_id, str) or not TRIAL_ID_RE.fullmatch(trial_id):
            raise ValueError("Allocation contains an unsafe trial_id")
        trial_ids.append(trial_id)
        if (
            not isinstance(trial.get("case_id"), str)
            or not isinstance(trial.get("pair_id"), str)
            or isinstance(trial.get("replicate"), bool)
            or not isinstance(trial.get("replicate"), int)
            or trial["replicate"] < 1
            or isinstance(trial.get("model_seed"), bool)
            or not isinstance(trial.get("model_seed"), int)
            or trial.get("condition") not in {"baseline", "skill"}
        ):
            raise ValueError(f"{trial_id}: allocation identity fields are malformed")
        hashes = trial.get("trial_file_hashes")
        if not isinstance(hashes, dict) or not all(
            isinstance(path, str) and isinstance(digest, str) and SHA256_RE.fullmatch(digest)
            for path, digest in hashes.items()
        ):
            raise ValueError(f"{trial_id}: allocation file hashes are malformed")
        expected_paths = core_paths if trial["condition"] == "baseline" else skill_paths
        if set(hashes) != expected_paths:
            raise ValueError(f"{trial_id}: allocation file paths do not match its condition")
    if len(trial_ids) != len(set(trial_ids)):
        raise ValueError("Allocation trial IDs must be unique")
    if not isinstance(order, list) or order != list(dict.fromkeys(order)) or set(order) != set(trial_ids):
        raise ValueError("Allocation dispatch order must be an exact trial permutation")
    return trials, order


def staged_files(trial_dir: Path) -> list[Path]:
    trial_dir = require_real_directory(trial_dir, f"{trial_dir.name} trial directory")
    for relative in CORE_FILES:
        child_file_state(trial_dir, relative, required=True)
    treatment_present = [
        relative for relative in TREATMENT_FILES
        if child_file_state(trial_dir, relative, required=False)
    ]
    if treatment_present and len(treatment_present) != len(TREATMENT_FILES):
        missing_treatment = [relative.as_posix() for relative in TREATMENT_FILES if not (trial_dir / relative).is_file()]
        raise ValueError(f"{trial_dir.name}: partial skill treatment; missing {missing_treatment}")
    return list(CORE_FILES) + treatment_present


def stage_trial(source_dir: Path, isolated_dir: Path) -> dict[str, str]:
    """Copy only the trial's explicitly allowed files into an empty directory."""
    if isolated_dir.exists() and any(isolated_dir.iterdir()):
        raise ValueError(f"Isolated staging directory is not empty: {isolated_dir}")
    isolated_dir.mkdir(parents=True, exist_ok=True)
    allowed = staged_files(source_dir)
    for relative in allowed:
        target = isolated_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_dir / relative, target)
    return {
        path.relative_to(isolated_dir).as_posix(): sha256_file(path)
        for path in sorted(isolated_dir.rglob("*"))
        if path.is_file()
    }


def require_allocated_hashes(
    trial_id: str,
    expected: Any,
    actual: dict[str, str],
) -> None:
    """Require an exact path-and-content match to the allocator's trial manifest."""
    if not isinstance(expected, dict) or not all(
        isinstance(path, str) and isinstance(digest, str)
        for path, digest in expected.items()
    ):
        raise ValueError(f"{trial_id}: allocation has an invalid trial_file_hashes map")
    if expected == actual:
        return
    expected_paths = set(expected)
    actual_paths = set(actual)
    missing = sorted(expected_paths - actual_paths)
    unexpected = sorted(actual_paths - expected_paths)
    changed = sorted(
        path for path in expected_paths & actual_paths if expected[path] != actual[path]
    )
    raise ValueError(
        f"{trial_id}: isolated staged file hashes do not exactly match allocation "
        f"(missing={missing}, unexpected={unexpected}, changed={changed})"
    )


def build_prompt(trial_dir: Path) -> tuple[str, dict[str, str]]:
    sections: list[str] = [
        "The following are the complete allowed files for this isolated evaluation trial. "
        "Use only their contents. No other files, tools, network sources, or prior context are available."
    ]
    hashes: dict[str, str] = {}
    for relative in staged_files(trial_dir):
        path = trial_dir / relative
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        label = relative.as_posix()
        hashes[label] = sha256_bytes(raw)
        sections.append(f"\n<allowed-file path={json.dumps(label)}>\n{text}\n</allowed-file>")
    sections.append(
        "\nComplete the task now. Return only the final JSON object required by response.schema.json. "
        "Do not mention this wrapper or the evaluation condition."
    )
    return "\n".join(sections), hashes


def codex_argv(
    codex_path: str,
    trial_dir: Path,
    response_path: Path,
    model: str,
    reasoning: str,
) -> list[str]:
    argv = [
        codex_path,
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        "--strict-config",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--model",
        model,
        "-c",
        f'model_reasoning_effort="{reasoning}"',
        "-c",
        'model_verbosity="low"',
        "-c",
        SKILL_CONFIG_OVERRIDES[0],
        "-c",
        SKILL_CONFIG_OVERRIDES[1],
        "-c",
        'shell_environment_policy.inherit="none"',
        "-c",
        "suppress_unstable_features_warning=true",
        "--enable",
        "skip_host_skill_discovery",
    ]
    for feature in DISABLED_FEATURES:
        argv.extend(["--disable", feature])
    argv.extend(
        [
            "--cd",
            str(trial_dir),
            "--output-schema",
            str(trial_dir / "response.schema.json"),
            "--output-last-message",
            str(response_path),
            "--json",
            "--color",
            "never",
            "-",
        ]
    )
    return argv


def execute_trial(
    trial: dict[str, Any],
    run_dir: Path,
    codex_path: str,
    expected_codex_sha256: str,
    model: str,
    reasoning: str,
    timeout_seconds: int,
    cancellation: threading.Event | None = None,
    active_processes: ActiveProcessRegistry | None = None,
) -> dict[str, Any]:
    cancellation = threading.Event() if cancellation is None else cancellation
    owns_active_processes = active_processes is None
    active_processes = ActiveProcessRegistry() if active_processes is None else active_processes
    if cancellation.is_set():
        raise RuntimeError("batch cancelled before trial staging")
    trial_id = trial.get("trial_id")
    trial_dir = require_trial_directory(run_dir, trial_id)
    final_record = trial_dir / "execution.json"
    started_record = trial_dir / "execution.started.json"
    prompt_path = trial_dir / "prompt.sent.txt"
    response_path = trial_dir / "response.raw.txt"
    stdout_path = trial_dir / "codex.stdout.jsonl"
    stderr_path = trial_dir / "codex.stderr.txt"
    require_fresh_trial_outputs(trial_dir)

    # The model process never runs in the dispatch tree. The temporary working
    # directory contains only the explicitly allowed inputs, and is destroyed
    # after this single trial. Allocation, condition metadata, logs, and raw
    # output remain outside it.
    with tempfile.TemporaryDirectory(prefix=f"csr-eval-{trial_id}-") as temporary:
        isolated_dir = Path(temporary).resolve()
        allowed_hashes = stage_trial(trial_dir, isolated_dir)
        require_allocated_hashes(
            trial_id,
            trial.get("trial_file_hashes"),
            allowed_hashes,
        )
        prompt, prompt_hashes = build_prompt(isolated_dir)
        if prompt_hashes != allowed_hashes:  # pragma: no cover - defensive TOCTOU check
            raise ValueError(f"{trial_id}: isolated files changed while building prompt")

        # Re-resolve immediately before the first persistent write so a
        # replaced dispatch or trial directory cannot redirect output.
        if require_trial_directory(run_dir, trial_id) != trial_dir:
            raise ValueError(f"{trial_id}: trial directory changed during staging")
        require_fresh_trial_outputs(trial_dir)
        require_executable_hash(
            codex_path,
            expected_codex_sha256,
            f"Codex executable before {trial_id} launch",
        )
        if cancellation.is_set():
            raise RuntimeError("batch cancelled before trial launch")
        argv = codex_argv(codex_path, isolated_dir, response_path, model, reasoning)
        write_text_exclusive_durable(prompt_path, prompt)
        started = {
            "schema_version": "1.0",
            "operator_version": OPERATOR_VERSION,
            "trial_id": trial_id,
            "case_id": trial["case_id"],
            "pair_id": trial["pair_id"],
            "replicate": trial["replicate"],
            "condition": trial["condition"],
            "allocated_model_seed": trial.get("model_seed"),
            "model_seed_applied": False,
            "model_seed_note": MODEL_SEED_NOTE,
            "started_at": utc_now(),
            "prompt_sha256": sha256_bytes(prompt.encode("utf-8")),
            "allowed_file_hashes": allowed_hashes,
            "argv": argv,
        }
        write_json_exclusive_durable(started_record, started)
        start_clock = time.monotonic()
        timed_out = False
        return_code: int | None = None
        launch_error: str | None = None
        with stdout_path.open("xb") as stdout_handle, stderr_path.open("xb") as stderr_handle:
            managed: Any = None
            try:
                managed = launch_managed(
                    argv,
                    owner=active_processes,
                    cwd=isolated_dir,
                    stdin=subprocess.PIPE,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    env=os.environ.copy(),
                )
                process = managed.process
                if cancellation.is_set():
                    raise ProcessContainmentError("batch cancellation raced a child launch")
                require_executable_hash(
                    codex_path,
                    expected_codex_sha256,
                    f"Codex executable after {trial_id} launch",
                )
                try:
                    process.communicate(prompt.encode("utf-8"), timeout=timeout_seconds)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    managed.terminate_tree()
                    try:
                        process.communicate(timeout=PROCESS_TREE_CLEANUP_SECONDS)
                    except subprocess.TimeoutExpired as exc:
                        raise ProcessContainmentError(
                            f"{trial_id}: model root did not reap after tree termination"
                        ) from exc
                return_code = process.returncode
            except OSError as exc:
                launch_error = f"{type(exc).__name__}: {exc}"
            finally:
                if owns_active_processes:
                    # This scope starts before launch, so it also owns a child if
                    # an async exception lands after CALL returns but before the
                    # local `managed` assignment executes.
                    cleanup_registry_with_retry(active_processes)
                elif managed is not None:
                    try:
                        managed.cleanup_tree()
                    except BaseException:
                        # Keep failed cleanup registered so the abort supervisor
                        # can retry after every worker has stopped.
                        raise
                    else:
                        active_processes.discard(managed)
            stdout_handle.flush()
            os.fsync(stdout_handle.fileno())
            stderr_handle.flush()
            os.fsync(stderr_handle.fileno())
        fsync_parent_directory(stdout_path)
        if managed is not None:
            require_executable_hash(
                codex_path,
                expected_codex_sha256,
                f"Codex executable after {trial_id} completion",
            )
    duration = time.monotonic() - start_clock
    response_present = child_file_state(trial_dir, Path("response.raw.txt"), required=False)
    if response_present:
        fsync_existing_file(response_path)
    record = {
        **started,
        "finished_at": utc_now(),
        "duration_seconds": round(duration, 3),
        "return_code": return_code,
        "timed_out": timed_out,
        "launch_error": launch_error,
        "response_present": response_present,
        "response_sha256": sha256_file(response_path) if response_present else None,
        "stdout_sha256": sha256_file(stdout_path),
        "stderr_sha256": sha256_file(stderr_path),
    }
    write_json_exclusive_durable(final_record, record)
    return {
        "trial_id": trial_id,
        "return_code": return_code,
        "timed_out": timed_out,
        "response_present": response_present,
        "duration_seconds": record["duration_seconds"],
    }


def run_batch(args: argparse.Namespace) -> dict[str, Any]:
    intended_run_dir = args.run_dir.resolve()
    if is_within(intended_run_dir, REPO_ROOT.resolve()):
        raise ValueError("Raw run directory must be outside the repository")
    if is_link_or_reparse(args.run_dir):
        raise ValueError("Raw run directory must not be a link, junction, or reparse point")
    run_dir = require_real_directory(args.run_dir, "Raw run directory")
    allocation_path = run_dir / "allocation.private.json"
    if not child_file_state(run_dir, Path("allocation.private.json"), required=False):
        raise ValueError(f"Missing allocation: {allocation_path}")
    allocation = load_json(allocation_path)
    trials, dispatch_order = validate_allocation(allocation)
    if trials:
        for trial in trials:
            trial_dir = require_trial_directory(run_dir, trial["trial_id"])
            _, actual_hashes = build_prompt(trial_dir)
            require_allocated_hashes(trial["trial_id"], trial["trial_file_hashes"], actual_hashes)
            require_fresh_trial_outputs(trial_dir)

    identity = executable_identity(args.codex)
    require_request_seed_unsupported(identity)
    catalog_entry, catalog_hash = model_catalog_entry(identity["resolved_path"], args.model)
    require_executable_hash(
        identity["resolved_path"],
        identity["binary_sha256"],
        "Codex executable after model-catalog probe",
    )
    if args.reasoning not in catalog_entry["supported_reasoning_levels"]:
        raise ValueError(f"Reasoning effort {args.reasoning!r} is unsupported for {args.model}")
    repository = git_snapshot(REPO_ROOT)
    if repository["head"] != args.expected_commit:
        raise ValueError(f"Repository HEAD {repository['head']} != expected {args.expected_commit}")
    if repository["status_short"]:
        raise ValueError(
            "Repository worktree is dirty; commit or remove every tracked and untracked "
            f"change before evaluation: {repository['status_short']}"
        )
    if allocation.get("seed") != args.expected_allocation_seed:
        raise ValueError(
            f"Allocation seed {allocation.get('seed')} != expected {args.expected_allocation_seed}"
        )

    config_path = run_dir / "operator-config.json"
    summary_path = run_dir / "operator-summary.json"
    abort_path = run_dir / "operator-abort.json"
    abort_cleanup_path = run_dir / "operator-abort-cleanup.json"
    if any(
        path.exists() or is_link_or_reparse(path)
        for path in (config_path, summary_path, abort_path, abort_cleanup_path)
    ):
        raise ValueError(
            "Operator evidence already exists; this run is ineligible for resume. "
            f"Preserve it and use a fresh allocation path: {config_path}"
        )
    _, child_isolation_record = child_process_isolation()
    config = {
        "schema_version": "1.0",
        "operator_version": OPERATOR_VERSION,
        "operator_script": str(Path(__file__).resolve()),
        "operator_script_sha256": sha256_file(Path(__file__).resolve()),
        "created_at": utc_now(),
        "repository": repository,
        "expected_commit": args.expected_commit,
        "allocation_seed": allocation["seed"],
        "replicates": allocation["replicates"],
        "fixture_hashes": allocation["fixture_hashes"],
        "skill_resource_hashes": allocation["skill_resource_hashes"],
        "codex": identity,
        "model_catalog_entry": catalog_entry,
        "model_catalog_raw_sha256": catalog_hash,
        "model_catalog_selected_sha256": sha256_bytes(
            canonical_json_value(catalog_entry).encode("utf-8")
        ),
        "model": args.model,
        "reasoning_effort": args.reasoning,
        "model_verbosity": "low",
        "temperature": "unset; provider/CLI default",
        "top_p": "unset; provider/CLI default",
        "max_output_tokens": "unset; provider/CLI default",
        "request_seed": REQUEST_SEED_STATUS,
        "sandbox": "read-only",
        "network_search": False,
        "ephemeral": True,
        "ignore_user_config": True,
        "ignore_rules": True,
        "skip_host_skill_discovery": True,
        "skills_include_instructions": False,
        "bundled_skills_enabled": False,
        "disabled_features": list(DISABLED_FEATURES),
        "jobs": args.jobs,
        "timeout_seconds": args.timeout_seconds,
        "batch_heartbeat_seconds": BATCH_HEARTBEAT_SECONDS,
        "child_process_isolation": child_isolation_record,
        "bounded_submission": True,
        "max_in_flight": args.jobs,
        "foreground_supervision_required": True,
        "python": sys.version,
        "platform": platform.platform(),
        "os_name": os.name,
        "trial_count": len(trials),
        "dispatch_order": dispatch_order,
    }
    write_json_exclusive_durable(config_path, config)
    emit_json_line(
        {
            "event": "batch_started",
            "jobs": args.jobs,
            "trial_count": len(trials),
        }
    )

    trial_by_id = {trial["trial_id"]: trial for trial in trials}
    ordered_trials = [trial_by_id[trial_id] for trial_id in dispatch_order]
    results: list[dict[str, Any]] = []
    cancellation = threading.Event()
    active_processes = ActiveProcessRegistry()
    executor: concurrent.futures.ThreadPoolExecutor | None = None
    remaining_trials = iter(ordered_trials)
    futures: dict[concurrent.futures.Future[dict[str, Any]], str] = {}

    def submit_until_full() -> None:
        if executor is None:  # pragma: no cover - protected by the enclosing try
            raise RuntimeError("trial executor is unavailable")
        while len(futures) < args.jobs and not cancellation.is_set():
            try:
                trial = next(remaining_trials)
            except StopIteration:
                return
            future = executor.submit(
                execute_trial,
                trial,
                run_dir,
                identity["resolved_path"],
                identity["binary_sha256"],
                args.model,
                args.reasoning,
                args.timeout_seconds,
                cancellation,
                active_processes,
            )
            futures[future] = trial["trial_id"]

    try:
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs)
        submit_until_full()
        completed_count = 0
        while futures:
            done, _ = concurrent.futures.wait(
                set(futures),
                timeout=BATCH_HEARTBEAT_SECONDS,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            if not done:
                emit_json_line(
                    {
                        "completed": completed_count,
                        "event": "batch_heartbeat",
                        "pending": len(futures),
                        "trial_count": len(ordered_trials),
                    }
                )
                continue
            for future in done:
                trial_id = futures.pop(future)
                try:
                    result = future.result()
                except ProcessContainmentError:
                    raise
                except Exception as exc:  # preserve the batch and do not retry
                    result = {
                        "trial_id": trial_id,
                        "operator_error": f"{type(exc).__name__}: {exc}",
                        "return_code": None,
                        "timed_out": False,
                        "response_present": False,
                    }
                results.append(result)
                completed_count += 1
                emit_json_line(
                    {
                        "event": "trial_completed",
                        "progress": f"{completed_count}/{len(ordered_trials)}",
                        **result,
                    }
                )
            submit_until_full()
        executor.shutdown(wait=True, cancel_futures=True)
        # Assert that every launch slot was discarded after proven cleanup. This
        # also drains an owner-visible child if a worker exception landed after
        # launch returned but before its local assignment executed.
        cleanup_registry_with_retry(active_processes)
    except BaseException as exc:
        cancellation.set()
        record_batch_abort(args, exc)
        cleanup_failures: list[BaseException] = []
        try:
            active_processes.terminate_all()
        except Exception as cleanup_exc:
            cleanup_failures.append(cleanup_exc)
            record_batch_cleanup_failure(args, exc, cleanup_failures)
        for future in futures:
            future.cancel()
        if executor is not None:
            try:
                executor.shutdown(wait=True, cancel_futures=True)
            except BaseException as cleanup_exc:
                cleanup_failures.append(cleanup_exc)
        try:
            active_processes.cleanup_all()
        except Exception as cleanup_exc:
            cleanup_failures.append(cleanup_exc)
        if cleanup_failures:
            record_batch_cleanup_failure(args, exc, cleanup_failures)
            detail = "; ".join(
                f"{type(failure).__name__}: {failure}" for failure in cleanup_failures
            )
            raise ProcessContainmentError(
                f"batch abort encountered containment cleanup failures: {detail}"
            ) from exc
        raise
    require_executable_hash(
        identity["resolved_path"],
        identity["binary_sha256"],
        "Codex executable after batch",
    )
    summary = {
        "schema_version": "1.0",
        "finished_at": utc_now(),
        "trial_count": len(results),
        "response_count": sum(bool(item.get("response_present")) for item in results),
        "zero_exit_count": sum(item.get("return_code") == 0 for item in results),
        "timeout_count": sum(bool(item.get("timed_out")) for item in results),
        "operator_error_count": sum("operator_error" in item for item in results),
        "results": sorted(results, key=lambda item: item["trial_id"]),
    }
    write_json_exclusive_durable(summary_path, summary)
    return summary


def preflight(args: argparse.Namespace) -> dict[str, Any]:
    identity = executable_identity(args.codex)
    require_request_seed_unsupported(identity)
    catalog_entry, catalog_hash = model_catalog_entry(identity["resolved_path"], args.model)
    require_executable_hash(
        identity["resolved_path"],
        identity["binary_sha256"],
        "Codex executable after model-catalog probe",
    )
    if args.reasoning not in catalog_entry["supported_reasoning_levels"]:
        raise ValueError(f"Reasoning effort {args.reasoning!r} is unsupported for {args.model}")
    return {
        "ok": True,
        "codex": identity,
        "model_catalog_entry": catalog_entry,
        "model_catalog_raw_sha256": catalog_hash,
        "model_catalog_selected_sha256": sha256_bytes(
            canonical_json_value(catalog_entry).encode("utf-8")
        ),
        "reasoning_effort": args.reasoning,
        "disabled_features": list(DISABLED_FEATURES),
        "note": (
            "The captured Codex exec help exposes no --seed or --request-seed option; "
            "allocated model seeds cannot be applied."
        ),
    }


def smoke_test(args: argparse.Namespace) -> dict[str, Any]:
    if is_link_or_reparse(args.out_dir):
        raise ValueError("Smoke output directory must not be a link, junction, or reparse point")
    if args.out_dir.exists() and not args.out_dir.is_dir():
        raise ValueError(f"Smoke output path is not a directory: {args.out_dir}")
    if args.out_dir.exists() and any(args.out_dir.iterdir()):
        raise ValueError(f"Refusing to overwrite non-empty smoke directory: {args.out_dir}")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_dir = require_real_directory(args.out_dir, "Smoke output directory")
    if is_within(out_dir, REPO_ROOT.resolve()):
        raise ValueError("Smoke output directory must be outside the repository")
    identity = executable_identity(args.codex)
    catalog_entry, catalog_hash = model_catalog_entry(identity["resolved_path"], args.model)
    require_executable_hash(
        identity["resolved_path"],
        identity["binary_sha256"],
        "Codex executable after model-catalog probe",
    )
    if args.reasoning not in catalog_entry["supported_reasoning_levels"]:
        raise ValueError(f"Reasoning effort {args.reasoning!r} is unsupported for {args.model}")
    response_schema = require_regular_file(RESPONSE_SCHEMA_PATH, "Frozen evaluation response schema")
    write_bytes_exclusive(out_dir / "response.schema.json", response_schema.read_bytes())
    prompt = (
        "Return exactly this JSON object and nothing else: "
        + json.dumps(SMOKE_EXPECTED_RESPONSE, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    write_text_exclusive(out_dir / "prompt.sent.txt", prompt)
    response_path = out_dir / "response.raw.txt"
    argv = codex_argv(identity["resolved_path"], out_dir, response_path, args.model, args.reasoning)
    started = time.monotonic()
    completed = run_managed_capture(
        argv,
        cwd=out_dir,
        input_text=prompt,
        timeout_seconds=args.timeout_seconds,
    )
    require_executable_hash(
        identity["resolved_path"],
        identity["binary_sha256"],
        "Codex executable after smoke call",
    )
    write_text_exclusive(out_dir / "codex.stdout.jsonl", completed.stdout)
    write_text_exclusive(out_dir / "codex.stderr.txt", completed.stderr)
    response_present = child_file_state(out_dir, Path("response.raw.txt"), required=False)
    response_matches_expected = False
    response_validation_error: str | None = None
    if response_present:
        try:
            response_value = json.loads(response_path.read_text(encoding="utf-8"))
            response_matches_expected = canonical_json_value(response_value) == canonical_json_value(
                SMOKE_EXPECTED_RESPONSE
            )
            if not response_matches_expected:
                response_validation_error = "response did not equal the requested smoke object"
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            response_validation_error = f"response was not one UTF-8 JSON value: {type(exc).__name__}"
    record = {
        "schema_version": "1.0",
        "finished_at": utc_now(),
        "duration_seconds": round(time.monotonic() - started, 3),
        "codex": identity,
        "model_catalog_entry": catalog_entry,
        "model_catalog_raw_sha256": catalog_hash,
        "model_catalog_selected_sha256": sha256_bytes(
            canonical_json_value(catalog_entry).encode("utf-8")
        ),
        "model": args.model,
        "reasoning_effort": args.reasoning,
        "argv": argv,
        "return_code": completed.returncode,
        "response_present": response_present,
        "response_matches_expected": response_matches_expected,
        "response_validation_error": response_validation_error,
        "response_sha256": sha256_file(response_path) if response_present else None,
        "stdout_sha256": sha256_file(out_dir / "codex.stdout.jsonl"),
        "stderr_sha256": sha256_file(out_dir / "codex.stderr.txt"),
    }
    write_json_exclusive(out_dir / "smoke.json", record)
    if completed.returncode != 0 or not response_present or not response_matches_expected:
        raise ValueError(f"Codex smoke failed; inspect {out_dir}")
    return record


def record_batch_abort(args: argparse.Namespace, exc: BaseException) -> str | None:
    """Best-effort exclusive failure record after a batch config has been sealed."""

    if getattr(args, "command", None) != "run" or not hasattr(args, "run_dir"):
        return None
    try:
        if is_link_or_reparse(args.run_dir):
            return None
        run_dir = require_real_directory(args.run_dir, "Raw run directory")
        config_path = run_dir / "operator-config.json"
        summary_path = run_dir / "operator-summary.json"
        abort_path = run_dir / "operator-abort.json"
        if not child_file_state(run_dir, Path("operator-config.json"), required=False):
            return None
        if summary_path.exists() or abort_path.exists() or is_link_or_reparse(abort_path):
            return None
        write_json_exclusive_durable(
            abort_path,
            {
                "schema_version": "1.0",
                "operator_version": OPERATOR_VERSION,
                "aborted_at": utc_now(),
                "exception_type": type(exc).__name__,
                "exception_message": str(exc),
                "operator_config_sha256": sha256_file(config_path),
                "disposition": "ineligible; do not resume, repair, or replace trials",
            },
        )
        return str(abort_path)
    except Exception:
        return None


def record_batch_cleanup_failure(
    args: argparse.Namespace,
    original_exc: BaseException,
    cleanup_failures: list[BaseException],
) -> str | None:
    """Best-effort secondary evidence when abort cleanup itself was not provable."""

    if getattr(args, "command", None) != "run" or not hasattr(args, "run_dir"):
        return None
    try:
        if is_link_or_reparse(args.run_dir):
            return None
        run_dir = require_real_directory(args.run_dir, "Raw run directory")
        config_path = run_dir / "operator-config.json"
        abort_path = run_dir / "operator-abort.json"
        cleanup_path = run_dir / "operator-abort-cleanup.json"
        if not child_file_state(run_dir, Path("operator-config.json"), required=False):
            return None
        if cleanup_path.exists() or is_link_or_reparse(cleanup_path):
            return None
        payload = {
            "schema_version": "1.0",
            "operator_version": OPERATOR_VERSION,
            "recorded_at": utc_now(),
            "original_exception_type": type(original_exc).__name__,
            "original_exception_message": str(original_exc),
            "cleanup_failures": [
                {
                    "exception_type": type(failure).__name__,
                    "exception_message": str(failure),
                }
                for failure in cleanup_failures
            ],
            "operator_config_sha256": sha256_file(config_path),
            "operator_abort_sha256": (
                sha256_file(abort_path)
                if child_file_state(run_dir, Path("operator-abort.json"), required=False)
                else None
            ),
            "disposition": "containment cleanup unproven; ineligible; do not resume",
        }
        write_json_exclusive_durable(cleanup_path, payload)
        return str(cleanup_path)
    except Exception:
        return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("preflight", help="Record CLI identity and confirm model/reasoning support")
    check.add_argument("--codex", default="codex")
    check.add_argument("--model", required=True)
    check.add_argument("--reasoning", required=True)

    smoke = subparsers.add_parser("smoke", help="Make one non-evaluation call with the exact isolated CLI flags")
    smoke.add_argument("--out-dir", type=Path, required=True)
    smoke.add_argument("--codex", default="codex")
    smoke.add_argument("--model", required=True)
    smoke.add_argument("--reasoning", required=True)
    smoke.add_argument("--timeout-seconds", type=int, default=120)

    run = subparsers.add_parser("run", help="Execute every allocated trial exactly once")
    run.add_argument("--run-dir", type=Path, required=True)
    run.add_argument("--codex", default="codex")
    run.add_argument("--model", required=True)
    run.add_argument("--reasoning", required=True)
    run.add_argument("--jobs", type=int, default=2)
    run.add_argument("--timeout-seconds", type=int, default=300)
    run.add_argument("--expected-commit", required=True)
    run.add_argument("--expected-allocation-seed", type=int, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "preflight":
            result = preflight(args)
        elif args.command == "smoke":
            result = smoke_test(args)
        elif args.command == "run":
            if args.jobs < 1:
                raise ValueError("jobs must be at least one")
            if args.timeout_seconds < 1:
                raise ValueError("timeout-seconds must be at least one")
            result = run_batch(args)
        else:  # pragma: no cover
            raise ValueError(f"Unknown command: {args.command}")
        try:
            print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
        except (BrokenPipeError, OSError, ValueError):
            pass
        return 0
    except KeyboardInterrupt as exc:
        abort_path = record_batch_abort(args, exc)
        emit_json_line(
            {"error": "KeyboardInterrupt", "operator_abort": abort_path},
            stream=sys.stderr,
        )
        return 130
    except Exception as exc:
        abort_path = record_batch_abort(args, exc)
        emit_json_line(
            {
                "error": f"{type(exc).__name__}: {exc}",
                "operator_abort": abort_path,
            },
            stream=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
