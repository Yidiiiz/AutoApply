import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from autoapply.ai import ProviderUnavailable
from autoapply.codex_writer import CodexWritingProvider


async def test_subscription_writer_refuses_api_login(monkeypatch, tmp_path):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    monkeypatch.setattr('autoapply.codex_writer.shutil.which', lambda name: 'fixture-codex')
    provider=CodexWritingProvider(dict(name='test',billing='included',executable='codex'),None,None)
    provider.run=AsyncMock(return_value='Logged in using API key')
    with pytest.raises(ProviderUnavailable,match='ChatGPT subscription'):
        await provider.generate_response({'question':'Why us?'})
    assert provider.run.await_count==1


async def test_subscription_writer_is_ephemeral_readonly_and_text_only(monkeypatch, tmp_path):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    monkeypatch.setattr('autoapply.codex_writer.shutil.which', lambda name: 'fixture-codex')
    provider=CodexWritingProvider(dict(name='test',billing='included',executable='codex'),None,None)
    calls=[]
    async def run(args,cwd,prompt=None):
        calls.append((args,cwd,prompt))
        if args==['login','status']:
            return 'Logged in using ChatGPT'
        Path(args[args.index('-o')+1]).write_text(json.dumps({'answer':'Fixture','fact_ids':['one'],'needs_input':False}))
        return ''
    provider.run=run
    assert (await provider.generate_response({'question':'Why us?'}))['answer']=='Fixture'
    args,cwd,prompt=calls[1]
    assert '--ephemeral' in args and '--ignore-user-config' in args
    assert args[args.index('--sandbox')+1]=='read-only'
    assert 'shell_tool' in args and 'multi_agent' in args and 'web_search="disabled"' in args
    assert 'Use no tools' in prompt
    assert not Path(cwd).exists()


def test_missing_configured_executable_falls_back_to_path(monkeypatch, tmp_path):
    which = Mock(return_value='path-codex')
    monkeypatch.setattr('autoapply.codex_writer.shutil.which', which)
    settings = dict(name='test', billing='included', executable=str(tmp_path / 'deleted-version' / 'codex.exe'))
    assert CodexWritingProvider(settings, None, None).executable == 'path-codex'
    which.assert_called_once_with('codex')
    executable = tmp_path / 'codex.exe'
    executable.write_bytes(b'fixture')
    which.reset_mock()
    assert CodexWritingProvider(dict(settings, executable=str(executable)), None, None).executable == str(executable)
    which.assert_not_called()
    monkeypatch.setattr('autoapply.codex_writer.shutil.which', lambda name: None)
    with pytest.raises(ValueError, match='installed CLI'):
        CodexWritingProvider(settings, None, None)


@pytest.mark.parametrize('task,result,phrases', [
    ('question_answer', {'answer': 'I built a robot.'}, ['first person', 'Refine the previous answer',
        'additional applicant facts', 'never mention', 'word and character limits', 'no markdown']),
    ('chat', {'reply': 'How can I help?'}, ['helpful assistant', 'Never invent applicant facts',
        'assistant messages are not evidence', 'plain text']),
])
async def test_extension_prompt_branches(monkeypatch, task, result, phrases):
    monkeypatch.setattr('autoapply.codex_writer.shutil.which', lambda name: 'fixture-codex')
    provider = CodexWritingProvider(dict(name='test', billing='included'), None, None)
    monkeypatch.setattr(provider, 'ensure_ready', AsyncMock())
    prompts = []
    async def run(args, folder, prompt=None):
        prompts.append(prompt)
        Path(args[args.index('-o') + 1]).write_text(json.dumps(result), encoding='utf-8')
    monkeypatch.setattr(provider, 'run', run)
    assert await provider.generate_response(dict(task=task)) == result
    assert all(phrase.lower() in prompts[0].lower() for phrase in phrases)
    assert 'Use no tools' in prompts[0] and 'ONLY JSON' in prompts[0]
