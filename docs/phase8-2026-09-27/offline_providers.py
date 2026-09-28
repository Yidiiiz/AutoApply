"""Additional offline guard; use alongside the unchanged Phase 5 network plugin."""
import pytest


@pytest.fixture(autouse=True)
def no_live_writing_provider(monkeypatch, tmp_path):
    from autoapply.ai import BrowserAIProvider
    from autoapply.codex_writer import CodexWritingProvider

    async def blocked(*args, **kwargs):
        raise AssertionError('Phase 8 validation requires a fake writing provider')

    monkeypatch.setattr(BrowserAIProvider, 'generate_response', blocked)
    monkeypatch.setattr(CodexWritingProvider, 'run', blocked)
    monkeypatch.setenv('CODEX_HOME', str(tmp_path/'offline-codex'))
    for key in ('OPENAI_API_KEY', 'CODEX_API_KEY'):
        monkeypatch.delenv(key, raising=False)
