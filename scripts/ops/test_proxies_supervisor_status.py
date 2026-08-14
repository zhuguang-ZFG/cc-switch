from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

SUPERVISOR = Path.home() / ".omp" / "guardian" / "proxies-supervisor.py"
spec = importlib.util.spec_from_file_location("proxies_supervisor", SUPERVISOR)
assert spec and spec.loader
supervisor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supervisor)


class SupervisorStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        supervisor.GUARDIAN_DIR = Path(self.tempdir.name)
        supervisor.STATUS_FILE = supervisor.GUARDIAN_DIR / "supervisor-status.json"

    def test_status_records_structured_service_health(self) -> None:
        services = {
            "codebuddy": supervisor.service_status(
                healthy=False,
                restart_blocked=True,
                last_error="restart limit reached",
                restarts_last_hour=5,
            ),
            "agentrouter": supervisor.service_status(
                healthy=True,
                restart_blocked=False,
                last_error=None,
                restarts_last_hour=0,
            ),
        }
        supervisor.write_status(services, {"codebuddy": 5}, "2026-08-08")
        payload = json.loads(supervisor.STATUS_FILE.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["services"]["codebuddy"]["healthy"], False)
        self.assertEqual(payload["services"]["codebuddy"]["restartBlocked"], True)
        self.assertEqual(payload["services"]["codebuddy"]["lastError"], "restart limit reached")
        self.assertEqual(payload["services"]["codebuddy"]["restartsLastHour"], 5)
        self.assertEqual(payload["services"]["agentrouter"]["healthy"], True)

    def test_restart_count_does_not_consume_allowance(self) -> None:
        supervisor._restart_times["codebuddy"] = [supervisor.time.time() - 10]
        self.assertEqual(supervisor.restarts_last_hour("codebuddy"), 1)
        self.assertEqual(supervisor.restarts_last_hour("codebuddy"), 1)


class SupervisorRestartTargetingTests(unittest.TestCase):
    """kill_stale matches against a command line, so cmd must contain the match.

    Regression: relay entries used a bare script name (resolved via cwd), so
    `match` never hit the real command line and stale relays were never killed.
    """

    def test_every_match_pattern_hits_its_own_command_line(self) -> None:
        for name, info in supervisor.PROXIES.items():
            with self.subTest(proxy=name):
                command_line = " ".join(info["cmd"])
                self.assertRegex(command_line, info["match"])

    def test_match_patterns_do_not_cross_match_other_proxies(self) -> None:
        for name, info in supervisor.PROXIES.items():
            for other, other_info in supervisor.PROXIES.items():
                if other == name:
                    continue
                with self.subTest(pattern=name, command=other):
                    self.assertNotRegex(" ".join(other_info["cmd"]), info["match"])


