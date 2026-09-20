"""Session-isolated lifecycle, adapted from the Lucidus recovery adapter.

Capture is incremental and at most once per turn, across process restarts.
A failed/interrupted extraction stays recorded; it is not silently retried.
"""
import contextlib
import hashlib
import logging
import os
import re
import sqlite3
import threading
import unicodedata
from collections import OrderedDict
from contextvars import ContextVar
from . import hooks, state

log = logging.getLogger(__name__)
context = ContextVar('icarus_session', default='')
_lock = threading.RLock()
_sessions = OrderedDict()


def memory_opt_out(message):
    text = unicodedata.normalize('NFKD', message.casefold())
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return bool(re.search(r"somente leitura|apenas leitura|read.only|nao (?:memorize|memoriza|guarde|registre|salve|anote)|do not (?:remember|save|store)|don.t (?:remember|save|store)|sem (?:grav|salv)", text))


def _session(sid):
    if sid not in _sessions:
        _sessions[sid] = {'tokens': set(), 'fabric': set(), 'qdrant': set(), 'sessions': set(), 'pending': OrderedDict()}
    _sessions.move_to_end(sid)
    if len(_sessions) > 128:
        # Never evict unpersisted turns; reject excess active sessions instead.
        for key in list(_sessions):
            if key != sid and not _sessions[key]['pending']:
                del _sessions[key]
                break
        else:
            del _sessions[sid]
            raise RuntimeError('Icarus active session limit reached')
    return _sessions[sid]


def on_session_start(session_id='', **kwargs):
    context.set(session_id)
    with _lock:
        _session(session_id)
    # Hermes owns SOUL/MEMORY injection. Do not rewrite MEMORY.md from Fabric.


def pre_llm_call(session_id='', user_message='', **kwargs):
    if not session_id:
        return None
    context.set(session_id)
    # Legacy recall globals are swapped atomically. Distinct sessions retain
    # their own topic gate and delivered IDs even when callbacks interleave.
    with _lock:
        s = _session(session_id)
        state.session_id = session_id
        hooks._last_query_tokens = s['tokens']
        hooks._injected_fabric = s['fabric']
        hooks._injected_qdrant = s['qdrant']
        hooks._injected_sessions = s['sessions']
        try:
            return hooks.pre_llm_call(session_id=session_id, user_message=user_message, **kwargs)
        finally:
            s['tokens'] = hooks._last_query_tokens


def post_llm_call(session_id='', user_message='', assistant_response='', platform='', turn_id='', **kwargs):
    context.set(session_id)
    if (os.environ.get('ICARUS_CAPTURE_ENABLED', '1') != '1' or not session_id
            or session_id.startswith('cron_') or platform == 'cron'
            or memory_opt_out(user_message) or hooks._is_social_close(user_message)
            or len(assistant_response.strip()) < 120):
        return
    turn = str(turn_id or hashlib.sha256((user_message + '\0' + assistant_response).encode()).hexdigest())
    with _lock:
        pending = _session(session_id)['pending']
        if len(pending) >= 32 and turn not in pending:
            log.warning('Icarus pending turn limit reached; capture skipped')
            return
        pending[turn] = (user_message[:5000], assistant_response[:10000], platform)


def on_session_end(session_id='', interrupted=False, **kwargs):
    context.set(session_id)
    with _lock:
        s = _sessions.get(session_id)
        items = list(s['pending'].items()) if s else []
        if s:
            s['pending'].clear()
    if interrupted or not items:
        return
    home = state.hermes_home()
    home.mkdir(parents=True, exist_ok=True)
    for turn, (user, answer, platform) in items:
        with contextlib.closing(sqlite3.connect(home / 'icarus-capture.sqlite3', timeout=30)) as con, con:
            con.execute('CREATE TABLE IF NOT EXISTS turns (session TEXT, turn TEXT, status TEXT, PRIMARY KEY(session,turn))')
            if not con.execute('INSERT OR IGNORE INTO turns VALUES (?,?,?)', (session_id, turn, 'processing')).rowcount:
                continue
        try:
            entries = hooks._llm_extract_entries('User:\n' + user + '\nAssistant:\n' + answer)
            for entry in entries[:2]:
                state.write_entry(entry['type'], entry['content'], entry['summary'],
                                  platform=platform or 'cli', training_value=entry.get('training_value', 'normal'),
                                  source_session_id=session_id, automatic=True, status='completed')
            status = 'processed' if entries else 'empty_or_unavailable'
        except Exception as exc:
            if 'budget' in str(exc).lower():
                # The daily automatic-capture budget is a deliberate stop, not a
                # transient error: reporting it as 'failed' hides the fact that
                # every later turn today will also be dropped.
                log.warning('Icarus capture skipped: %s — turn recorded as '
                            'budget_reached; raise ICARUS_MAX_DAILY_ENTRIES '
                            '(default 12) to capture more today', exc)
                status = 'budget_reached'
            else:
                log.warning('Icarus capture failed: %s: %s', type(exc).__name__, exc)
                status = 'failed'
        with contextlib.closing(sqlite3.connect(home / 'icarus-capture.sqlite3', timeout=30)) as con, con:
            con.execute('UPDATE turns SET status=? WHERE session=? AND turn=?', (status, session_id, turn))
