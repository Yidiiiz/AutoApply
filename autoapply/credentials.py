"""Private account credentials. Never merge these values into profile/config/answers."""

import csv
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading

from .dashboard_settings import SettingsRejected

NAMES = ('AUTOAPPLY_LOGIN_EMAIL', 'AUTOAPPLY_LOGIN_PASSWORD', 'AUTOAPPLY_LOGIN_STRICT_PASSWORD')
MAX_BODY = 16384
_LOCK = threading.RLock()


def strict_password(value):
    return (isinstance(value, str) and 12 <= len(value) <= 4096 and
            all(re.search(pattern, value) for pattern in (r'[A-Z]', r'[a-z]', r'[0-9]', r'[^A-Za-z0-9\s]')))


def _validate(email, password, *, required=False):
    if (not isinstance(email, str) or len(email) > 254 or
            email and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email) or
            not isinstance(password, str) or len(password) > 4096 or
            any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in email + password) or
            required and not password):
        raise SettingsRejected('Invalid account credentials')


class CredentialStore:
    def __init__(self, config):
        self.config = config
        self.path = config.private / 'credentials.json'

    def _file(self):
        try:
            if self.path.stat().st_size > MAX_BODY:
                raise ValueError
            value = json.loads(self.path.read_text(encoding='utf-8'))
            _validate(value.get('email', ''), value.get('password', ''))
            strict = value.get('strict_password', '')
            _validate('', strict)
            if strict and not strict_password(strict):
                raise ValueError
            choices = value.get('variants', [])
            if (not isinstance(choices, list) or len(choices) > 32 or any(
                    not isinstance(row, dict) or set(row) != {'origin', 'email', 'variant'} or
                    not all(isinstance(row[k], str) for k in row) or
                    len(row['origin']) > 300 or len(row['email']) > 254 or
                    row['variant'] not in {'standard', 'strict'} for row in choices)):
                raise ValueError
            return value
        except FileNotFoundError:
            return {}
        except Exception:
            raise SettingsRejected('Private account credentials could not be read') from None

    def _env(self):
        # Deliberately NOT a shell/dotenv evaluator. Everything after '=' is literal.
        result = {}
        path = self.config.root / '.env'
        try:
            with path.open(encoding='utf-8-sig', newline='') as stream:
                for line in stream:
                    key, sep, value = line.rstrip('\r\n').partition('=')
                    if sep and key in NAMES:
                        result[key] = value
        except FileNotFoundError:
            pass
        for name in NAMES:
            if name in os.environ:
                result[name] = os.environ[name]
        return result

    def load(self, *, profile_email=False, include_strict=False):
        with _LOCK:
            saved, env = self._file(), self._env()
            email = env.get(NAMES[0], saved.get('email', ''))
            password = env.get(NAMES[1], saved.get('password', ''))
            _validate(email, password)
            if not email and profile_email:
                email = self.config.profile.get('contact', {}).get('email', '')
                _validate(email, password)
            result = dict(email=email, password=password)
            if include_strict:
                strict = env.get(NAMES[2], saved.get('strict_password', ''))
                _validate('', strict)
                if strict and not strict_password(strict):
                    raise SettingsRejected('Invalid strict account password')
                result['strict_password'] = strict
            return result

    def select(self, origin, mode, policy):
        with _LOCK:
            value = self.load(profile_email=True, include_strict=True)
            remembered = next((row['variant'] for row in self._file().get('variants', [])
                               if row['origin'] == origin and row['email'] == value['email']), None)
            variant = remembered or 'standard'
            # A remembered account password must never be silently replaced.
            if remembered is None and mode == 'signup' and variant == 'standard' and policy == 'strict' and not strict_password(value['password']):
                variant = 'strict'
            password = value['strict_password'] if variant == 'strict' else value['password']
            if policy == 'strict' and mode == 'signup' and not strict_password(password):
                return dict(configured=False, strict_required=True)
            if not password or not value['email']:
                return dict(configured=False)
            return dict(configured=True, email=value['email'], password=password, variant=variant)

    def remember(self, origin, variant):
        with _LOCK:
            value = self.load(profile_email=True, include_strict=True)
            saved = self._file()
            choices = [row for row in saved.get('variants', [])
                       if (row['origin'], row['email']) != (origin, value['email'])]
            choices.append(dict(origin=origin, email=value['email'], variant=variant))
            saved['variants'] = choices[-32:]
            self._write(saved)

    def status(self):
        value = self.load(include_strict=True)
        env = self._env()
        return dict(configured=bool(value['password']), strict_configured=bool(value['strict_password']), email_override=bool(value['email']),
                    environment_override=any(name in env for name in NAMES))

    def save(self, payload):
        if not isinstance(payload, dict) or set(payload) not in ({'email', 'password'}, {'email', 'password', 'strict_password'}, {'clear'}):
            raise SettingsRejected('Invalid account credentials request')
        with _LOCK:
            if 'clear' not in payload:
                _validate(payload['email'], payload['password'], required=not payload.get('strict_password'))
                saved = self._file()
                if 'strict_password' in payload:
                    strict = payload['strict_password']
                    _validate('', strict)
                    if strict and not strict_password(strict):
                        raise SettingsRejected('Strict password requires 12+ characters, uppercase, lowercase, digit and special character')
                    if strict:
                        saved['strict_password'] = strict
                    # A strict-only save preserves blank standard/email inputs.
                    if strict and not payload['password']:
                        payload = {**payload, 'password': saved.get('password', ''),
                                   'email': payload['email'] or saved.get('email', '')}
                saved.update({key: payload[key] for key in ('email', 'password')})
            env = self._env()
            email = env.get(NAMES[0], payload.get('email', ''))
            password = env.get(NAMES[1], payload.get('password', ''))
            _validate(email, password)
            strict = env.get(NAMES[2], saved.get('strict_password', '') if 'clear' not in payload else '')
            _validate('', strict)
            if strict and not strict_password(strict):
                raise SettingsRejected('Invalid strict account password')
            status = dict(configured=bool(password), email_override=bool(email),
                          strict_configured=bool(strict),
                          environment_override=any(name in env for name in NAMES))
            if 'clear' in payload:
                if payload['clear'] is not True:
                    raise SettingsRejected('Invalid account credentials request')
                try:
                    self.path.unlink(missing_ok=True)
                except OSError:
                    raise SettingsRejected('Private account credentials could not be cleared') from None
            else:
                self._write(saved)
            return status

    def _write(self, value):
        serialized = json.dumps(value, ensure_ascii=False)
        if len(serialized.encode('utf-8')) > MAX_BODY:
            raise SettingsRejected('Invalid account credentials request')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            fd, temporary = tempfile.mkstemp(prefix='.credentials-', dir=self.path.parent)
            os.close(fd)
            if os.name == 'nt':
                sid = list(csv.reader(io.StringIO(subprocess.check_output(
                    ['whoami', '/user', '/fo', 'csv', '/nh'], text=True))))[0][1]
                subprocess.run(['icacls', temporary, '/inheritance:r', '/grant:r', '*' + sid + ':(F)'],
                               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                os.chmod(temporary, 0o600)
            with open(temporary, 'w', encoding='utf-8', newline='') as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except Exception:
            raise SettingsRejected('Private account credentials could not be saved') from None
        finally:
            if temporary:
                Path(temporary).unlink(missing_ok=True)

    def redact(self, payload):
        # Include saved values even when environment overrides are active.
        saved = self._file()
        values = sorted({v for v in (*self.load(include_strict=True).values(),
                        *(saved.get(key, '') for key in ('email', 'password', 'strict_password'))) if v}, key=len, reverse=True)
        def clean(value):
            if isinstance(value, str):
                for secret in values:
                    value = value.replace(secret, '[private account value]')
                return value
            if isinstance(value, list):
                return [clean(v) for v in value]
            if isinstance(value, dict):
                return {clean(k): clean(v) for k, v in value.items()}
            return value
        return clean(payload)
