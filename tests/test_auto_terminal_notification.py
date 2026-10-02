import sys
import unittest
from runtime_helpers import RUNTIME
sys.path.insert(0,str(RUNTIME))
import pi_board

class AutoTerminalNotificationTest(unittest.TestCase):
    def case(self,round_number=2,state='completed'):
        card=pi_board._new_card('task','11111111-2222-3333-4444-555555555555','title','goal',None,None,'/tmp/repo','/tmp/state','/tmp/worktree','offline',None,1)
        status={'round':round_number,'state':state,'recordedState':state,'currentHead':'b'*40,'phase':{'phaseId':'P','contractSha256':'a'*64,'autoContinue':{'status':'started','round':2},'readiness':{'status':'not_ready','coverage':{'required':4,'covered':3,'failed':1},'scope':'ok','resource':{'status':'ok'},'execution':{'status':'ok'},'items':[{'status':'failed'}]}}}
        return card,status
    def test_finished_auto_round_cannot_suppress_terminal_failure(self):
        card,status=self.case();events=pi_board._project_phase_events(card,status,2)
        self.assertEqual([e['kind'] for e in events],['phase_blocked'])
        self.assertEqual(pi_board._project_phase_events(card,status,3),[])
    def test_future_auto_round_still_suppresses_predecessor_wakeup(self):
        card,status=self.case(round_number=1);self.assertEqual(pi_board._project_phase_events(card,status,2),[])
    def test_active_auto_round_does_not_emit_terminal_failure(self):
        card,status=self.case(state='running');self.assertFalse(any(e['kind']=='phase_blocked' for e in pi_board._project_phase_events(card,status,2)))

if __name__=='__main__':unittest.main()
