"""Capacity, history, transaction/restart and frozen-runtime recovery contracts."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from runtime_helpers import RUNTIME, Repo, base_env, cleanup_repos, default_config
sys.path.insert(0,str(RUNTIME))
import pi_archive
import pi_board
import pi_core
import pi_events
import pi_queue
import pi_recovery
import pi_store
import pi_task
from pi_takeover import review_policy

THREAD='11111111-2222-3333-4444-555555555555'


class ArchiveTest(unittest.TestCase):
    def test_explicit_takeover_checkpoint_retains_actual_failure_count(self):
        c = self.card()
        c["reviewPolicyPin"] = {"qualityFailureLimit": 2, "earlyTakeover": True}
        c["codex"]["takeover"] = {"required": True, "cause": "main_decision",
            "note": "Verified repair uncertainty", "limit": 2, "failedDeliveries": 1,
            "failedReports": [{"round": 1, "eventId": "a" * 64}]}
        self.legacy({"task": c})
        pi_recovery.recover_store(self.repo.root)
        for number in range(2, 65):
            board, card = self.load()
            event = pi_events.add_event(card, "review_required", number, str(number), "review",
                                       {"head": self.head}, {}, "review", number)
            event.update(handled=True, decision="accepted", handledAt=number)
            self.save(board)
        _, card = self.load()
        policy = review_policy(card)
        self.assertEqual(policy["failedDeliveries"], 1)
        self.assertEqual(policy["takeoverCause"], "main_decision")
        self.assertEqual(policy["takeoverNote"], "Verified repair uncertainty")
        self.assertEqual(policy["implementationOwner"], "codex")
        self.assertEqual(policy["failedReports"], [{"round": 1, "eventId": "a" * 64}])

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='codex-pi-archive-')
        self.addCleanup(self.tmp.cleanup);self.addCleanup(cleanup_repos)
        self.root=Path(self.tmp.name)
        self.repo=Repo(self.root,config=default_config())
        self.wt=self.repo.worktree('worker')
        self.file=self.repo.state_dir/'board.json';self.file.parent.mkdir(parents=True,exist_ok=True)
        self.head=pi_core.git(self.wt,'rev-parse','HEAD')

    def card(self,name='task'):
        c=pi_events._new_card(name,THREAD,'Synthetic','Synthetic',None,None,
                             self.repo.root,self.repo.state_dir,str(self.wt),'offline',None,1)
        c['pi']={'round':1,'state':'completed'}
        return c

    def legacy(self,cards):
        b={'schemaVersion':1,'revision':10,'createdAt':1,'updatedAt':1,'cards':cards}
        self.file.write_text(json.dumps(b,indent=2)+'\n');return b

    def load(self,task='task'):
        b,problem=pi_store.read_board(self.file);self.assertIsNone(problem)
        return b,b['cards'][task]

    def save(self,b):
        b['revision']+=1;pi_store._write_board(self.file,b)

    def test_near_full_legacy_history_is_preserved_and_new_pending_work_fits(self):
        cards={f'history-{i:02}':self.card(f'history-{i:02}') for i in range(25)}
        current=self.card();current['paused']=True;cards['task']=current
        old=cards['history-00'];old['reviewPolicyPin']={'qualityFailureLimit':1}
        old['handled']['a'*64]={'round':1,'decision':'changes_requested','at':1,
                               'eventKind':'review_required','failureKind':'quality'}
        old['codex']['takeover']={'required':True,'limit':1,'failedReports':[{'round':1,'eventId':'a'*64}]}
        b=self.legacy(cards)
        current['padding']='x'* (260_000-len(self.file.read_bytes()))
        self.legacy(cards);raw=self.file.read_bytes()
        broken=json.loads(raw);pi_events.add_event(broken['cards']['task'],'execution_failed',1,'fault',
            'fault',{'head':self.head},{'details':'x'*3000},'resolve',2)
        with self.assertRaises(pi_store.BoardOverflow):pi_store._write_board(self.file,broken)
        self.assertEqual(self.file.read_bytes(),raw)
        claim={'schemaVersion':1,'tasks':{'task':{'claims':{'b'*64:{'status':'uncertain','attempts':1,'at':2}},
                                                'lastStatus':'uncertain','failures':[]}}}
        (self.file.parent/'board.queue.json').write_text(json.dumps(claim))
        recovered=pi_recovery.recover_store(self.repo.root)
        self.assertFalse(recovered['resumed']);self.assertEqual(recovered['tasks'],26)
        self.assertEqual(len(self.file.read_bytes())<4096,True)
        b,c=self.load();self.assertTrue(c['paused'])
        event=pi_events.add_event(c,'execution_failed',1,'fault','fault',{'head':self.head},
                                  {'details':'x'*3000},'resolve',2)
        self.save(b)
        b,c=self.load();self.assertEqual(c.pending_count(),1)
        self.assertEqual(review_policy(b['cards']['history-00'])['limit'],1)
        self.assertEqual(review_policy(b['cards']['history-00'])['failedDeliveries'],1)
        self.assertTrue(review_policy(b['cards']['history-00'])['takeoverRequired'])
        self.assertEqual(pi_queue.queue_view(self.file,'task')['uncertain'],1)
        self.assertTrue(pi_recovery.recover_store(self.repo.root)['idempotent'])
        archives=list((self.file.parent/'legacy').glob('board-*.json'))
        self.assertTrue(any(p.read_bytes()==raw for p in archives))

    def test_pending_pages_exact_old_event_decision_replay_and_policy_checkpoint(self):
        c=self.card();self.legacy({'task':c});pi_recovery.recover_store(self.repo.root)
        ids=[]
        for n in range(1,121):
            b,c=self.load()
            event=pi_events.add_event(c,'review_required',n,f'candidate-{n}','review',
                {'head':self.head},{},'review',n);ids.append(event['id']);self.save(b)
        b,c=self.load();self.assertLessEqual(len(c['events']),50);self.assertEqual(c.pending_count(),120)
        seq=[];cursor=0
        while True:
            page=pi_archive.event_page(self.file,'task',cursor,pending=True)
            seq.extend(e['seq'] for e in page['events'])
            if page['nextCursor'] is None:break
            cursor=page['nextCursor']
        self.assertEqual(seq,list(range(1,121)))
        # The middle event is not in either projection window, but is still
        # independently addressable, decidable and immutable on restart.
        decided=pi_board.decide(self.repo.root,'task',ids[59],'changes_requested')
        self.assertEqual(decided['reviewPolicy']['failedDeliveries'],1)
        self.assertTrue(pi_board.decide(self.repo.root,'task',ids[59],'changes_requested')['idempotent'])
        with self.assertRaises(ValueError):pi_board.decide(self.repo.root,'task',ids[59],'reject')
        b,c=self.load();self.assertEqual(review_policy(c)['failedDeliveries'],1)
        self.assertEqual(c.pending_count(),119)
        pi_board.decide(self.repo.root,'task',ids[119],'changes_requested')
        b,c=self.load();self.assertTrue(review_policy(c)['takeoverRequired'])
        self.assertEqual(review_policy(c)['failedDeliveries'],2)

    def test_card_update_and_event_insertion_rollback_together(self):
        self.legacy({'task':self.card()});pi_recovery.recover_store(self.repo.root)
        b,c=self.load();revision=b['revision'];c['paused']=True
        e=pi_events.add_event(c,'execution_failed',1,'fault','fault',{'head':self.head},{},'resolve',2)
        original=pi_archive._save_card
        def fail(db,card):
            original(db,card);raise ValueError('controlled crash before commit')
        with patch('pi_archive._save_card',side_effect=fail):
            with self.assertRaises(ValueError):self.save(b)
        b,c=self.load();self.assertEqual(b['revision'],revision);self.assertFalse(c['paused'])
        self.assertIsNone(pi_events.find_event(c,e['id']))

    def test_recovery_refuses_a_writer_and_keeps_original_byte_for_byte(self):
        self.legacy({'task':self.card()});raw=self.file.read_bytes()
        task=self.repo.task_dir('task');task.mkdir(parents=True)
        fd=os.open(task/'.task.lock',os.O_CREAT|os.O_RDWR,0o600)
        self.addCleanup(os.close,fd);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with self.assertRaises(pi_core.LockHeld):pi_recovery.recover_store(self.repo.root)
        self.assertEqual(self.file.read_bytes(),raw)

    def test_population_failure_and_unpublished_commit_are_both_retryable(self):
        self.legacy({'task':self.card()});raw=self.file.read_bytes()
        with patch('pi_archive._save_card',side_effect=ValueError('injected import failure')):
            with self.assertRaises(ValueError):pi_recovery.recover_store(self.repo.root)
        self.assertEqual(self.file.read_bytes(),raw)
        with patch('pi_archive.os.replace',side_effect=OSError('injected publication failure')):
            with self.assertRaises(OSError):pi_recovery.recover_store(self.repo.root)
        self.assertEqual(self.file.read_bytes(),raw)
        result=pi_recovery.recover_store(self.repo.root)
        self.assertTrue(result['ok']);self.assertEqual(result['tasks'],1)

    def test_pause_during_claim_prevents_external_queue_spawn(self):
        c=self.card();c['transport']='cli-queue';c['codexBin']='/bin/true'
        self.legacy({'task':c});pi_recovery.recover_store(self.repo.root)
        b,c=self.load();pi_events.add_event(c,'review_required',1,'candidate','review',
            {'head':self.head},{},'review',2);self.save(b)
        calls=[];claim=pi_queue._claim_queue
        def pause_after_claim(*args):
            result=claim(*args);pi_board.set_paused(self.repo.root,'task',True);return result
        with patch('pi_queue._claim_queue',side_effect=pause_after_claim), \
                patch('pi_queue.route_paused',return_value=(False,None)):
            result=pi_queue.dispatch_task(self.file,'task',cli_runner=lambda *a:calls.append(a))
        self.assertFalse(result['dispatched']);self.assertEqual(calls,[])
        self.assertTrue(self.load()[1]['paused']);self.assertEqual(self.load()[1].pending_count(),1)

    def test_notification_history_stays_indexed_and_quota_does_not_reset(self):
        c=self.card();c['notify']={'schemaVersion':1,'phases':{}}
        self.legacy({'task':c});pi_recovery.recover_store(self.repo.root)
        for n in range(150):
            b,c=self.load();c['notify']['phases'][f'phase-{n:03}']={'count':2,'lastAt':n,
                'milestones':{f'm-{m}':{'summary':'x'*100} for m in range(20)}};self.save(b)
        b,c=self.load();self.assertEqual(c['notify']['phases']['phase-020']['count'],2)
        self.assertEqual(len(c['notify']['phases']),150)
        with pi_archive.connection(self.file) as db:
            body=db.execute('SELECT body FROM cards WHERE task=?',('task',)).fetchone()[0]
        self.assertLess(len(body),4096)

    def test_unregistered_writer_also_blocks_recovery(self):
        self.legacy({'task':self.card()});raw=self.file.read_bytes()
        task=self.repo.task_dir('not-registered');task.mkdir(parents=True)
        fd=os.open(task/'.task.lock',os.O_CREAT|os.O_RDWR,0o600)
        self.addCleanup(os.close,fd);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with self.assertRaises(pi_core.LockHeld):pi_recovery.recover_store(self.repo.root)
        self.assertEqual(self.file.read_bytes(),raw)

    def test_many_queue_tasks_and_claims_keep_uncertain_state_and_exact_rearm(self):
        c=self.card();c['transport']='cli-queue';c['codexBin']='/bin/true'
        self.legacy({'task':c});pi_recovery.recover_store(self.repo.root)
        q,_=pi_queue.read_queue(self.file)
        for n in range(250):
            q['tasks'][f'task-{n:03}']={'claims':{f'{n:064x}':{'status':'uncertain','at':1}},
                                      'failures':[],'lastStatus':'uncertain'}
        q['tasks']['task']={'claims':{f'{n:064x}':{'status':'queued' if n%2 else 'uncertain','at':1}
                                     for n in range(120)},'failures':[],'lastStatus':'uncertain'}
        pi_queue._write_queue(self.file,q)
        q,_=pi_queue.read_queue(self.file);self.assertEqual(len(q['tasks']),251)
        view=pi_queue.queue_view(self.file,'task');self.assertEqual(view['queued'],60)
        self.assertEqual(view['uncertain'],60);self.assertEqual(view['totalClaims'],120)
        with self.assertRaises(ValueError):pi_queue._clear_queue_claims(self.file,'task')
        pi_queue._clear_queue_claims(self.file,'task',f'{100:064x}')
        self.assertEqual(pi_queue.queue_view(self.file,'task')['uncertain'],59)
        with pi_archive.connection(self.file) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM claim_history').fetchone()[0],1)

    def test_new_card_pages_do_not_rewrite_the_marker_or_other_tasks(self):
        self.legacy({'task':self.card()});pi_recovery.recover_store(self.repo.root)
        marker=self.file.read_bytes()
        for n in range(120):
            b,_c=self.load();name=f'new-{n:03}';b['cards'][name]=self.card(name);self.save(b)
        b,_c=self.load();self.assertEqual(len(b['cards']),121)
        first=b['cards'].page();self.assertEqual(len(first),50)
        second=b['cards'].page(first[-1]);self.assertEqual(len(second),50)
        self.assertEqual(self.file.read_bytes(),marker)


class AdoptionTest(unittest.TestCase):
    def test_expired_phase_keeps_its_anchor_and_cannot_start_a_recovery_round(self):
        from test_phase import PhaseTest
        with tempfile.TemporaryDirectory(prefix='codex-pi-deadline-') as tmp:
            repo=Repo(Path(tmp),config=default_config());wt=repo.worktree('worker')
            try:
                fixture=PhaseTest();fixture.tmp=Path(tmp)
                sha=fixture.write_design(repo)
                contract=fixture.contract(repo,'RECOVERY',design_sha=sha)
                path=Path(tmp)/'contract.json';path.write_text(json.dumps(contract))
                env=base_env(PI_DOUBLE_MODE='connection-error')
                proc=subprocess.run([sys.executable,str(RUNTIME/'pi_task.py'),'start','--repo',str(repo.root),
                    '--task','task','--worktree',str(wt),'--prompt','go','--contract-file',str(path)],
                    env=env,capture_output=True,text=True)
                self.assertEqual(proc.returncode,0,proc.stderr);repo.wait_terminal('task',env=env)
                pi_board.register_task(repo.root,'task',transport='offline')
                task=repo.task_dir('task');state=task/'phase.state.json';data=json.loads(state.read_text())
                data['deadlineAt']=time.time()-1;data['startedAt']=data['deadlineAt']-data['budgetSeconds']
                state.write_text(json.dumps(data));original=state.read_bytes()
                adopted=pi_recovery.adopt_runtime(repo.root,'task',pi_task.HELPER_FILES,RUNTIME)
                self.assertTrue(adopted['budget']['exhausted']);self.assertEqual(state.read_bytes(),original)
                blocked=subprocess.run([sys.executable,str(RUNTIME/'pi_task.py'),'continue','--repo',str(repo.root),
                    '--task','task','--prompt','retry'],env=env,capture_output=True,text=True)
                self.assertNotEqual(blocked.returncode,0);self.assertIn('budget is exhausted',blocked.stderr)
                self.assertEqual(state.read_bytes(),original)
                self.assertFalse((task/'rounds/2').exists())
            finally:cleanup_repos()

    def test_adoption_keeps_task_session_dirty_original_helpers_and_paused_route(self):
        with tempfile.TemporaryDirectory(prefix='codex-pi-adoption-') as tmp:
            repo=Repo(Path(tmp),config=default_config());wt=repo.worktree('worker')
            try:
                env=base_env(PI_DOUBLE_MODE='ok',PI_DOUBLE_TRACE=str(Path(tmp)/'trace.jsonl'))
                repo.start('task',wt,env=env);repo.wait_terminal('task',env=env)
                pi_board.register_task(repo.root,'task',transport='offline')
                task=repo.task_dir('task')
                # A synthetic old-version snapshot, never a real task/cache.
                frozen=json.loads((task/'task.json').read_text());frozen['runtimeVersion']='0.6.0'
                (task/'tools/VERSION').write_text('0.6.0\n')
                frozen['helperHashes']['VERSION']=hashlib.sha256((task/'tools/VERSION').read_bytes()).hexdigest()
                (task/'task.json').write_text(json.dumps(frozen))
                unsafe=subprocess.run([sys.executable,str(RUNTIME/'pi_task.py'),'continue','--repo',str(repo.root),
                    '--task','task','--prompt','go'],env=env,capture_output=True,text=True)
                self.assertNotEqual(unsafe.returncode,0);self.assertIn('adopt-runtime',unsafe.stderr)
                pi_board.set_paused(repo.root,'task',True)
                task=repo.task_dir('task');original=(task/'task.json').read_bytes()
                helpers={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (task/'tools').iterdir() if p.is_file()}
                (wt/'dirty.txt').write_text('keep this\n')
                adopted=pi_recovery.adopt_runtime(repo.root,'task',pi_task.HELPER_FILES,RUNTIME)
                self.assertFalse(adopted['resumed']);self.assertTrue(adopted['paused'])
                self.assertEqual((task/'task.json').read_bytes(),original)
                self.assertEqual((wt/'dirty.txt').read_text(),'keep this\n')
                self.assertEqual(helpers,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (task/'tools').iterdir() if p.is_file()})
                blocked=subprocess.run([sys.executable,str(RUNTIME/'pi_task.py'),'continue','--repo',str(repo.root),
                    '--task','task','--prompt','go'],env=env,capture_output=True,text=True)
                self.assertNotEqual(blocked.returncode,0);self.assertIn('paused',blocked.stderr)
                pi_board.set_paused(repo.root,'task',False)
                proc=subprocess.run([sys.executable,str(RUNTIME/'pi_task.py'),'continue','--repo',str(repo.root),
                    '--task','task','--prompt','go'],env=env,capture_output=True,text=True)
                self.assertEqual(proc.returncode,0,proc.stderr)
                repo.wait_terminal('task',round=2,env=env)
                config=json.loads((task/'rounds/2/worker.json').read_text())
                self.assertEqual(config['toolsDir'],adopted['runtimeTools'])
                trace=[json.loads(line) for line in (Path(tmp)/'trace.jsonl').read_text().splitlines()]
                self.assertEqual(trace[0]['sessionDir'],trace[1]['sessionDir'])
                self.assertEqual(trace[0]['sessionId'],trace[1]['sessionId'])
                self.assertEqual(Path(trace[1]['extension']).parent,Path(adopted['runtimeTools']))
            finally:cleanup_repos()