@unittest.skipUnless(sys.platform == "win32", "Windows named mutex semantics")
class SupervisorSingleInstanceTests(unittest.TestCase):
    """Two sequential duplicate launches must BOTH lose to a live supervisor.

    Regression: CreateMutexW was called with bInitialOwner=False, so nobody
    owned the mutex. The first duplicate's WaitForSingleObject(0) silently took
    ownership and abandoned it on exit, letting the *second* duplicate hit the
    WAIT_ABANDONED takeover branch and run as a second owner alongside the
    live supervisor (2026-08-06 dual-owner incident).

    Each test uses a unique mutex name so it never collides with the live
    supervisor (which legitimately holds the production name).
    """

    def setUp(self) -> None:
        self.mutex_name = f"Local\\OMPProxiesSupervisorTest-{uuid.uuid4()}"

    def probe_source(self, tail: str) -> str:
        return (
            "import importlib.util;"
            f"spec = importlib.util.spec_from_file_location('s', r'{SUPERVISOR}');"
            "m = importlib.util.module_from_spec(spec);"
            "spec.loader.exec_module(m);"
            f"h = m.acquire_single_instance({self.mutex_name!r});" + tail
        )

    def probe(self) -> str:
        """Launch a short-lived duplicate; report whether it would have run."""
        result = subprocess.run(
            [sys.executable, "-c", self.probe_source(
                "print('ACQUIRED' if h else 'DUPLICATE')")],
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip().splitlines()[-1]

    @staticmethod
    def terminate(holder: subprocess.Popen) -> None:
        if holder.poll() is None:
            holder.kill()
        holder.wait(timeout=30)

    def spawn_holder(self) -> subprocess.Popen:
        holder = subprocess.Popen(
            [sys.executable, "-c", self.probe_source(
                "print('ACQUIRED' if h else 'DUPLICATE', flush=True);"
                "import time; time.sleep(120)")],
            stdout=subprocess.PIPE, text=True,
        )
        self.addCleanup(holder.stdout.close)
        self.addCleanup(self.terminate, holder)
        self.assertEqual(holder.stdout.readline().strip(), "ACQUIRED")
        return holder

    def test_holder_blocks_repeated_duplicate_launches(self) -> None:
        self.spawn_holder()
        self.assertEqual(self.probe(), "DUPLICATE")
        # Second probe is the regression: it must not inherit an abandoned
        # mutex from the first probe while the holder is still alive.
        self.assertEqual(self.probe(), "DUPLICATE")

    def test_abandoned_mutex_is_taken_over_after_holder_dies(self) -> None:
        """Hard-killed supervisor must not permanently block restarts."""
        holder = self.spawn_holder()
        self.assertEqual(self.probe(), "DUPLICATE")
        holder.kill()
        holder.wait(timeout=30)
        self.assertEqual(self.probe(), "ACQUIRED")


@unittest.skipUnless(sys.platform == "win32", "pythonw.exe is Windows-only")
class PythonwFaulthandlerRegressionTests(unittest.TestCase):
    """Module import must not die under pythonw.exe (console-less GUI entry).

    Regression: module-level `faulthandler.enable()` raises
    RuntimeError: sys.stderr is None when launched via pythonw.exe with no
    inherited std handles (exactly how HKCU Run starts it), so the documented
    "唯一持久入口" OMPProxiesSupervisor exited 1 before ever reaching the
    mutex / supervise loop. Fixed by writing the crash stack to a dedicated
    log file via a raw fd.

    The launch MUST pass no std handles at all: capture_output / DEVNULL give
    pythonw valid handles and mask the bug. CreateProcessW with
    bInheritHandles=False reproduces the Explorer/Run-key launch faithfully.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.pythonw = Path(sys.executable).with_name("pythonw.exe")
        if not cls.pythonw.exists():
            raise unittest.SkipTest("pythonw.exe not present")

    def test_import_exits_zero_without_console(self) -> None:
        import ctypes
        from ctypes import wintypes

        code = (
            "import importlib.util;"
            f"spec = importlib.util.spec_from_file_location('s', r'{SUPERVISOR}');"
            "m = importlib.util.module_from_spec(spec);"
            "spec.loader.exec_module(m)"
        )
        cmdline = f'"{self.pythonw}" -c "{code}"'

        class SI(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
                ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
                ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
                ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
                ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                ("lpReserved2", ctypes.POINTER(wintypes.BYTE)),
                ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
                ("hStdError", wintypes.HANDLE),
            ]

        class PI(ctypes.Structure):
            _fields_ = [
                ("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD),
            ]

        kernel32 = ctypes.windll.kernel32
        si, pi = SI(), PI()
        si.cb = ctypes.sizeof(SI)
        ok = kernel32.CreateProcessW(
            None, cmdline, None, None, False, 0x08000000,  # CREATE_NO_WINDOW
            None, None, ctypes.byref(si), ctypes.byref(pi),
        )
        if not ok:
            raise ctypes.WinError()
        try:
            kernel32.WaitForSingleObject(pi.hProcess, 120000)
            rc = wintypes.DWORD()
            kernel32.GetExitCodeProcess(pi.hProcess, ctypes.byref(rc))
        finally:
            kernel32.CloseHandle(pi.hThread)
            kernel32.CloseHandle(pi.hProcess)
        self.assertEqual(
            rc.value, 0,
            f"supervisor module died at import under pythonw (rc={rc.value})",
        )


if __name__ == "__main__":
    unittest.main()
