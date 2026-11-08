"""Native IPC and installation use temporary files, fake registry and fake spawn only."""
import io
import json
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest

from autoapply import dashboard_launcher as launcher

A, B = 'a' * 32, 'b' * 32


class Key:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class Registry:
    HKEY_CURRENT_USER, REG_SZ = 'HKCU', 1

    def __init__(self):
        self.value = None
        self.extra = False

    def OpenKey(self, hive, path):
        assert (hive, path) == (self.HKEY_CURRENT_USER, launcher.REGISTRY)
        if self.value is None:
            raise FileNotFoundError
        return Key()

    def CreateKey(self, hive, path):
        assert (hive, path) == (self.HKEY_CURRENT_USER, launcher.REGISTRY)
        return Key()

    def QueryValueEx(self, key, name):
        assert name is None
        return self.value, self.REG_SZ

    def SetValueEx(self, key, name, reserved, kind, value):
        assert (name, reserved, kind) == (None, 0, self.REG_SZ)
        self.value = value

    def QueryInfoKey(self, key):
        return 0, 2 if self.extra else 1, 0

    def DeleteKey(self, hive, path):
        self.OpenKey(hive, path)
        self.value = None


@pytest.fixture
def installed(tmp_path, monkeypatch):
    root = tmp_path / 'installation with spaces'
    python = root / '.venv/Scripts/python.exe'
    python.parent.mkdir(parents=True)
    python.touch()
    registry = Registry()
    launcher.install([A], root, registry)
    monkeypatch.setattr(launcher.tempfile, 'gettempdir', lambda: str(tmp_path))
    return root, registry


def frame(value):
    body = json.dumps(value).encode()
    return struct.pack('=I', len(body)) + body


def host(root, payload, args=None):
    output = io.BytesIO()
    launcher.native_host(args or [f'chrome-extension://{A}/', '--parent-window=0'], io.BytesIO(payload), output, root)
    result = output.getvalue()
    if not result:
        return None
    length = struct.unpack('=I', result[:4])[0]
    assert length == len(result) - 4
    return json.loads(result[4:])


def test_install_preserves_ids_and_only_removes_owned_files(installed):
    root, registry = installed
    launcher.install([B, A], root, registry)
    launcher.install([A], root, registry)
    manifest, command = launcher.paths(root)
    assert launcher.owned_manifest(root)['allowed_origins'] == [f'chrome-extension://{id}/' for id in [A, B]]
    assert f'"{root / ".venv/Scripts/python.exe"}"' in command.read_text()
    assert ' %*' in command.read_text()
    unrelated = manifest.parent / 'keep.txt'
    unrelated.write_text('unrelated')
    launcher.uninstall(root, registry)
    launcher.uninstall(root, registry)
    assert registry.value is None and not manifest.exists() and not command.exists()
    assert unrelated.read_text() == 'unrelated'


@pytest.mark.parametrize('id', ['', 'a'*31, 'q'*32, 'A'*32, A+'/', '*', A+'\n'])
def test_invalid_ids_do_not_register(installed, id):
    root, registry = installed
    before = registry.value
    with pytest.raises(ValueError, match='invalid_extension_id'):
        launcher.install([id], root, registry)
    assert registry.value == before


def test_foreign_registration_and_modified_files_are_preserved(installed):
    root, registry = installed
    registry.value = 'foreign.json'
    for action in [lambda: launcher.install([A], root, registry), lambda: launcher.uninstall(root, registry)]:
        with pytest.raises(ValueError, match='foreign_registration'):
            action()
    assert registry.value == 'foreign.json'
    registry.value = str(launcher.paths(root)[0])
    command = launcher.paths(root)[1]
    command.write_text('foreign command')
    with pytest.raises(ValueError, match='foreign_installation'):
        launcher.uninstall(root, registry)
    assert command.read_text() == 'foreign command'


def test_uninstall_refuses_extra_registry_values(installed):
    root, registry = installed
    registry.extra = True
    with pytest.raises(ValueError, match='foreign_registration'):
        launcher.uninstall(root, registry)
    assert registry.value and launcher.paths(root)[0].exists()


@pytest.mark.parametrize('body', [b'', b'\x01', struct.pack('=I', 0), struct.pack('=I', 1025),
    struct.pack('=I', 8)+b'{}', struct.pack('=I', 1)+b'\xff', frame({}), frame([]),
    frame({'action':'start_dashboard', 'port':True}), frame({'action':'start_dashboard', 'port':80}),
    frame({'action':'start_dashboard', 'port':65536}), frame({'action':'start_dashboard', 'port':8765, 'url':'http://remote'}),
    frame({'action':'exec', 'port':8765}), frame({'action':'start_dashboard', 'port':'8765'}),
    struct.pack('=I', len(b'{"action":"start_dashboard","port":8765,"port":8766}'))+b'{"action":"start_dashboard","port":8765,"port":8766}'])
