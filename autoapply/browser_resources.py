"""Local process evidence for the dedicated browser; never kill by process name.

Only a fresh, unguessable launch marker plus the exact profile establishes a
browser root. Descendants are captured while that root/our driver is alive.
PID reuse is checked again through an OS handle before termination.
"""
import ctypes
import json
import os
from pathlib import Path
import signal
import shlex
import subprocess
import time
import uuid

from .runtime import ProcessLock


class BrowserOwnershipError(RuntimeError):
    pass


def command_arguments(command):
    if os.name != 'nt':
        return shlex.split(command)
    from ctypes import wintypes
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    count = ctypes.c_int()
    args = shell.CommandLineToArgvW(command, ctypes.byref(count))
    if not args:
        raise BrowserOwnershipError('Cannot inspect process arguments')
    try:
        return [args[i] for i in range(count.value)]
    finally:
        kernel = ctypes.WinDLL('kernel32')
        kernel.LocalFree.argtypes = [wintypes.HLOCAL]
        kernel.LocalFree(args)


DEFAULT_INVENTORY_TIMEOUT_SECONDS = 30


def process_inventory(timeout_seconds=DEFAULT_INVENTORY_TIMEOUT_SECONDS):
    if (type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 120):
        raise ValueError('Process inventory budget must be greater than zero and at most 120 seconds')
    if os.name == 'nt':
        script = """$ErrorActionPreference='Stop';
[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;
$OutputEncoding=[System.Text.Encoding]::UTF8;
Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CommandLine,
@{n='Birth';e={$_.CreationDate.ToFileTimeUtc().ToString()}} | ConvertTo-Json -Compress
"""
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                                capture_output=True, text=True, encoding='utf-8', errors='replace',
                                timeout=timeout_seconds,
                                creationflags=subprocess.CREATE_NO_WINDOW, check=True)
        if result.stdout is None or not result.stdout.strip():
            raise BrowserOwnershipError('Process inventory returned no output; ownership cannot be verified')
        rows = json.loads(result.stdout)
        if isinstance(rows, dict):
            rows = [rows]
        return {int(r['ProcessId']): dict(pid=int(r['ProcessId']), parent=int(r['ParentProcessId']),
                birth=r['Birth'], command=r['CommandLine'] or '') for r in rows}
    if not Path('/proc').is_dir():
        raise BrowserOwnershipError('Process ownership inspection unavailable on this platform')
    rows = {}
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            command = shlex.join((entry / 'cmdline').read_bytes().decode(errors='replace').rstrip('\0').split('\0'))
            rows[int(entry.name)] = dict(pid=int(entry.name), parent=int(stat[1]), birth=stat[19], command=command)
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    return rows


def check_profile_lock(path):
    """Windows leaves a lockfile behind; prove it has no open owner, don't delete it."""
    if os.name != 'nt':
        raise BrowserOwnershipError('Unproven profile lockfile ownership')
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(path), 0x80, 0, None, 3, 0, None)
    if handle == wintypes.HANDLE(-1).value:
        raise BrowserOwnershipError('Dedicated profile lockfile is held or inaccessible')
    kernel.CloseHandle(handle)


def bind_process_identity(record):
    """Bind CIM's microsecond timestamp to the exact kernel generation once."""
    if os.name != 'nt':
        return record
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, record['pid'])
    if not handle:
        if ctypes.get_last_error() == 87:
            return None  # Exited between inventory and binding.
        raise BrowserOwnershipError('Cannot bind owned process identity')
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            raise BrowserOwnershipError('Cannot read owned process creation time')
        birth = times[0].dwHighDateTime << 32 | times[0].dwLowDateTime
        # CIM discards the last 100-nanosecond digit. This check binds its record;
        # termination subsequently requires the exact saved native creation time.
        if birth // 10 != int(record['birth']) // 10:
            raise BrowserOwnershipError('Process identity changed during ownership capture')
        return dict(record, native_birth=str(birth))
    finally:
        kernel.CloseHandle(handle)


