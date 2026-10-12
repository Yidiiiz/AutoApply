"""Owned process groups on POSIX; kill-on-close Job Objects on Windows."""
import ctypes
import os
import signal
import subprocess
import sys
from pathlib import Path

class Containment:
    def __init__(self, gate, cwd, token):
        self.job = None
        self.process = subprocess.Popen([sys.executable,str(gate),token],cwd=cwd,
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
            start_new_session=os.name!='nt',
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        try:
            if os.name=='nt':
                from ctypes import wintypes as w
                class BASIC(ctypes.Structure):
                    _fields_=[('per_process',ctypes.c_int64),('per_job',ctypes.c_int64),('flags',w.DWORD),
                        ('minimum',ctypes.c_size_t),('maximum',ctypes.c_size_t),('count',w.DWORD),
                        ('affinity',ctypes.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
                class IO(ctypes.Structure):
                    _fields_=[(n,ctypes.c_uint64) for n in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
                class EXTENDED(ctypes.Structure):
                    _fields_=[('basic',BASIC),('io',IO),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),
                              ('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
                self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
                self.kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,w.LPCWSTR]
                self.kernel.CreateJobObjectW.restype=w.HANDLE
                self.kernel.SetInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD]
                self.kernel.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE]
                self.kernel.TerminateJobObject.argtypes=[w.HANDLE,w.UINT]
                self.kernel.CloseHandle.argtypes=[w.HANDLE]
                self.job=self.kernel.CreateJobObjectW(None,None)
                info=EXTENDED(); info.basic.flags=0x2000 # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                if (not self.job or not self.kernel.SetInformationJobObject(self.job,9,ctypes.byref(info),ctypes.sizeof(info))
                        or not self.kernel.AssignProcessToJobObject(self.job,w.HANDLE(int(self.process._handle)))):
                    raise OSError('Cannot contain worker; launch refused')
        except BaseException:
            self.close()
            raise

    def start(self, payload):
        import json
        self.process.stdin.write((json.dumps(payload)+'\n').encode())
        self.process.stdin.flush()
        self.process.stdin.close()

    def close(self):
        if self.job:
            self.kernel.TerminateJobObject(self.job,1)
            self.kernel.CloseHandle(self.job)
            self.job=None
        elif os.name!='nt':
            try: os.killpg(self.process.pid,signal.SIGKILL)
            except ProcessLookupError: pass
        elif self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=10)


def identity(pid):
    """Creation identity only; no process-name kills or credential inspection."""
    if os.name=='nt':
        from ctypes import wintypes as w
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD]; kernel.OpenProcess.restype=w.HANDLE
        kernel.GetProcessTimes.argtypes=[w.HANDLE,*([ctypes.POINTER(w.FILETIME)]*4)]
        kernel.GetExitCodeProcess.argtypes=[w.HANDLE,ctypes.POINTER(w.DWORD)]
        kernel.CloseHandle.argtypes=[w.HANDLE]
        handle=kernel.OpenProcess(0x1000,False,pid)
        if not handle: return None
        try:
            times=[w.FILETIME() for _ in range(4)]; code=w.DWORD()
            if not kernel.GetExitCodeProcess(handle,ctypes.byref(code)) or code.value!=259: return None
            if not kernel.GetProcessTimes(handle,*[ctypes.byref(t) for t in times]): return None
            return str((times[0].dwHighDateTime<<32)|times[0].dwLowDateTime)
        finally: kernel.CloseHandle(handle)
    try: return Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[19]
    except (OSError,IndexError): return None
