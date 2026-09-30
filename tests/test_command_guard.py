from __future__ import annotations
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from runtime_helpers import RUNTIME
sys.path.insert(0, str(RUNTIME))
from pi_command_guard import CommandGuard

class CommandGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name);self.log = self.root/'round.jsonl';self.log.touch()
        self.guard = CommandGuard(self.root, 900001, default=1, ceiling=60)
        self.rows = {900002:{'pid':900002,'parent':900001,'group':900002,'birth':'fixed','createdAt':100,'comm':'/bin/bash'}}
    def emit(self, **values):
        with self.log.open('a') as f:f.write(json.dumps(values)+'\n')
    def start(self, **args):
        self.emit(type='tool_execution_start',toolName='bash',toolCallId='c',args={'command':'fixture',**args})
        self.guard.scan(now=100,rows=self.rows)
    def test_default_deadline_signals_only_owned_shell_group(self):
        self.start()
        with patch('pi_command_guard.os.killpg') as kill:
            self.guard.scan(now=112,rows=self.rows);kill.assert_called_once_with(900002,signal.SIGTERM)
        history=[json.loads(line) for line in (self.root/'command-guard-signals.jsonl').read_text().splitlines()]
        self.assertEqual(history[0]['effect'],'unconfirmed')
        self.assertEqual(history[0]['process']['pid'],900002)
        state=json.loads((self.root/'command-guard.json').read_text());self.assertEqual(state['active']['status'],'timed_out');self.assertEqual(state['acceptance'],'not_verified')
    def test_completed_tool_wins_even_after_deadline(self):
        self.start();self.emit(type='tool_execution_end',toolCallId='c',isError=False)
        with patch('pi_command_guard.os.killpg') as kill:self.guard.scan(now=112,rows=self.rows);kill.assert_not_called()
        self.assertIsNone(self.guard.active)
    def test_explicit_deadline_and_quiet_long_command_are_preserved(self):
        self.start(timeout=40)
        with patch('pi_command_guard.os.killpg') as kill:self.guard.scan(now=130,rows=self.rows);kill.assert_not_called()
        self.assertEqual(self.guard.active['deadlineAt'],150)
    def test_partial_json_is_not_a_start_until_complete(self):
        self.log.write_text('{"type":"tool_execution_start"')
        self.guard.scan(now=100,rows=self.rows);self.assertIsNone(self.guard.active)
    def test_wrong_parent_reused_pid_and_ambiguous_shells_never_signal(self):
        for mutation in ['parent','birth','extra']:
            self.guard=CommandGuard(self.root,900001,default=1,ceiling=60);self.log.write_text('');self.start()
            rows={p:dict(r) for p,r in self.rows.items()}
            if mutation=='extra':rows[900003]={**rows[900002],'pid':900003,'parent':55}
            else:rows[900002][mutation]=88 if mutation=='parent' else 'reused'
            with patch('pi_command_guard.os.killpg') as kill:self.guard.scan(now=112,rows=rows);kill.assert_not_called()
    def test_detached_descendant_requires_main_diagnosis(self):
        self.start();rows={**self.rows,900003:{'pid':900003,'parent':900002,'group':900003,'birth':'fixed','createdAt':100,'comm':'python'}}
        with patch('pi_command_guard.os.killpg') as kill:self.guard.scan(now=112,rows=rows);kill.assert_not_called()
        self.assertEqual(self.guard.active['status'],'unknown-detached-descendant')
    def test_owned_formal_marker_preserves_actual_declared_deadline(self):
        self.start();rows={**self.rows,900003:{'pid':900003,'parent':900002,'group':900002,'birth':'fixed','createdAt':100,'comm':'python'}}
        d=self.root/'round.checks';d.mkdir();(d/'x.running').write_text(json.dumps({'pid':900003,'started_at':100,'deadline_at':150}))
        with patch('pi_command_guard.os.killpg') as kill:self.guard.scan(now=112,rows=rows);kill.assert_not_called()
        self.assertEqual(self.guard.active['deadlineAt'],160)
    def test_unrelated_or_over_ceiling_marker_does_not_extend(self):
        self.start();d=self.root/'round.checks';d.mkdir();(d/'x.running').write_text(json.dumps({'pid':77,'started_at':100,'deadline_at':10000}))
        with patch('pi_command_guard.os.killpg') as kill:self.guard.scan(now=112,rows=self.rows);kill.assert_called_once()
    def test_attached_guard_uses_original_tool_timestamp_not_attach_time(self):
        self.emit(type='message_end',message={'role':'assistant','timestamp':100000})
        self.emit(type='tool_execution_start',toolName='bash',toolCallId='c',args={'command':'fixture'})
        with patch('pi_command_guard.os.killpg') as kill:self.guard.scan(now=200,rows=self.rows);kill.assert_called_once_with(900002,signal.SIGTERM)
        self.assertEqual(self.guard.active['startedAt'],100)
    def test_background_process_older_than_tool_is_not_adopted(self):
        rows={p:{**r,'createdAt':10} for p,r in self.rows.items()}
        self.emit(type='tool_execution_start',toolName='bash',toolCallId='c',args={'command':'fixture'})
        with patch('pi_command_guard.os.killpg') as kill:self.guard.scan(now=200,rows=rows);kill.assert_not_called()
        self.assertEqual(self.guard.active['status'],'running')
    def test_failed_os_inspection_is_unknown_and_does_not_signal(self):
        self.start()
        with patch('pi_command_guard.process_snapshot',side_effect=OSError('unavailable')), patch('pi_command_guard.os.killpg') as kill:
            self.guard.scan(now=112);kill.assert_not_called()
        self.assertEqual(self.guard.active['status'],'unknown-process-inspection')
    def test_generation_delay_does_not_shorten_actual_execution_deadline(self):
        self.emit(type='message_end',message={'role':'assistant','timestamp':100000})
        self.emit(type='tool_execution_start',toolName='bash',toolCallId='c',args={'command':'fixture','timeout':20})
        rows={p:{**r,'createdAt':130} for p,r in self.rows.items()}
        with patch('pi_command_guard.os.killpg') as kill:self.guard.scan(now=135,rows=rows);kill.assert_not_called()
        self.assertEqual(self.guard.active['messageAt'],100)
        self.assertEqual(self.guard.active['startedAt'],130)
        self.assertEqual(self.guard.active['deadlineAt'],160)
    def test_real_hung_shell_is_stopped_and_parent_survives(self):
        child=subprocess.Popen(['/bin/sh','-c','sleep 30'],start_new_session=True)
        self.addCleanup(lambda: child.poll() is None and os.killpg(child.pid,signal.SIGKILL))
        self.guard=CommandGuard(self.root,os.getpid(),default=.01,ceiling=60)
        self.emit(type='tool_execution_start',toolName='bash',toolCallId='c',args={'command':'sleep 30'})
        self.guard.scan(now=time.time());self.assertEqual(self.guard.active['process']['pid'],child.pid)
        self.guard.scan(now=time.time()+11);self.assertNotEqual(child.wait(timeout=5),0)
        os.kill(os.getpid(),0)

if __name__ == '__main__':unittest.main()
