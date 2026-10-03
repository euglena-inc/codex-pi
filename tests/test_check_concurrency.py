"""Actual helper subprocesses behind one extension: independent interval/side-effect oracles."""
import json
import shlex
import sys
import time
from unittest.mock import patch
from test_check_budget import BudgetCase
import pi_phase


class ConcurrencyTest(BudgetCase):
    def setUp(self):
        super().setUp()
        self.write_state(remaining=300)
        self.pressure = self.tmp / 'pressure.json'
        self.pressure.write_text('{"state":"normal"}')
        self.env = patch.dict('os.environ', {'CODEX_PI_PRESSURE_SNAPSHOT':str(self.pressure)})
        self.env.start(); self.addCleanup(self.env.stop)
        self.config['checkExecution'] = {'maxConcurrent':2,'cpuSlots':2,'memoryMiB':128}

    def job(self, name, seconds=.25, **resources):
        dest = self.markers / (name+'.json')
        script = (f"import json,time,os,pathlib; p=pathlib.Path({str(dest)!r}); "
                  "t=time.monotonic(); p.write_text(json.dumps({'start':t,'pid':os.getpid()})); "
                  f"time.sleep({seconds}); p.write_text(json.dumps({{'start':t,'end':time.monotonic(),'pid':os.getpid()}}))")
        command = shlex.join([sys.executable,'-c',script])
        r = {'parallelSafe':True,'cpuSlots':1,'memoryMiB':32,**resources}
        self.config['acceptanceItems'].append({'id':name,'command':command,'checkResources':r})
        return {'op':'tool_start','slot':name,'name':'check','params':{'id':name,'command':command,'estimatedSeconds':.05,'timeoutSeconds':5}}

    def interval(self,name):
        return json.loads((self.markers/(name+'.json')).read_text())

    def run_jobs(self, steps):
        names=[s['slot'] for s in steps if s['op']=='tool_start']
        return self.run_steps(steps+[{'op':'tool_await','slot':n} for n in names])[-len(names):]

    def assert_overlap(self,a,b,overlap):
        x,y=self.interval(a),self.interval(b)
        self.assertEqual(min(x['end'],y['end'])>max(x['start'],y['start']),overlap)

    def test_parallel_capacity_cpu_and_memory(self):
        for mode,cap in [('count',None),('cpu',1),('memory',48)]:
            with self.subTest(mode=mode):
                self.config['checkExecution']={'maxConcurrent':2,'cpuSlots':2,'memoryMiB':128}
                if mode=='cpu':self.config['checkExecution']['cpuSlots']=cap
                if mode=='memory':self.config['checkExecution']['memoryMiB']=cap
                a,b=mode+'a',mode+'b';rs=self.run_jobs([self.job(a),self.job(b)])
                self.assertTrue(all(r['structuredContent']['ok'] for r in rs))
                self.assert_overlap(a,b,mode=='count')

    def test_shared_key_and_unknown_declaration_are_exclusive(self):
        a=self.job('keya',exclusiveKeys=['db']);b=self.job('keyb',exclusiveKeys=['db'])
        self.run_jobs([a,b]);self.assert_overlap('keya','keyb',False)
        a=self.job('known');b=self.job('unknown');self.config['acceptanceItems'][-1].pop('checkResources')
        self.run_jobs([a,b]);self.assert_overlap('known','unknown',False)

    def test_unknown_memory_or_missing_bound_degrades_to_serial(self):
        for mode in ('pressure','declaration','pool'):
            with self.subTest(mode=mode):
                self.pressure.write_text(json.dumps({'state':'unknown' if mode=='pressure' else 'normal'}))
                a,b=mode+'a',mode+'b';steps=[self.job(a),self.job(b)]
                if mode=='declaration':
                    for item in self.config['acceptanceItems'][-2:]:item['checkResources'].pop('memoryMiB')
                if mode=='pool':self.config['checkExecution'].pop('memoryMiB')
                rs=self.run_jobs(steps);self.assertTrue(all(r['structuredContent']['ok'] for r in rs))
                self.assert_overlap(a,b,False)

    def test_oversized_and_pressure_refuse_without_spawn(self):
        for label,state,resources in [('large','normal',{'memoryMiB':999}),('pressure','high',{}),
                                      ('linux','normal',{})]:
            self.pressure.write_text(json.dumps({'state':state,**({'availableMiB':30,'thresholdMiB':10} if label=='linux' else {})}))
            r=self.run_jobs([self.job(label,**resources)])[0]['structuredContent']
            self.assertFalse(r['ok']);self.assertIsNone(r['receipt']);self.assertFalse((self.markers/(label+'.json')).exists())

    def test_fifo_exclusive_cannot_be_overtaken(self):
        a=self.job('first');b=self.job('exclusive',parallelSafe=False);c=self.job('last')
        self.run_jobs([a,b,c]);self.assertGreaterEqual(self.interval('exclusive')['start'],self.interval('first')['end'])
        self.assertGreaterEqual(self.interval('last')['start'],self.interval('exclusive')['end'])

    def test_cancel_waiter_does_not_spawn_or_leak_permit(self):
        a=self.job('holder',.5,parallelSafe=False);b=self.job('cancel');c=self.job('after')
        results=self.run_jobs([a,{'op':'wait_file','path':str(self.markers/'holder.json')},b,
                              {'op':'sleep','ms':40},{'op':'tool_abort','slot':'cancel'},c])
        self.assertTrue(results[1]['structuredContent']['cancelled'])
        self.assertFalse((self.markers/'cancel.json').exists());self.assertTrue(results[2]['structuredContent']['ok'])

    def test_waiting_deadline_expires_before_holder_finishes(self):
        self.write_state(remaining=61.1)
        a=self.job('holder',2,parallelSafe=False);b=self.job('expire')
        r=self.run_jobs([a,{'op':'wait_file','path':str(self.markers/'holder.json')},b])[1]['structuredContent']
        self.assertFalse(r['ok']);self.assertIn('budget',r['reason']);self.assertLess(r.get('queueSeconds',0),1.5)
        self.assertFalse((self.markers/'expire.json').exists())

    def test_dirty_candidate_and_pressure_rechecked_after_wait(self):
        for mode in ('dirty','pressure'):
            a=self.job(mode+'holder',.35,parallelSafe=False);b=self.job(mode+'waiting')
            if mode=='dirty':
                self.config['acceptanceItems'][-1]['targetedCommand']='true';b['params']['final']=True
                mutation={'op':'file_write','path':str(self.worktree/'dirty'),'text':'changed'}
            else:mutation={'op':'file_write','path':str(self.pressure),'text':'{"state":"high"}'}
            r=self.run_jobs([a,{'op':'wait_file','path':str(self.markers/(mode+'holder.json'))},b,
                            {'op':'sleep','ms':30},mutation])[1]['structuredContent']
            self.assertFalse(r['ok']);self.assertIsNone(r['receipt']);self.assertFalse((self.markers/(mode+'waiting.json')).exists())
            (self.worktree/'dirty').unlink(missing_ok=True)
            self.pressure.write_text('{"state":"normal"}')

    def test_contract_fields_validate_and_preserve_absence(self):
        c=self.contract([self.item()]);old=pi_phase.validate_contract(c,self.main)
        self.assertNotIn('checkExecution',old)
        c['checkExecution']={'maxConcurrent':2,'cpuSlots':2,'memoryMiB':128}
        c['acceptanceItems'][0]['checkResources']={'parallelSafe':True,'cpuSlots':1,'memoryMiB':32}
        self.assertEqual(pi_phase.validate_contract(c,self.main)['checkExecution']['maxConcurrent'],2)
        c['checkExecution']['maxConcurrent']=5
        with self.assertRaises(ValueError):pi_phase.validate_contract(c,self.main)

    def test_same_argv_cannot_hide_conflicting_resources_under_another_id(self):
        items=[self.item(id='A',command='python3 -c pass',checkResources={'parallelSafe':True,'cpuSlots':1,'memoryMiB':32}),
               self.item(id='B',command='python3  -c pass',checkResources={'parallelSafe':True,'cpuSlots':1,'memoryMiB':64})]
        with self.assertRaises(ValueError):pi_phase.validate_contract(self.contract(items),self.main)
