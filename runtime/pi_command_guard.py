"""Bounded shell-command deadlines inside an owning Pi round, not a worker timeout.

Only a unique direct child shell in its own process group may be signalled.
Unknown ownership stays unknown. Native tool completion always wins over a
late observation. Formal pi_check markers retain their declared deadline.
"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import signal
import subprocess
import time
from pathlib import Path

DEFAULT_SECONDS = 600.0
GRACE_SECONDS = 10.0


def process_snapshot():
    rows = subprocess.check_output(
        ['ps', '-axo', 'pid=,ppid=,pgid=,lstart=,comm='], text=True, timeout=3)
    result = {}
    for row in rows.splitlines():
        fields = row.split(None, 8)
        if len(fields) != 9:
            continue
        try:
            pid, parent, group = map(int, fields[:3])
        except ValueError:
            continue
        birth = ' '.join(fields[3:8])
        try:
            created = time.mktime(time.strptime(birth, '%a %b %d %H:%M:%S %Y'))
        except ValueError:
            continue
        result[pid] = {'pid': pid, 'parent': parent, 'group': group,
                       'birth': birth, 'createdAt': created, 'comm': fields[8]}
    return result


def descendants(rows, root):
    owned = {root}
    while True:
        added = {pid for pid, row in rows.items() if row['parent'] in owned} - owned
        if not added:
            return owned - {root}
        owned.update(added)


def finite_seconds(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


FORBIDDEN_RULE = 'forbidden-path'
_PATH_SPLIT = re.compile(r"""[\s'"`;|&()<>=,:{}\[\]$]+""")


def _resolve(token, cwd):
    token = os.path.expanduser(token)
    if not os.path.isabs(token):
        token = os.path.join(cwd, token)
    return os.path.realpath(token)


def _within(path, root):
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def forbidden_reference(command, forbidden, allowed, cwd):
    """First path in ``command`` that resolves (through symlinks) into a forbidden root.

    A path that is also inside an allowed root (round worktree, task tools,
    round checks) is never forbidden, even when the allowed root lives under a
    forbidden one (the task directory sits inside the main checkout's ``.git``).
    Returns the resolved path or None. Tokens without a slash are not paths.
    """
    if not forbidden:
        return None
    roots = [os.path.realpath(root) for root in forbidden]
    safe = [os.path.realpath(root) for root in allowed]
    for token in _PATH_SPLIT.split(str(command)):
        if '/' not in token and token not in ('~', '..', '.'):
            continue
        try:
            path = _resolve(token, cwd)
        except (OSError, ValueError):
            continue
        if any(_within(path, root) for root in safe):
            continue
        if any(_within(path, root) for root in roots):
            return path
    return None


class CommandGuard:
    def __init__(self, round_dir, pi_pid, ceiling=3600.0, default=DEFAULT_SECONDS,
                 forbidden=None, allowed=None, cwd=None):
        self.forbidden = list(forbidden or [])
        self.allowed = list(allowed or [])
        self.cwd = str(cwd) if cwd else os.getcwd()
        self.round_dir = Path(round_dir)
        self.pi_pid = pi_pid
        self.ceiling = ceiling
        self.default = min(default, ceiling)
        self.offset = 0
        self.pending = b''
        self.active = None
        self.events = []
        self.assistant_at = None

    def persist(self):
        value = {'schemaVersion': 1, 'piPid': self.pi_pid, 'defaultSeconds': self.default,
                 'ceilingSeconds': self.ceiling, 'active': self.active,
                 'events': self.events[-100:], 'acceptance': 'not_verified'}
        target = self.round_dir / 'command-guard.json'
        temp = target.with_suffix('.tmp')
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
        os.replace(temp, target)

    def consume(self, now):
        source = self.round_dir / 'round.jsonl'
        with source.open('rb') as stream:
            stream.seek(self.offset)
            data = stream.read(8 * 1024 * 1024)
            self.offset = stream.tell()
        lines = (self.pending + data).split(b'\n')
        self.pending = lines.pop()
        changed = False
        for line in lines:
            try:
                row = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            message = row.get('message') or {}
            if row.get('type') == 'message_end' and message.get('role') == 'assistant':
                stamp = message.get('timestamp')
                if finite_seconds(stamp) and stamp / 1000 <= now + 1:
                    self.assistant_at = stamp / 1000
            if row.get('type') == 'tool_execution_start' and row.get('toolName') == 'bash':
                args = row.get('args') or {}
                timeout = args.get('timeout')
                seconds = min(timeout, self.ceiling) if finite_seconds(timeout) else self.default
                # One Pi bash runs serially. Ambiguous starts cannot authorize a signal.
                if self.active:
                    self.active['status'] = 'unknown-overlapping-tool-start'
                else:
                    start = self.assistant_at if self.assistant_at is not None else now
                    hit = forbidden_reference(args.get('command', ''), self.forbidden,
                                              self.allowed, self.cwd)
                    if hit:
                        # Terminate immediately once ownership is proven; never
                        # a pre-ownership signal.
                        seconds = 0.0
                    self.active = {'toolCallId': row.get('toolCallId'), 'startedAt': start,
                                   'deadlineAt': start + seconds + GRACE_SECONDS,
                                   'timeoutSeconds': seconds,
                                   'timeoutSource': 'native-explicit' if finite_seconds(timeout) else 'default',
                                   'commandSha256': hashlib.sha256(str(args.get('command', '')).encode()).hexdigest(),
                                   'status': 'running', 'process': None}
                    if hit:
                        self.active.update(rule=FORBIDDEN_RULE, forbiddenPath=hit,
                                           deadlineAt=start)
                changed = True
            elif row.get('type') == 'tool_execution_end' and self.active and row.get('toolCallId') == self.active['toolCallId']:
                self.events.append({**self.active, 'endedAt': now,
                                    'toolEnded': True, 'toolIsError': row.get('isError'),
                                    'status': (self.active.get('status') if self.active.get('rule')
                                               and self.active.get('signalledAt') else
                                               'timed_out' if self.active.get('signalledAt')
                                               else 'tool_ended')})
                self.active = None
                changed = True
        if changed:
            self.persist()

    def scan(self, now=None, rows=None):
        now = time.time() if now is None else now
        self.consume(now)
        active = self.active
        if not active or active['status'].startswith('unknown'):
            return
        supplied_rows = rows is not None
        try:
            rows = process_snapshot() if rows is None else rows
        except (OSError, subprocess.SubprocessError) as exc:
            active['status'] = 'unknown-process-inspection'
            active['inspectionError'] = type(exc).__name__
            self.persist()
            return
        if active['process'] is None:
            candidates = [row for row in rows.values()
                          if row['parent'] == self.pi_pid and row['pid'] == row['group']
                          and active['startedAt'] - 5 <= row.get('createdAt', -1) <= now + 1]
            if len(candidates) != 1:
                if now >= active['deadlineAt']:
                    active['status'] = 'unknown-shell-ownership'
                    self.persist()
                return
            active['process'] = candidates[0]
            # Model message timestamps may precede actual tool spawn by the
            # generation duration. Anchor execution time to the OS birth fact.
            active['messageAt'] = active['startedAt']
            active['startedAt'] = candidates[0]['createdAt']
            active['deadlineAt'] = active['startedAt'] + active['timeoutSeconds'] + (
                0.0 if active.get('rule') else GRACE_SECONDS)
            self.persist()
        identity = active['process']
        live = rows.get(identity['pid'])
        if live != identity:
            active['status'] = 'unknown-process-identity'
            self.persist()
            return
        owned = descendants(rows, self.pi_pid)
        group_members = {pid for pid, row in rows.items() if row['group'] == identity['group']}
        if identity['pid'] not in owned or not group_members <= owned or identity['group'] == os.getpgrp():
            active['status'] = 'unknown-group-ownership'
            self.persist()
            return
        # pi_check uses its own actual child PID and explicit immutable wrapper
        # deadline. A valid owned marker supersedes the default temporary-probe
        # deadline, but never the phase's command ceiling.
        for marker in ([] if active.get('rule') else
                       (self.round_dir / 'round.checks').glob('*.running')):
            try:
                data = json.loads(marker.read_text())
            except (OSError, ValueError):
                continue
            start, deadline = data.get('started_at'), data.get('deadline_at')
            if data.get('pid') not in descendants(rows, identity['pid']):
                continue
            if not finite_seconds(start) or not finite_seconds(deadline) or not 0 < deadline - start <= self.ceiling:
                continue
            if start < active['startedAt'] - 5 or start > now or not marker.is_file():
                continue
            active['deadlineAt'] = deadline + GRACE_SECONDS
            active['timeoutSource'] = 'owned-pi_check-marker'
        if now < active['deadlineAt']:
            return
        # Re-read completion and OS identity immediately before signalling.
        self.consume(now)
        if self.active is not active:
            return
        try:
            actual = rows if supplied_rows else process_snapshot()
        except (OSError, subprocess.SubprocessError) as exc:
            active['status'] = 'unknown-process-inspection'
            active['inspectionError'] = type(exc).__name__
            self.persist()
            return
        if actual.get(identity['pid']) != identity:
            return
        # Refuse detached descendant groups: half-cleanup must not be presented
        # as a complete command stop. The heartbeat main can diagnose these.
        actual_owned = descendants(actual, self.pi_pid)
        actual_group = {p for p, r in actual.items() if r['group'] == identity['group']}
        if identity['pid'] not in actual_owned or not actual_group <= actual_owned:
            active['status'] = 'unknown-group-ownership'
            self.persist()
            return
        subtree = descendants(actual, identity['pid']) | {identity['pid']}
        if any(actual[p]['group'] != identity['group'] for p in subtree):
            active['status'] = 'unknown-detached-descendant'
            self.persist()
            return
        sig = signal.SIGKILL if active.get('signalledAt') and now - active['signalledAt'] >= 5 else signal.SIGTERM
        if active.get('signalledAt') and sig == signal.SIGTERM:
            return
        active['status'] = 'forbidden_path_terminated' if active.get('rule') else 'timed_out'
        active.setdefault('signalledAt', now)
        active['signal'] = int(sig)
        self.persist()  # preserve timeout evidence before the effect
        # Keep every attempted timeout intervention even when the bounded
        # status projection drops old routine events. Requested != confirmed.
        with (self.round_dir / 'command-guard-signals.jsonl').open('a') as log:
            log.write(json.dumps({**active, 'effect': 'unconfirmed', 'signalRequestedAt': now}) + '\n')
        try:
            os.killpg(identity['group'], sig)
        except ProcessLookupError:
            pass
        except OSError as exc:
            active["status"] = "unknown-signal-effect"
            active["signalError"] = type(exc).__name__
            self.persist()


def main():
    parser = argparse.ArgumentParser(description='Pinned command protection for an existing owned Pi round; never edits the worker/helper contract')
    parser.add_argument('--round-dir', type=Path, required=True)
    parser.add_argument('--pi-pid', type=int, required=True)
    parser.add_argument('--ceiling-seconds', type=float, default=3600)
    args = parser.parse_args()
    if not finite_seconds(args.ceiling_seconds):
        parser.error('finite positive ceiling required')
    root = args.round_dir.resolve()
    task_dir = root.parent.parent
    lock = (root / 'command-guard.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    guard = CommandGuard(root, args.pi_pid, ceiling=args.ceiling_seconds)
    initial = process_snapshot().get(args.pi_pid)
    if initial is None:
        raise SystemExit('Pi worker is absent; no process signalled')
    while True:
        state = json.loads((root / 'round.state.json').read_text())
        if state.get('state') not in ('starting', 'running') or state.get('piPid') != args.pi_pid:
            break
        rows = process_snapshot()
        if rows.get(args.pi_pid) != initial:
            break
        # A user pause disables automatic intervention even when the worker is
        # still alive. Missing board means no registered pause to override.
        paused = False
        board_file = task_dir.parent.parent / 'board.json'
        if board_file.exists():
            board = json.loads(board_file.read_text())
            task = json.loads((task_dir / 'task.json').read_text())
            paused = (board.get('cards', {}).get(task.get('task'), {}) or {}).get('paused', False)
        if not paused:
            guard.scan()
        time.sleep(1)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
