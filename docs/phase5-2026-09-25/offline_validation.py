"""Optional pytest guard: local fixtures and mocked routes only, no external I/O.

Run with this directory on PYTHONPATH and pytest -p offline_validation.
This changes test infrastructure only, never production browser configuration.
"""
import ipaddress
import socket
from urllib.parse import urlsplit

import pytest


def local(host):
    if isinstance(host, bytes):
        host = host.decode('ascii')
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    connect, connect_ex, getaddrinfo = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo

    def check(address):
        if isinstance(address, tuple) and not local(address[0]):
            raise AssertionError('Phase 5 validation forbids external socket connections')

    def guarded_connect(self, address):
        check(address)
        return connect(self, address)

    def guarded_connect_ex(self, address):
        check(address)
        return connect_ex(self, address)

    def guarded_dns(host, *args, **kwargs):
        if host is not None and not local(host):
            raise AssertionError('Phase 5 validation forbids external DNS resolution')
        return getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, 'connect', guarded_connect)
    monkeypatch.setattr(socket.socket, 'connect_ex', guarded_connect_ex)
    monkeypatch.setattr(socket, 'getaddrinfo', guarded_dns)

    from playwright.async_api import Browser, BrowserType
    persistent, context, launch = BrowserType.launch_persistent_context, Browser.new_context, BrowserType.launch

    async def route_guard(route):
        if local(urlsplit(route.request.url).hostname or ''):
            await route.continue_()
        else:
            await route.abort()

    def offline_args(kwargs):
        kwargs['args'] = [*kwargs.get('args', []), '--disable-background-networking',
                          '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1, EXCLUDE [::1]']

    async def persistent_context(self, *args, **kwargs):
        offline_args(kwargs)
        result = await persistent(self, *args, **kwargs)
        # Test-specific page/context routes registered later take precedence.
        await result.route('**/*', route_guard)
        return result

    async def new_context(self, *args, **kwargs):
        result = await context(self, *args, **kwargs)
        await result.route('**/*', route_guard)
        return result

    async def browser_launch(self, *args, **kwargs):
        offline_args(kwargs)
        return await launch(self, *args, **kwargs)

    monkeypatch.setattr(BrowserType, 'launch_persistent_context', persistent_context)
    monkeypatch.setattr(Browser, 'new_context', new_context)
    monkeypatch.setattr(BrowserType, 'launch', browser_launch)
