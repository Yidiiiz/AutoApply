"""Windows Chrome native host: one fixed, local dashboard-start capability.

No application configuration or applicant data is loaded by this module.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from autoapply.runtime import ProcessLock

HOST = "com.autoapply.dashboard"
DESCRIPTION = "AutoApply start-only dashboard launcher"
REGISTRY = rf"Software\Google\Chrome\NativeMessagingHosts\{HOST}"
MAX_MESSAGE = 1024
ORIGIN = re.compile(r"chrome-extension://[a-p]{32}/\Z")
EXTENSION_ID = re.compile(r"[a-p]{32}\Z")


def paths(root=ROOT):
    folder = root / "data/private/launcher"
    return folder / f"{HOST}.json", folder / "dashboard-host.cmd"


def wrapper(root=ROOT):
    # cmd expansion can reinterpret these even inside double quotes.
    if any(char in str(root) for char in '%!\r\n"'):
        raise ValueError("unsupported_install_path")
    return ('@echo off\r\n@chcp 65001 >nul\r\n"' + str(root / ".venv/Scripts/python.exe") + '" "' +
            str(root / "autoapply/dashboard_launcher.py") + '" %*\r\n')


def owned_manifest(root=ROOT):
    manifest, command = paths(root)
    value = json.loads(manifest.read_text(encoding="utf-8"))
    if (not isinstance(value, dict) or set(value) != {"name", "description", "path", "type", "allowed_origins"} or
            value["name"] != HOST or value["description"] != DESCRIPTION or
            value["type"] != "stdio" or value["path"] != str(command) or
            not isinstance(value["allowed_origins"], list) or not value["allowed_origins"] or
            any(not isinstance(v, str) or not ORIGIN.fullmatch(v) for v in value["allowed_origins"]) or
            command.read_bytes() != wrapper(root).encode("utf-8")):
        raise ValueError("foreign_installation")
    return value


def registration(registry):
    try:
        with registry.OpenKey(registry.HKEY_CURRENT_USER, REGISTRY) as key:
            value, kind = registry.QueryValueEx(key, None)
            if kind != registry.REG_SZ:
                raise ValueError("foreign_registration")
            return value
    except FileNotFoundError:
        return None


def install(extension_ids, root=ROOT, registry=None):
    if not extension_ids or any(not EXTENSION_ID.fullmatch(v) for v in extension_ids):
        raise ValueError("invalid_extension_id")
    content = wrapper(root)
    if not (root / ".venv/Scripts/python.exe").is_file():
        raise ValueError("venv_missing")
    if registry is None:
        import winreg as registry
    manifest, command = paths(root)
    registered = registration(registry)
    if registered is not None and registered != str(manifest):
        raise ValueError("foreign_registration")
    origins = set()
    if manifest.exists() or command.exists():
        origins.update(owned_manifest(root)["allowed_origins"])
    elif registered:
        raise ValueError("foreign_installation")
    origins.update(f"chrome-extension://{value}/" for value in extension_ids)
    value = dict(name=HOST, description=DESCRIPTION, path=str(command), type="stdio",
                 allowed_origins=sorted(origins))
    manifest.parent.mkdir(parents=True, exist_ok=True)
    command.write_bytes(content.encode("utf-8"))
    manifest.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    with registry.CreateKey(registry.HKEY_CURRENT_USER, REGISTRY) as key:
        registry.SetValueEx(key, None, 0, registry.REG_SZ, str(manifest))


def uninstall(root=ROOT, registry=None):
    if registry is None:
        import winreg as registry
    manifest, command = paths(root)
    registered = registration(registry)
    if registered is not None and registered != str(manifest):
        raise ValueError("foreign_registration")
    if manifest.exists() or command.exists():
        owned_manifest(root)
    elif registered:
        raise ValueError("foreign_installation")
    if registered:
        # Refuse a key containing anything except our default value.
        with registry.OpenKey(registry.HKEY_CURRENT_USER, REGISTRY) as key:
            subkeys, values, _ = registry.QueryInfoKey(key)
            if subkeys or values != 1:
                raise ValueError("foreign_registration")
        registry.DeleteKey(registry.HKEY_CURRENT_USER, REGISTRY)
    manifest.unlink(missing_ok=True)
    command.unlink(missing_ok=True)


def read_exact(stream, size):
    chunks = bytearray()
    while len(chunks) < size:
        part = stream.read(size - len(chunks))
        if not part:
            raise ValueError("invalid_message")
        chunks.extend(part)
    return bytes(chunks)


def read_message(stream):
    first = stream.read(1)
    if not first:
        return None
    size = struct.unpack("=I", first + read_exact(stream, 3))[0]
    if not 0 < size <= MAX_MESSAGE:
        raise ValueError("invalid_message")
    try:
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("invalid_message")
                result[key] = value
            return result
        value = json.loads(read_exact(stream, size).decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("invalid_message") from None
    if (not isinstance(value, dict) or set(value) != {"action", "port"} or
            value["action"] != "start_dashboard" or type(value["port"]) is not int or
            not 1024 <= value["port"] <= 65535):
        raise ValueError("invalid_message")
    return value


def process_birth(pid, kernel32=None):
    """Return creation ticks only for a live process, using query/sync rights.

    Creation time prevents a recycled PID from suppressing a new start. Never
    use os.kill(pid, 0): on Windows that can terminate the process.
    """
    import ctypes
    from ctypes import wintypes

    if kernel32 is None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x1000 | 0x00100000, False, pid)  # QUERY_LIMITED_INFORMATION | SYNCHRONIZE
    if not handle:
        return None
    try:
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not kernel32.GetProcessTimes(handle, *(ctypes.byref(v) for v in (created, exited, kernel, user))):
            return None
        if kernel32.WaitForSingleObject(handle, 0) != 258:  # WAIT_TIMEOUT: still running
            return None
        return (created.dwHighDateTime << 32) | created.dwLowDateTime
    finally:
        kernel32.CloseHandle(handle)


def pending_child(marker):
    try:
        with marker.open("rb") as source:
            encoded = source.read(MAX_MESSAGE + 1)
        if len(encoded) > MAX_MESSAGE:
            return False
        value = json.loads(encoded)
        if (not isinstance(value, dict) or set(value) != {"pid", "birth"} or
                type(value["pid"]) is not int or not 0 < value["pid"] <= 0xffffffff or
                type(value["birth"]) is not int or not 0 < value["birth"] <= 0xffffffffffffffff):
            return False
        return process_birth(value["pid"]) == value["birth"]
    except (OSError, ValueError, RecursionError):
        return False


def start_dashboard(port, root=ROOT):
    if type(port) is not int or not 1024 <= port <= 65535:
        return {"status": "error", "error": "invalid_request"}
    python = root / ".venv/Scripts/python.exe"
    if not python.is_file():
        return {"status": "error", "error": "venv_missing"}
    # The dashboard owns this existing lock before loading config or doing work.
    try:
        with ProcessLock(Path(tempfile.gettempdir()) / f"autoapply-dashboard-127.0.0.1-{port}.lock"):
            pass
    except RuntimeError:
        return {"status": "already-running"}
    except OSError:
        return {"status": "error", "error": "start_failed"}
    token = hashlib.sha256(str(root).encode()).hexdigest()[:16]
    marker = Path(tempfile.gettempdir()) / f"autoapply-launcher-{token}-{port}"
    try:
        with ProcessLock(marker.with_suffix(".lock")):
            if pending_child(marker):
                return {"status": "starting"}
            folder = paths(root)[0].parent
            folder.mkdir(parents=True, exist_ok=True)
            with (folder / "dashboard.log").open("ab") as output:
                child = subprocess.Popen([str(python), "-m", "autoapply", "dashboard", "--no-browser", "--port", str(port)],
                                 cwd=str(root), stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                 close_fds=True, creationflags=(subprocess.CREATE_NO_WINDOW |
                                     subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP |
                                     subprocess.CREATE_BREAKAWAY_FROM_JOB))
            birth = process_birth(child.pid)
            if birth:
                marker.write_text(json.dumps({"pid": child.pid, "birth": birth}), encoding="utf-8")
            else:
                marker.unlink(missing_ok=True)
            return {"status": "starting"}
    except RuntimeError:
        return {"status": "starting"}
    except (OSError, AttributeError):
        return {"status": "error", "error": "start_failed"}


def native_host(argv, source, target, root=ROOT):
    try:
        if (not 1 <= len(argv) <= 2 or not ORIGIN.fullmatch(argv[0]) or
                len(argv) == 2 and not re.fullmatch(r"--parent-window=\d{1,20}", argv[1]) or
                argv[0] not in owned_manifest(root)["allowed_origins"]):
            raise ValueError("origin_rejected")
        request = read_message(source)
        if request is None:
            return
        response = start_dashboard(request["port"], root)
    except (ValueError, OSError):
        response = {"status": "error", "error": "invalid_request"}
    encoded = json.dumps(response, separators=(",", ":")).encode("utf-8")
    target.write(struct.pack("=I", len(encoded)) + encoded)
    target.flush()


def main():
    if len(sys.argv) > 1 and sys.argv[1] in {"install", "uninstall"}:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("command", choices=["install", "uninstall"])
        parser.add_argument("--extension-id", action="append", default=[])
        args = parser.parse_args()
        try:
            if os.name != "nt":
                raise ValueError("windows_required")
            if args.command == "install":
                install(args.extension_id)
            else:
                uninstall()
        except (ValueError, OSError) as error:
            # Fixed codes only; no registry data, payloads or secrets.
            code = str(error) if isinstance(error, ValueError) else "installation_failed"
            parser.exit(1, code + "\n")
        print("Dashboard launcher " + args.command + " complete.")
    else:
        if os.name == "nt":
            import msvcrt
            msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
            msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
        native_host(sys.argv[1:], sys.stdin.buffer, sys.stdout.buffer)


if __name__ == "__main__":
    main()
