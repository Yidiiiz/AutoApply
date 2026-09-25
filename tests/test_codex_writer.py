import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from autoapply.ai import ProviderUnavailable
from autoapply.codex_writer import CodexWritingProvider


async def test_subscription_writer_refuses_api_login(monkeypatch):
    provider=CodexWritingProvider(dict(name='test',billing='included',executable='codex'),None,None)
    provider.run=AsyncMock(return_value='Logged in using API key')
    with pytest.raises(ProviderUnavailable,match='ChatGPT subscription'):
        await provider.generate_response({'question':'Why us?'})
    assert provider.run.await_count==1


async def test_subscription_writer_is_ephemeral_readonly_and_text_only():
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
