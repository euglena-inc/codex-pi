"""Independent assertions over real SDK observations (no model/network)."""
import unittest


def maximum_overlap(records):
    events=[(r['start'],1) for r in records]+[(r['end'],-1) for r in records]
    active=peak=0
    for _,change in sorted(events):
        active+=change;peak=max(peak,active)
    return peak


def active_peak_bound(records):
    events=[(r['start'],r['peakBytes']) for r in records]+[(r['end'],-r['peakBytes']) for r in records]
    active=peak=0
    for _,change in sorted(events):
        active+=change;peak=max(peak,active)
    return peak


class ConcurrencyIntegrationTest(unittest.TestCase):
    report=None
    layout=None

    def scenario(self,name):
        self.assertIsNone(self.report['cases'][name]['error'])
        return self.report['concurrency']['scenarios'][name]

    def test_parallel_1_2_4_actual_subprocess_overlap_and_same_results(self):
        digests=set()
        for width in (1,2,4):
            s=self.scenario(f'parallel-{width}')
            self.assertEqual(len(s['records']),4)
            self.assertEqual(s['live'],0)
            peak=maximum_overlap(s['records'])
            self.assertLessEqual(peak,width)
            if width>1 and self.report['concurrency']['cpus']>1:self.assertGreaterEqual(peak,2)
            digests.update(r['digest'] for r in s['records'])
            self.assertTrue(all(r['peakBytes']>32*1024*1024 for r in s['records']))
        self.assertEqual(len(digests),1)

    def test_shared_resource_and_unknown_pressure_are_serial(self):
        for name in ('shared','unknown'):
            s=self.scenario(name);self.assertEqual(len(s['records']),4)
            self.assertEqual(maximum_overlap(s['records']),1)
            self.assertEqual(s['live'],0)

    def test_pressure_and_oversize_never_spawn(self):
        for name,reason in [('pressure','memory_pressure'),('capacity','resource_exceeds_pool')]:
            s=self.scenario(name);self.assertEqual(s['started'],0)
            text='\n'.join(r['text'] for r in self.report['cases'][name]['codemode'])
            self.assertIn(reason,text);self.assertIn('"receipt":null',text)

    def test_cancel_stops_owned_child_and_never_starts_waiter(self):
        s=self.scenario('cancel');self.assertEqual(s['started'],1)
        self.assertEqual(s['live'],0);self.assertEqual(s['records'],[])

    def test_waiting_budget_and_dirty_candidate_rechecked_in_native_pipeline(self):
        s=self.scenario('deadline')
        self.assertGreaterEqual(s['started'],1);self.assertLess(s['started'],4)
        self.assertEqual(s['live'],0)
        text='\n'.join(r['text'] for r in self.report['cases']['deadline']['codemode'])
        self.assertIn('insufficient_budget',text)
        s=self.scenario('dirty');self.assertEqual(s['started'],1);self.assertEqual(s['live'],0)
        text='\n'.join(r['text'] for r in self.report['cases']['dirty']['codemode'])
        self.assertIn('dirty_final',text)

    @classmethod
    def measurements(cls):
        return {'kind':'same-workload-concurrency','timing':'SDK prompt wall; scripted provider, not paid model',
                'rss':'overlap-weighted sum of per-child peak RSS: upper bound, not host/VM measurement',
                'rows':[{'width':w,'wallSeconds':cls.report['cases'][f'parallel-{w}']['elapsedMs']/1000,
                         'maxOverlap':maximum_overlap(cls.report['concurrency']['scenarios'][f'parallel-{w}']['records']),
                         'concurrentChildPeakUpperMiB':active_peak_bound(cls.report['concurrency']['scenarios'][f'parallel-{w}']['records'])/2**20,
                         'sumChildCpuSeconds':sum(r['cpuSeconds'] for r in cls.report['concurrency']['scenarios'][f'parallel-{w}']['records'])}
                        for w in (1,2,4)]}
