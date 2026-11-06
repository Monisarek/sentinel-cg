"""Bounded child-process supervision; resource limits are not identity isolation."""

import math
import os
import signal
import subprocess
import sys
import threading
import time

from apps.control.sentinel.agents.contracts import ProviderError


MAX_INPUT_BYTES = 2_000_000
MAX_OUTPUT_BYTES = 2_000_000
PROCESS_MEMORY_BYTES = 1024 * 1024 * 1024
JOB_MEMORY_BYTES = 2 * PROCESS_MEMORY_BYTES
MAX_CHILD_PROCESSES = 8


class ResourceLimit(ProviderError):
    error_category = "RESOURCE_LIMIT"


class _WindowsJob:
    def __init__(self, cpu_seconds, process_memory_bytes, job_memory_bytes, max_processes):
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                ("flags", wintypes.DWORD), ("minimum_working_set", ctypes.c_size_t),
                ("maximum_working_set", ctypes.c_size_t), ("active_processes", wintypes.DWORD),
                ("affinity", ctypes.c_size_t), ("priority_class", wintypes.DWORD),
                ("scheduling_class", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in (
                "read_operations", "write_operations", "other_operations",
                "read_bytes", "write_bytes", "other_bytes",
            )]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("basic", BasicLimits), ("io", IoCounters),
                ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t),
            ]

        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.SetInformationJobObject.restype = wintypes.BOOL
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ResourceLimit("Windows process resource limits could not be created")
        limits = ExtendedLimits()
        # Active-process, aggregate CPU, per-process/job memory, and kill-on-close limits.
        limits.basic.flags = 0x8 | 0x4 | 0x100 | 0x200 | 0x2000
        limits.basic.active_processes = max_processes
        limits.basic.job_time = cpu_seconds * 10_000_000
        limits.process_memory = process_memory_bytes
        limits.job_memory = job_memory_bytes
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise ResourceLimit("Windows process resource limits could not be enforced")

    def attach_and_resume(self, process):
        import ctypes
        from ctypes import wintypes

        class ThreadEntry(ctypes.Structure):
            _fields_ = [
                ("size", wintypes.DWORD), ("usage", wintypes.DWORD),
                ("thread_id", wintypes.DWORD), ("process_id", wintypes.DWORD),
                ("base_priority", wintypes.LONG), ("delta_priority", wintypes.LONG),
                ("flags", wintypes.DWORD),
            ]

        if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ResourceLimit("Windows child process could not enter its resource-limited job")
        self.kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        self.kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        self.kernel.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
        self.kernel.Thread32First.restype = wintypes.BOOL
        self.kernel.Thread32Next.argtypes = self.kernel.Thread32First.argtypes
        self.kernel.Thread32Next.restype = wintypes.BOOL
        self.kernel.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenThread.restype = wintypes.HANDLE
        self.kernel.ResumeThread.argtypes = [wintypes.HANDLE]
        self.kernel.ResumeThread.restype = wintypes.DWORD
        snapshot = self.kernel.CreateToolhelp32Snapshot(0x4, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise ResourceLimit("Windows suspended child could not be resumed safely")
        try:
            entry = ThreadEntry()
            entry.size = ctypes.sizeof(entry)
            available = self.kernel.Thread32First(snapshot, ctypes.byref(entry))
            while available:
                if entry.process_id == process.pid:
                    thread = self.kernel.OpenThread(0x2, False, entry.thread_id)
                    if not thread:
                        break
                    try:
                        if self.kernel.ResumeThread(thread) != 0xFFFFFFFF:
                            return
                    finally:
                        self.kernel.CloseHandle(thread)
                    break
                available = self.kernel.Thread32Next(snapshot, ctypes.byref(entry))
        finally:
            self.kernel.CloseHandle(snapshot)
        raise ResourceLimit("Windows suspended child could not be resumed safely")

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


_POSIX_LIMITER = (
    "import os,resource,sys; "
    "memory=int(sys.argv[1]); cpu=int(sys.argv[2]); "
    "resource.setrlimit(resource.RLIMIT_AS,(memory,memory)); "
    "resource.setrlimit(resource.RLIMIT_CPU,(cpu,cpu)); "
    "os.execvpe(sys.argv[3],sys.argv[3:],os.environ)"
)


def bounded_run(command, *, input=None, cwd=None, env=None, timeout,
                check_cancel=None, max_output_bytes=MAX_OUTPUT_BYTES,
                max_input_bytes=MAX_INPUT_BYTES, process_memory_bytes=PROCESS_MEMORY_BYTES,
                job_memory_bytes=JOB_MEMORY_BYTES, max_processes=MAX_CHILD_PROCESSES,
                max_cpu_seconds=None,
                capture_output=True, text=True, encoding="utf-8", errors="strict",
                creationflags=0):
    """Capture bounded output and terminate the process tree on every exit path."""
    if (type(command) not in (list, tuple) or not command
            or any(type(argument) is not str for argument in command)):
        raise ValueError("child command must be an argument list")
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("child timeout must be finite and positive")
    cpu_seconds = math.ceil(timeout) if max_cpu_seconds is None else max_cpu_seconds
    if type(cpu_seconds) is not int or not 1 <= cpu_seconds <= math.ceil(timeout):
        raise ValueError("child CPU limit must be a positive integer within the wall-clock ceiling")
    for limit in (max_output_bytes, max_input_bytes, process_memory_bytes, job_memory_bytes, max_processes):
        if type(limit) is not int or limit <= 0:
            raise ValueError("child resource limits must be positive integers")
    if capture_output is not True or text is not True:
        raise ValueError("bounded child execution requires captured text output")
    payload = input.encode(encoding, errors) if input is not None else b""
    if len(payload) > max_input_bytes:
        raise ResourceLimit("Child input exceeds the resource limit")
    if check_cancel is not None:
        check_cancel()
    started = time.monotonic()
    job = process = None
    buffers, output_size, over_limit = [bytearray(), bytearray()], [0], threading.Event()
    output_lock = threading.Lock()
    readers, sender = [], None

    def read_output(stream, buffer):
        try:
            while chunk := stream.read1(65536):
                with output_lock:
                    output_size[0] += len(chunk)
                    if output_size[0] > max_output_bytes:
                        over_limit.set()
                        break
                    buffer.extend(chunk)
        finally:
            stream.close()

    def send_input():
        try:
            process.stdin.write(payload)
        except (BrokenPipeError, OSError):
            pass
        finally:
            try:
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass

    def terminate_tree():
        if job is not None:
            job.close()
        elif process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if process is not None and process.poll() is None:
            process.kill()

    try:
        if os.name == "nt":
            job = _WindowsJob(cpu_seconds, process_memory_bytes, job_memory_bytes, max_processes)
            flags = creationflags | subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP | 0x4
            options = {"creationflags": flags}
            arguments = command
        else:
            # The POSIX group cleans up descendants; it is not a hostile-code containment boundary.
            options = {"start_new_session": True}
            arguments = [sys.executable, "-B", "-c", _POSIX_LIMITER,
                         str(process_memory_bytes), str(cpu_seconds), *command]
        process = subprocess.Popen(arguments, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True, **options)
        for stream, buffer in zip((process.stdout, process.stderr), buffers):
            reader = threading.Thread(target=read_output, args=(stream, buffer), daemon=True)
            reader.start()
            readers.append(reader)
        if job is not None:
            job.attach_and_resume(process)
        sender = threading.Thread(target=send_input, daemon=True)
        sender.start()
        while True:
            if check_cancel is not None:
                check_cancel()
            if over_limit.is_set():
                raise ResourceLimit("Child output exceeds the resource limit")
            if time.monotonic() - started >= timeout:
                raise subprocess.TimeoutExpired(command, timeout)
            if process.poll() is not None:
                terminate_tree()
                for reader in readers:
                    reader.join(timeout=1)
                if over_limit.is_set():
                    raise ResourceLimit("Child output exceeds the resource limit")
                if any(reader.is_alive() for reader in readers):
                    raise ResourceLimit("Child output did not close within the cleanup deadline")
                return subprocess.CompletedProcess(command, process.returncode,
                    stdout=buffers[0].decode(encoding, errors), stderr=buffers[1].decode(encoding, errors))
            time.sleep(0.02)
    finally:
        terminate_tree()
        if process is not None:
            process.wait(timeout=5)
            if sender is not None:
                sender.join(timeout=1)
            if not process.stdin.closed:
                process.stdin.close()
            for reader in readers:
                reader.join(timeout=1)