def test_bad_messages_never_spawn(installed, monkeypatch, body):
    root, _ = installed
    monkeypatch.setattr(launcher, 'start_dashboard', lambda *a: pytest.fail('unexpected spawn'))
    assert host(root, body) == (None if body == b'' else {'status':'error', 'error':'invalid_request'})


@pytest.mark.parametrize('args', [[f'chrome-extension://{B}/'], ['https://localhost/'],
    [f'chrome-extension://{A}/evil'], [f'chrome-extension://{A}/', '--port=1234'],
    [f'chrome-extension://{A}/', '--parent-window=0', 'extra'], []])
def test_origin_and_arguments_checked_before_payload(installed, monkeypatch, args):
    root, _ = installed
    monkeypatch.setattr(launcher, 'start_dashboard', lambda *a: pytest.fail('unexpected spawn'))
    output = io.BytesIO()
    launcher.native_host(args, io.BytesIO(frame({'action':'start_dashboard', 'port':8765})), output, root)
    assert json.loads(output.getvalue()[4:]) == {'status':'error', 'error':'invalid_request'}


def test_fixed_detached_spawn_and_duplicate_host_reuse(installed, monkeypatch):
    root, _ = installed
    calls = []
    for name, value in [('CREATE_NO_WINDOW', 1), ('DETACHED_PROCESS', 2), ('CREATE_NEW_PROCESS_GROUP', 4), ('CREATE_BREAKAWAY_FROM_JOB', 8)]:
        monkeypatch.setattr(launcher.subprocess, name, value, raising=False)
    def spawn(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(pid=4321)
    monkeypatch.setattr(launcher.subprocess, 'Popen', spawn)
    monkeypatch.setattr(launcher, 'process_birth', lambda pid: 123456789 if pid == 4321 else None)
    request = frame({'action':'start_dashboard', 'port':9876})
    assert host(root, request) == {'status':'starting'}
    assert host(root, request) == {'status':'starting'}
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] == [str(root / '.venv/Scripts/python.exe'), '-m', 'autoapply', 'dashboard', '--no-browser', '--port', '9876']
    assert kwargs['cwd'] == str(root) and kwargs['close_fds'] is True
    assert kwargs['creationflags'] == 15 and kwargs['stdin'] == launcher.subprocess.DEVNULL
    assert kwargs['stdout'] is kwargs['stderr'] and kwargs['stdout'].closed
    # Host has returned and closed its log handle, with no wait or termination.
    with launcher.ProcessLock(Path(launcher.tempfile.gettempdir()) / 'autoapply-dashboard-127.0.0.1-9876.lock'):
        assert host(root, request) == {'status':'already-running'}


@pytest.mark.parametrize('previous_birth', [None, 987654321])
def test_recent_stopped_failed_or_recycled_child_can_restart(installed, monkeypatch, previous_birth):
    root, _ = installed
    calls, births = [], {4321: 123456789}
    for flag in ['CREATE_NO_WINDOW', 'DETACHED_PROCESS', 'CREATE_NEW_PROCESS_GROUP', 'CREATE_BREAKAWAY_FROM_JOB']:
        monkeypatch.setattr(launcher.subprocess, flag, 1, raising=False)
    def spawn(*args, **kwargs):
        pid = 4321 + len(calls)
        calls.append(pid)
        births[pid] = 123456789
        return SimpleNamespace(pid=pid)
    monkeypatch.setattr(launcher.subprocess, 'Popen', spawn)
    monkeypatch.setattr(launcher, 'process_birth', lambda pid: births.get(pid))
    request = frame({'action':'start_dashboard', 'port':9876})
    assert host(root, request) == {'status':'starting'}
    assert host(root, request) == {'status':'starting'}  # fresh native host, live boot
    assert len(calls) == 1
    births[4321] = previous_birth  # stopped/crashed, or PID reused by another process
    assert host(root, request) == {'status':'starting'}  # no TTL delay, even in a new Chrome session
    assert calls == [4321, 4322]
    assert host(root, request) == {'status':'starting'}
    assert len(calls) == 2


def test_concurrent_native_boot_is_single_flight(installed, monkeypatch):
    root, _ = installed
    token = launcher.hashlib.sha256(str(root).encode()).hexdigest()[:16]
    lock = Path(launcher.tempfile.gettempdir()) / f'autoapply-launcher-{token}-9876.lock'
    monkeypatch.setattr(launcher.subprocess, 'Popen', lambda *a, **kw: pytest.fail('concurrent spawn'))
    with launcher.ProcessLock(lock):
        assert host(root, frame({'action':'start_dashboard', 'port':9876})) == {'status':'starting'}