def terminate_owned(record):
    """Terminate the captured identity, never a recycled PID or its new children."""
    pid = record['pid']
    if os.name == 'nt':
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        handle = kernel.OpenProcess(0x1000 | 0x0001 | 0x100000, False, pid)
        if not handle:
            if ctypes.get_last_error() == 87:  # No such process.
                return
            raise BrowserOwnershipError('Cannot open owned process for cleanup')
        try:
            times = [wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                raise BrowserOwnershipError('Cannot verify owned process creation time')
            birth = str(times[0].dwHighDateTime << 32 | times[0].dwLowDateTime)
            if birth != record.get('native_birth'):
                return
            if kernel.WaitForSingleObject(handle, 0) == 0:
                return
            if not kernel.TerminateProcess(handle, 1):
                # Reaping its parent/child can concurrently complete this process.
                if kernel.WaitForSingleObject(handle, 3000) == 0:
                    return
                raise BrowserOwnershipError('Owned process termination failed')
            if kernel.WaitForSingleObject(handle, 3000) != 0:
                raise BrowserOwnershipError('Owned process did not exit within cleanup budget')
        finally:
            kernel.CloseHandle(handle)
    else:
        # pidfd prevents the check/kill PID reuse race on Linux.
        try:
            fd = os.pidfd_open(pid)
        except ProcessLookupError:
            return
        try:
            current = process_inventory().get(pid)
            if current and current['birth'] == record['birth']:
                signal.pidfd_send_signal(fd, signal.SIGKILL)
        finally:
            os.close(fd)


class DedicatedResources:
    def __init__(self, private):
        self.profile = private.resolve() / 'browser_profile'
        if (private / 'browser_profile').resolve() != self.profile:
            raise BrowserOwnershipError('Dedicated profile redirects outside its owned directory')
        self.lock = ProcessLock(private / 'browser-profile.lock')
        self.locked = False
        self.token = uuid.uuid4().hex
        self.marker = '--autoapply-session=' + self.token
        self.owned = {}
        self.diagnostics = {}
        self.driver_birth = None
        self.inventory_timeout_seconds = DEFAULT_INVENTORY_TIMEOUT_SECONDS

    def inventory(self):
        """One bounded observation; slow CIM work is never restarted by polling."""
        started = time.monotonic()
        progress = dict(state='still_working', budget_seconds=self.inventory_timeout_seconds)
        self.diagnostics['inventory'] = progress
        try:
            rows = (process_inventory() if self.inventory_timeout_seconds == DEFAULT_INVENTORY_TIMEOUT_SECONDS
                    else process_inventory(self.inventory_timeout_seconds))
        except subprocess.TimeoutExpired:
            progress['state'] = 'timed_out'
            raise
        except Exception:
            progress['state'] = 'definitively_failed'
            raise
        else:
            progress['state'] = 'completed'
            return rows
        finally:
            progress['elapsed_seconds'] = round(time.monotonic() - started, 3)

    def inspect(self):
        rows = self.inventory()
        profile = os.path.normcase(str(self.profile))
        # Matching the full profile is for conflict detection only, never kill authority.
        conflicts = [r['pid'] for r in rows.values()
                     if profile in os.path.normcase(r['command']) and r['pid'] not in self.owned]
        locks = [name for name in ('SingletonLock', 'SingletonCookie', 'SingletonSocket', 'lockfile')
                 if os.path.lexists(self.profile / name)]
        self.diagnostics.update(profile_locks=locks, conflicting_processes=conflicts,
                                owned_processes=len(self.owned), profile_accessible=False)
        if conflicts or any(name != 'lockfile' for name in locks):
            raise BrowserOwnershipError('Dedicated profile ownership/lock is ambiguous')
        if 'lockfile' in locks:
            check_profile_lock(self.profile / 'lockfile')
        self.profile.mkdir(parents=True, exist_ok=True)
        probe = self.profile / ('.autoapply-access-' + self.token)
        with probe.open('xb') as file:
            file.write(b'')
        probe.unlink()
        self.diagnostics['profile_accessible'] = True

    def acquire(self):
        if not self.locked:
            self.lock.__enter__()
            self.locked = True
        self.inspect()

    def capture(self, driver_pid=None):
        rows = self.inventory()
        roots = {r['pid'] for r in rows.values() if self.marker in r['command']
                 and self.marker in command_arguments(r['command'])
                 and any(os.path.normcase(arg) == os.path.normcase('--user-data-dir=' + str(self.profile))
                         for arg in command_arguments(r['command']))}
        # The PID comes directly from this instance's freshly spawned Playwright
        # transport, and must still be our child executing run-driver.
        driver = rows.get(driver_pid)
        if (driver and driver['parent'] == os.getpid() and 'run-driver' in command_arguments(driver['command'])
                and self.driver_birth in (None, driver['birth'])):
            roots.add(driver_pid)
            self.driver_birth = driver['birth']
        while True:
            children = {r['pid'] for r in rows.values() if r['parent'] in roots
                        and int(r['birth']) >= int(rows[r['parent']]['birth'])}
            if children <= roots:
                break
            roots |= children
        for pid in roots:
            record = bind_process_identity(rows[pid])
            if record:
                self.owned[pid] = record

    def reap(self):
        # Refresh ownership before shutdown, then use only those saved identities.
        rows = self.inventory()
        for pid, record in self.owned.items():
            if pid in rows and rows[pid]['birth'] == record['birth']:
                terminate_owned(record)
        deadline = time.monotonic() + 3
        rows = self.inventory()
        while any(pid in rows and rows[pid]['birth'] == r['birth'] for pid, r in self.owned.items()):
            if time.monotonic() >= deadline:
                break
            time.sleep(.1)
            rows = self.inventory()
        remaining = {pid: r for pid, r in self.owned.items()
                     if pid in rows and rows[pid]['birth'] == r['birth']}
        self.owned = remaining
        if remaining:
            raise BrowserOwnershipError('Dedicated process cleanup could not be confirmed')

    def release(self):
        if self.locked:
            self.lock.__exit__(None, None, None)
            self.locked = False
