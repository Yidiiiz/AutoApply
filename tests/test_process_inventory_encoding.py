"""Windows CIM decoding regressions; all process output is mocked."""
import json
from types import SimpleNamespace

import pytest

import autoapply.browser_resources as module


@pytest.fixture
def windows_inventory(monkeypatch):
    # Replace only this module's OS reference, without changing pathlib's platform.
    monkeypatch.setattr(module, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(module.subprocess, 'CREATE_NO_WINDOW', 0x08000000, raising=False)


@pytest.mark.parametrize('budget', [None, 1, 7.5, 120])
def test_utf8_subprocess_contract_and_timeout(windows_inventory, monkeypatch, budget):
    def run(args, **kwargs):
        assert args[:4] == ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command']
        assert len(args) == 5
        script = args[4]
        console = '[Console]::OutputEncoding=[System.Text.Encoding]::UTF8'
        output = '$OutputEncoding=[System.Text.Encoding]::UTF8'
        assert console in script and output in script
        assert script.index(console) < script.index('Get-CimInstance')
        assert script.index(output) < script.index('Get-CimInstance')
        assert kwargs['encoding'] == 'utf-8'
        assert kwargs['errors'] == 'replace'
        assert kwargs['capture_output'] is True and kwargs['text'] is True
        assert kwargs['check'] is True
        assert kwargs['creationflags'] == 0x08000000
        assert kwargs['timeout'] == (30 if budget is None else budget)
        return SimpleNamespace(stdout='[]')

    monkeypatch.setattr(module.subprocess, 'run', run)
    rows = module.process_inventory() if budget is None else module.process_inventory(budget)
    assert rows == {}


@pytest.mark.parametrize('stdout', [None, '', ' \r\n\t'])
def test_missing_output_fails_closed(windows_inventory, monkeypatch, stdout):
    monkeypatch.setattr(module.subprocess, 'run', lambda args, **kwargs: SimpleNamespace(stdout=stdout))
    with pytest.raises(module.BrowserOwnershipError, match='Process inventory returned no output'):
        module.process_inventory()


@pytest.mark.parametrize('single_object', [False, True])
def test_unicode_command_round_trip_and_row_shape(windows_inventory, monkeypatch, single_object):
    command = 'chrome.exe --user-data-dir="C:\\users\\\u0081\\\u6d4b\u8bd5"'
    row = dict(ProcessId=42, ParentProcessId=7, Birth='133700000000000000', CommandLine=command)
    payload = json.dumps(row if single_object else [row], ensure_ascii=False).encode('utf-8')

    def run(args, **kwargs):
        # Model subprocess's reader decoding bytes using the caller's settings.
        stdout = payload.decode(kwargs.get('encoding', 'cp1252'), kwargs.get('errors', 'strict'))
        return SimpleNamespace(stdout=stdout)

    monkeypatch.setattr(module.subprocess, 'run', run)
    assert module.process_inventory() == {
        42: dict(pid=42, parent=7, birth='133700000000000000', command=command),
    }


def test_stray_byte_does_not_abort_decoding(windows_inventory, monkeypatch):
    payload = b'[{"ProcessId":42,"ParentProcessId":7,"Birth":"123","CommandLine":"chrome \x81"}]'

    def run(args, **kwargs):
        stdout = payload.decode(kwargs.get('encoding', 'cp1252'), kwargs.get('errors', 'strict'))
        return SimpleNamespace(stdout=stdout)

    monkeypatch.setattr(module.subprocess, 'run', run)
    assert module.process_inventory()[42]['command'] == 'chrome \ufffd'


def test_null_command_line_remains_empty(windows_inventory, monkeypatch):
    payload = json.dumps([dict(ProcessId=42, ParentProcessId=7, Birth='123', CommandLine=None)])
    monkeypatch.setattr(module.subprocess, 'run', lambda args, **kwargs: SimpleNamespace(stdout=payload))
    assert module.process_inventory() == {42: dict(pid=42, parent=7, birth='123', command='')}


@pytest.mark.parametrize('budget', [0, -1, 120.1, True, False, None, '30', float('inf'), float('nan')])
def test_invalid_timeout_is_rejected_before_subprocess(windows_inventory, monkeypatch, budget):
    def forbidden(*args, **kwargs):
        pytest.fail('Invalid timeout reached subprocess.run')

    monkeypatch.setattr(module.subprocess, 'run', forbidden)
    with pytest.raises(ValueError, match='greater than zero and at most 120 seconds'):
        module.process_inventory(budget)
