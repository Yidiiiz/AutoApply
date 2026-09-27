"""Tokenized manual requests and a durable, non-replaying command ledger."""
import json
import uuid
from dataclasses import asdict, dataclass, field

from .models import now


@dataclass(frozen=True)
class ManualCommand:
    action: str
    application_id: int | None
    session_token: str | None = None
    parameters: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    @classmethod
    def parse(cls, text, db):
        """Both operator interfaces parse once, preserving exact answer/evidence text."""
        parts = text.strip().lstrip('!/').split(maxsplit=2)
        action = parts[0].lower() if parts else 'help'
        identifier = int(parts[1]) if len(parts)>1 and parts[1].isdigit() else None
        app_id = identifier
        if action in {'answer','skip','accept','ai','yes','no'} and identifier is not None:
            row = db.one('SELECT application_id FROM questions WHERE id=?', (identifier,))
            if not row:
                raise ValueError('Unknown question')
            app_id = row['application_id']
        remainder = parts[2] if len(parts)>2 else ''
        token = remainder.split('--token ', 1)[1].split()[0] if '--token ' in remainder else None
        return cls(action, app_id, token, {'parts': parts, 'identifier': identifier})

    def encode(self):
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def decode(cls, app_id, value):
        if value == 'inspect':
            # Old ordinary input commands are reconstruction requests, never live tokens.
            return cls('inspect', app_id, parameters={'reconstruct': True})
        data = json.loads(value)
        if not isinstance(data, dict):
            raise ValueError('Manual command must be an object')
        if (not isinstance(data.get('action'), str) or not isinstance(data.get('id'), str) or not data['id']
                or not isinstance(data.get('parameters', {}), dict)
                or not isinstance(data.get('session_token', data.get('token', '')) or '', str)):
            raise ValueError('Malformed manual command fields')
        if data.get('application_id', app_id) != app_id:
            raise ValueError('Manual command application mismatch')
        return cls(data['action'], app_id, data.get('session_token', data.get('token')),
                   data.get('parameters', {}), data['id'])


class ManualCommands:
    def __init__(self, db):
        self.db = db

    def enqueue(self, command):
        if command.action not in {'inspect', 'inspect-only', 'PASSED', 'FAILED', 'SKIPPED'}:
            raise ValueError('Unsupported manual operation')
        with self.db.lifecycle.atomic():
            if self.db.one('SELECT id FROM manual_commands WHERE id=?', (command.id,)):
                return command.id
            pending = self.db.one('SELECT * FROM manual_requests WHERE application_id=?', (command.application_id,))
            if pending:
                return ManualCommand.decode(command.application_id, pending['action']).id
            self.db.execute('INSERT INTO manual_commands VALUES (?,?,?,?,?,?,?)',
                            (command.id, command.application_id, command.encode(), 'PENDING', now(), now(), None))
            self.db.execute('INSERT INTO manual_requests VALUES (?,?,?)', (command.application_id, command.encode(), now()))
            self.db.event(command.application_id, 'manual_command_pending', command.encode())
        return command.id

    def claim(self, request):
        with self.db.lifecycle.atomic():
            try:
                command = ManualCommand.decode(request['application_id'], request['action'])
            except (ValueError, KeyError, TypeError):
                command = ManualCommand('invalid', request['application_id'], parameters={'legacy_payload': request['action']})
                self.db.execute('INSERT INTO manual_commands VALUES (?,?,?,?,?,?,?)',
                                (command.id, command.application_id, command.encode(), 'FAILED', now(), now(), 'Malformed legacy request; not executed'))
                self.db.execute('DELETE FROM manual_requests WHERE application_id=? AND action=? AND created_at=?',
                                (request['application_id'], request['action'], request['created_at']))
                self.db.event(command.application_id, 'manual_command_result', command.id + ': malformed request; not executed')
                return None
            # Legacy ingestion is persisted before removing the compatibility queue row.
            self.db.execute('INSERT OR IGNORE INTO manual_commands VALUES (?,?,?,?,?,?,?)',
                            (command.id, command.application_id, command.encode(), 'PENDING', now(), now(), None))
            deleted = self.db.execute('DELETE FROM manual_requests WHERE application_id=? AND action=? AND created_at=?',
                                     (request['application_id'], request['action'], request['created_at']))
            if not deleted.rowcount or self.db.setting(f'manual_ack:{command.id}'):
                return None
            claimed = self.db.execute("UPDATE manual_commands SET state='IN_PROGRESS',updated_at=? WHERE id=? AND state='PENDING'", (now(), command.id))
            if not claimed.rowcount:
                return None
            self.db.set_setting(f'manual_ack:{command.id}', 'IN_PROGRESS')
            self.db.event(command.application_id, 'manual_command_claimed', command.id)
            return command

    def finish(self, command, state, result):
        if state not in {'ACKNOWLEDGED', 'FAILED'}:
            raise ValueError('Invalid manual command result')
        with self.db.lifecycle.atomic():
            changed = self.db.execute('UPDATE manual_commands SET state=?,result=?,updated_at=? WHERE id=? AND state=\'IN_PROGRESS\'',
                            (state, result, now(), command.id))
            if not changed.rowcount:
                return
            self.db.set_setting(f'manual_ack:{command.id}', state)
            self.db.event(command.application_id, 'manual_command_result', json.dumps({'id': command.id, 'state': state, 'result': result}))

    def cancel(self, app_id=None):
        with self.db.lifecycle.atomic():
            suffix, args = (' WHERE application_id=?', (app_id,)) if app_id is not None else ('', ())
            for request in self.db.rows('SELECT * FROM manual_requests' + suffix, args):
                command = self.claim(request)
                if command:
                    self.finish(command, 'FAILED', 'Cancelled by operator')

    def recover(self):
        for row in self.db.rows("SELECT * FROM manual_commands WHERE state='IN_PROGRESS'"):
            command = ManualCommand.decode(row['application_id'], row['command'])
            self.finish(command, 'FAILED', 'Worker interrupted after claim; outcome requires inspection; not replayed')


def validate_session_token(db, app_id, token):
    target = db.setting('controlled_application_id')
    session = db.setting(f'manual_session:{app_id}')
    if target is not None and target != app_id:
        raise ValueError('Controlled run permits only the configured application')
    if target is not None and (not token or not session or token != session.get('token')):
        raise ValueError('Controlled resume requires the correct session token')
    if token is not None and (not session or token != session.get('token')):
        raise ValueError('Stale or incorrect session token')

def enqueue_inspection(db, app_id, action='inspect', *, token=None, reconstruct=False):
    app = db.application(app_id)
    target = db.setting('controlled_application_id')
    if db.automation_retired(app_id):
        raise ValueError('User-reported submission permanently excludes this application from automation')
    if target is not None and target != app_id:
        raise ValueError('Controlled run permits only the configured application')
    if not app['manual_action_required']:
        raise ValueError('Application has no pending manual intervention')
    session = db.setting(f'manual_session:{app_id}')
    validate_session_token(db, app_id, token)
    if session and app['session_preserved']:
        if session.get('busy'):
            raise ValueError('No idle preserved session is available')
        token = session['token']
        reconstruct = False
    elif not (reconstruct and target is None and app['error_category'] == 'INPUT_REQUIRED'
              and app['manual_resume_allowed'] and not app['submit_intent_at']):
        raise ValueError('No idle preserved session is available')
    command = ManualCommand(action, app_id, token, {'reconstruct': reconstruct})
    return ManualCommands(db).enqueue(command)
