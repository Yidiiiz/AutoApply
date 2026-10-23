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

NAMES = ('AUTOAPPLY_LOGIN_EMAIL', 'AUTOAPPLY_LOGIN_PASSWORD')
MAX_BODY = 16384
_LOCK = threading.RLock()


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

    def load(self, *, profile_email=False):
        with _LOCK:
            saved, env = self._file(), self._env()
            email = env.get(NAMES[0], saved.get('email', ''))
            password = env.get(NAMES[1], saved.get('password', ''))
            _validate(email, password)
            if not email and profile_email:
                email = self.config.profile.get('contact', {}).get('email', '')
                _validate(email, password)
            return dict(email=email, password=password)

    def status(self):
        value = self.load()
        env = self._env()
        return dict(configured=bool(value['password']), email_override=bool(value['email']),
                    environment_override=any(name in env for name in NAMES))

    def save(self, payload):
        if not isinstance(payload, dict) or set(payload) not in ({'email', 'password'}, {'clear'}):
            raise SettingsRejected('Invalid account credentials request')
        with _LOCK:
            if 'clear' not in payload:
                _validate(payload['email'], payload['password'], required=True)
                serialized = json.dumps(payload, ensure_ascii=False)
                if len(serialized.encode('utf-8')) > MAX_BODY:
                    raise SettingsRejected('Invalid account credentials request')
            env = self._env()
            email = env.get(NAMES[0], payload.get('email', ''))
            password = env.get(NAMES[1], payload.get('password', ''))
            _validate(email, password)
            status = dict(configured=bool(password), email_override=bool(email),
                          environment_override=any(name in env for name in NAMES))
            if 'clear' in payload:
                if payload['clear'] is not True:
                    raise SettingsRejected('Invalid account credentials request')
                try:
                    self.path.unlink(missing_ok=True)
                except OSError:
                    raise SettingsRejected('Private account credentials could not be cleared') from None
            else:
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
            return status

    def redact(self, payload):
        values = [v for v in self.load().values() if v]
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
