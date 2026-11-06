"""Offline process supervision checks using ordinary Python children, never a model."""

import ctypes
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from apps.control.sentinel.agents.process import ResourceLimit, bounded_run


def _alive(pid):
    if os.name == "nt":
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        status = Path(f"/proc/{pid}/stat")
        return not status.exists() or status.read_text().split()[2] != "Z"
    except ProcessLookupError:
        return False


def _supervisor(marker):
    code = "import os,pathlib,time,sys; pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(60)"
    bounded_run([sys.executable, "-B", "-c", code, marker], timeout=30)


class ProcessLimitTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def execute(self, code, *arguments, **options):
        return bounded_run([sys.executable, "-B", "-c", code, *map(str, arguments)],
                           cwd=self.root, env=os.environ.copy(), timeout=options.pop("timeout", 10), **options)

    def assert_dead(self, pid):
        deadline = time.monotonic() + 5
        while _alive(pid) and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertFalse(_alive(pid), "supervised descendant survived cleanup")

    def tree_code(self, parent_wait=60):
        return (
            "import pathlib,subprocess,sys,time; "
            "child=subprocess.Popen([sys.executable,'-B','-c','import time; time.sleep(60)']); "
            f"pathlib.Path(sys.argv[1]).write_text(str(child.pid)); time.sleep({parent_wait})"
        )

    def test_text_input_and_both_output_streams_round_trip(self):
        result = self.execute("import sys; print(sys.stdin.read()); print('diagnostic',file=sys.stderr)", input="recorded input")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "recorded input")
        self.assertEqual(result.stderr.strip(), "diagnostic")

    def test_combined_output_quota_terminates_unbounded_output(self):
        started = time.monotonic()
        with self.assertRaisesRegex(ResourceLimit, "output"):
            self.execute("import os; exec('while True: os.write(1,b\"x\"*1024); os.write(2,b\"y\"*1024)')",
                         max_output_bytes=1500)
        self.assertLess(time.monotonic() - started, 10)

    def test_input_quota_rejects_before_starting_a_child(self):
        with patch("apps.control.sentinel.agents.process.subprocess.Popen") as process:
            with self.assertRaisesRegex(ResourceLimit, "input"):
                self.execute("pass", input="x" * 129, max_input_bytes=128)
            process.assert_not_called()

    def test_timeout_terminates_child_and_grandchild(self):
        marker = self.root / "descendant.pid"
        with self.assertRaises(subprocess.TimeoutExpired):
            self.execute(self.tree_code(), marker, timeout=3)
        self.assertTrue(marker.is_file())
        self.assert_dead(int(marker.read_text()))

    def test_cancellation_terminates_child_and_grandchild(self):
        marker = self.root / "descendant.pid"

        class Cancelled(Exception):
            pass

        def check_cancel():
            if marker.exists():
                raise Cancelled("offline cancellation fixture")

        with self.assertRaises(Cancelled):
            self.execute(self.tree_code(), marker, check_cancel=check_cancel)
        self.assert_dead(int(marker.read_text()))

    def test_successful_parent_exit_still_cleans_up_leftover_descendants(self):
        marker = self.root / "descendant.pid"
        result = self.execute(self.tree_code(0.2), marker)
        self.assertEqual(result.returncode, 0)
        self.assert_dead(int(marker.read_text()))

    @unittest.skipUnless(os.name == "nt", "Windows Job Object acceptance")
    def test_windows_job_closes_children_when_supervisor_crashes(self):
        marker = self.root / "child.pid"
        supervisor = multiprocessing.get_context("spawn").Process(target=_supervisor, args=(str(marker),))
        supervisor.start()
        try:
            deadline = time.monotonic() + 10
            while not marker.is_file() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(marker.is_file())
            child = int(marker.read_text())
            self.assertTrue(_alive(child))
            supervisor.terminate()
            supervisor.join(timeout=10)
            self.assertIsNotNone(supervisor.exitcode)
            self.assert_dead(child)
        finally:
            if supervisor.is_alive():
                supervisor.terminate()
            supervisor.join(timeout=10)

    @unittest.skipUnless(os.name == "nt", "Windows Job Object acceptance")
    def test_windows_active_process_limit_prevents_forking(self):
        code = "import subprocess,sys\ntry: subprocess.Popen([sys.executable,'-c','pass'])\nexcept OSError: sys.exit(17)\nsys.exit(99)"
        # The Windows virtualenv launcher itself spawns the base interpreter.
        result = bounded_run([sys._base_executable, "-B", "-c", code], timeout=10, max_processes=1)
        self.assertEqual(result.returncode, 17)

    @unittest.skipUnless(os.name == "nt", "Windows Job Object acceptance")
    def test_windows_memory_limit_bounds_allocation(self):
        code = "import sys\ntry: bytearray(512*1024*1024)\nexcept MemoryError: sys.exit(19)\nsys.exit(99)"
        result = self.execute(code, process_memory_bytes=64 * 1024 * 1024, job_memory_bytes=128 * 1024 * 1024)
        self.assertEqual(result.returncode, 19)

    @unittest.skipUnless(os.name == "nt", "Windows Job Object acceptance")
    def test_job_attachment_failure_never_resumes_the_untrusted_child(self):
        marker = self.root / "executed.txt"
        with patch("apps.control.sentinel.agents.process._WindowsJob.attach_and_resume",
                   side_effect=ResourceLimit("offline job-attachment failure")):
            with self.assertRaises(ResourceLimit):
                self.execute("import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('executed')", marker)
        self.assertFalse(marker.exists())

    def test_invalid_resource_settings_fail_before_process_creation(self):
        for options in ({"timeout": 0}, {"timeout": float("nan")}, {"max_output_bytes": True}, {"max_processes": 0}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.execute("pass", **options)


if __name__ == "__main__":
    unittest.main()