def test_child_exited_before_marker_capture_does_not_suppress_retry(installed, monkeypatch):
    root, _ = installed
    calls = []
    for flag in ['CREATE_NO_WINDOW', 'DETACHED_PROCESS', 'CREATE_NEW_PROCESS_GROUP', 'CREATE_BREAKAWAY_FROM_JOB']:
        monkeypatch.setattr(launcher.subprocess, flag, 1, raising=False)
    def spawn(*args, **kwargs):
        calls.append(4321)
        return SimpleNamespace(pid=4321)
    monkeypatch.setattr(launcher.subprocess, 'Popen', spawn)
    monkeypatch.setattr(launcher, 'process_birth', lambda pid: None)
    request = frame({'action':'start_dashboard', 'port':9876})
    assert host(root, request) == {'status':'starting'}
    assert host(root, request) == {'status':'starting'}
    assert calls == [4321, 4321]


@pytest.mark.parametrize('encoded', [b'', b'old timestamp marker', b'\xff', b'{'*1100,
    b'[]', b'{"pid":true,"birth":1}', b'{"pid":0,"birth":1}', b'{"pid":4294967296,"birth":1}',
    b'{"pid":1,"birth":false}', b'{"pid":1,"birth":-1}', b'{"pid":1,"birth":18446744073709551616}',
    b'{"pid":1,"birth":1,"command":"ignored"}'])
def test_marker_reads_are_bounded_and_corrupt_safe(tmp_path, monkeypatch, encoded):
    marker = tmp_path / 'marker'
    marker.write_bytes(encoded)
    monkeypatch.setattr(launcher, 'process_birth', lambda *a: pytest.fail('invalid PID queried'))
    assert launcher.pending_child(marker) is False


@pytest.mark.parametrize('opened,times_ok,wait,expected', [
    (True, True, 258, (2 << 32) | 3), (True, True, 0, None),
    (True, True, 0xffffffff, None), (True, False, 258, None), (False, True, 258, None)])
def test_process_liveness_uses_only_query_and_sync_handles(opened, times_ok, wait, expected):
    calls = []
    class Function:
        def __init__(self, fn):
            self.fn = fn
        def __call__(self, *args):
            return self.fn(*args)
    def open_process(rights, inherit, pid):
        assert (rights, inherit, pid) == (0x00101000, False, 4321)
        return 99 if opened else None
    def get_times(handle, created, *other):
        assert handle == 99
        created._obj.dwHighDateTime, created._obj.dwLowDateTime = 2, 3
        return times_ok
    kernel = SimpleNamespace(OpenProcess=Function(open_process), GetProcessTimes=Function(get_times),
        WaitForSingleObject=Function(lambda handle, timeout: wait if (handle, timeout) == (99, 0) else pytest.fail('blocking wait')),
        CloseHandle=Function(lambda handle: calls.append(handle)))
    assert launcher.process_birth(4321, kernel) == expected
    assert calls == ([99] if opened else [])


def test_setup_and_spawn_failure_do_not_claim_ready(installed, monkeypatch):
    root, _ = installed
    monkeypatch.setattr(launcher.subprocess, 'CREATE_NO_WINDOW', 1, raising=False)
    monkeypatch.setattr(launcher.subprocess, 'DETACHED_PROCESS', 2, raising=False)
    monkeypatch.setattr(launcher.subprocess, 'CREATE_NEW_PROCESS_GROUP', 4, raising=False)
    monkeypatch.setattr(launcher.subprocess, 'CREATE_BREAKAWAY_FROM_JOB', 8, raising=False)
    def fail(*args, **kwargs):
        raise OSError('private details must not escape')
    monkeypatch.setattr(launcher.subprocess, 'Popen', fail)
    request = frame({'action':'start_dashboard', 'port':8765})
    assert host(root, request) == {'status':'error', 'error':'start_failed'}
    (root / '.venv/Scripts/python.exe').unlink()
    assert host(root, request) == {'status':'error', 'error':'venv_missing'}


@pytest.mark.parametrize('origins', [['chrome-extension://*/'], ['https://remote.test/'], [f'chrome-extension://{A}/extra'], [None], []])
def test_malformed_manifest_origins_do_not_launch(installed, monkeypatch, origins):
    root, registry = installed
    manifest = launcher.paths(root)[0]
    value = json.loads(manifest.read_text())
    value['allowed_origins'] = origins
    manifest.write_text(json.dumps(value))
    before = manifest.read_bytes()
    monkeypatch.setattr(launcher, 'start_dashboard', lambda *a: pytest.fail('unexpected spawn'))
    assert host(root, frame({'action':'start_dashboard', 'port':8765})) == {'status':'error', 'error':'invalid_request'}
    with pytest.raises(ValueError, match='foreign_installation'):
        launcher.install([B], root, registry)
    assert manifest.read_bytes() == before


def test_short_stream_reads_and_clean_eof():
    class ShortRead(io.BytesIO):
        def read(self, count=-1):
            return super().read(min(count, 2))
    request = {'action':'start_dashboard', 'port':8765}
    assert launcher.read_message(ShortRead(frame(request))) == request
    assert launcher.read_message(io.BytesIO()) is None
