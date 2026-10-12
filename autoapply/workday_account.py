"""Operator-authorized Workday candidate accounts (2026-10-07).

The user chose automatic account creation: one password kept only in the
ignored .env (WORKDAY_ACCOUNT_PASSWORD) and the profile email. Existing
accounts are signed into with the same credentials. Email verification and
security challenges still hold for the user. Values are never logged.
"""
import asyncio
import logging
import os
import re
import secrets
import string
from pathlib import Path

log = logging.getLogger('autoapply')
ENV = 'WORKDAY_ACCOUNT_PASSWORD'


def ensure_password(root):
    """Return the stored password, generating one into .env on first use."""
    value = os.environ.get(ENV, '').strip()
    if value:
        return value
    rng = secrets.SystemRandom()
    parts = [rng.choice(string.ascii_uppercase), rng.choice(string.ascii_lowercase),
             rng.choice(string.digits), rng.choice('!@#$%*?')]
    parts += [rng.choice(string.ascii_letters + string.digits) for _ in range(14)]
    rng.shuffle(parts)
    value = ''.join(parts)
    env = Path(root) / '.env'
    existing = env.read_text(encoding='utf-8') if env.exists() else ''
    if not re.search(rf'^{ENV}=', existing, re.M):
        with env.open('a', encoding='utf-8') as handle:
            handle.write(('' if existing.endswith('\n') or not existing else '\n') + f'{ENV}={value}\n')
    os.environ[ENV] = value
    log.info('[WORKDAY] Generated account password and stored it in .env as %s', ENV)
    return value


class AccountHold(RuntimeError):
    """The account step needs the user (email verification, rejected credentials)."""


async def _visible(locator):
    try:
        return await locator.count() > 0 and await locator.first.is_visible()
    except Exception:
        return False


async def _fill(page, automation_id, value):
    field = page.locator(f'input[data-automation-id="{automation_id}"]').first
    await field.fill(value)
    if await field.input_value() != value:
        raise AccountHold(f'Workday {automation_id} field did not retain its value')


async def _press(page, click, automation_id):
    # Workday overlays submit buttons with a transparent click_filter div.
    button = page.locator(f'[data-automation-id="{automation_id}"]').first
    overlay = page.locator(f'[data-automation-id="click_filter"][aria-label="{await button.inner_text()}"]')
    target = overlay.first if await _visible(overlay) else button
    await click(page, target)


async def _outcome(page, timeout=20):
    """Wait for the account form to go away (signed in) or an error/verification message."""
    for _ in range(int(timeout / .5)):
        await asyncio.sleep(.5)
        text = (await page.locator('body').inner_text())[:20000]
        if re.search(r'verify (?:your )?(?:email|account)|verification (?:email|link)|check your email|activate your account', text, re.I):
            return 'verify'
        if re.search(r'already (?:in use|exists|registered)|account (?:already )?exists|email address is already', text, re.I):
            return 'exists'
        if re.search(r'invalid (?:email|password|credentials)|incorrect (?:email|password)|wrong (?:email|password)|account (?:is )?locked', text, re.I):
            return 'rejected'
        if not await _visible(page.locator('input[data-automation-id="password"]')):
            return 'signed_in'
    return 'timeout'


async def create_or_sign_in(page, click, email, password):
    """Create the candidate account, or sign in when it already exists."""
    if not email or not password:
        raise AccountHold('Workday account credentials are unavailable')
    if await _visible(page.locator('[data-automation-id="createAccountSubmitButton"]')):
        await _fill(page, 'email', email)
        await _fill(page, 'password', password)
        if await _visible(page.locator('input[data-automation-id="verifyPassword"]')):
            await _fill(page, 'verifyPassword', password)
        agree = page.locator('input[data-automation-id="createAccountCheckbox"]')
        if await _visible(agree) and not await agree.first.is_checked():
            await agree.first.check()
        await _press(page, click, 'createAccountSubmitButton')
        result = await _outcome(page)
        log.info('[WORKDAY] Account creation outcome: %s', result)
        if result == 'signed_in':
            return 'created'
        if result != 'exists':
            raise AccountHold('Workday account created; open the verification email, then !resume-manual'
                              if result == 'verify' else f'Workday account creation did not complete ({result})')
        signin = page.locator('[data-automation-id="signInLink"]')
        if await _visible(signin):
            await _press(page, click, 'signInLink')
            await asyncio.sleep(1.5)
    # Sign-in form (existing account).
    if not await _visible(page.locator('input[data-automation-id="password"]')):
        signin = page.locator('[data-automation-id="signInLink"]')
        if await _visible(signin):
            await _press(page, click, 'signInLink')
            await asyncio.sleep(1.5)
    await _fill(page, 'email', email)
    await _fill(page, 'password', password)
    button = 'signInSubmitButton' if await _visible(page.locator('[data-automation-id="signInSubmitButton"]')) else None
    if not button:
        raise AccountHold('Workday sign-in button not found')
    await _press(page, click, button)
    result = await _outcome(page)
    log.info('[WORKDAY] Sign-in outcome: %s', result)
    if result != 'signed_in':
        raise AccountHold('Workday sign-in needs you: ' + {'verify': 'verify the account email, then !resume-manual',
                                                           'rejected': 'credentials were rejected'}.get(result, result))
    return 'signed_in'
