"""Exercise real tracker SDR hooks without importing astronomy/hardware startup."""
import ast
from pathlib import Path
import unittest
from unittest.mock import Mock


class TrackerTests(unittest.TestCase):
    def test_sdr_only_skips_physical_radio_and_receives_doppler(self):
        path=Path(__file__).resolve().parents[3]/'bqe_track_continuously.py'
        tree=ast.parse(path.read_text(encoding='utf-8'))
        main=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
        guard=next(n for n in ast.walk(main) if isinstance(n,ast.If)
                   and isinstance(n.test,ast.UnaryOp) and isinstance(n.test.operand,ast.Name)
                   and n.test.operand.id=='sdr_only')
        tune=next(n for n in ast.walk(main) if isinstance(n,ast.If)
                  and isinstance(n.test,ast.BoolOp)
                  and any(isinstance(v,ast.Name) and v.id=='use_sdr' for v in n.test.values))
        radio=Mock();sdr=Mock()
        ns=dict(sdr_only=True,use_sdr=True,enable_tuning=True,SDR_RECEIVER=sdr,
                downlink_freq_with_doppler_hz=145804321,start_rigctld_process=radio)
        exec(compile(ast.Module(body=[guard,tune],type_ignores=[]),str(path),'exec'),ns)
        radio.assert_not_called()
        sdr.tune.assert_called_once_with(145804321)
        ns['enable_tuning']=False;sdr.reset_mock()
        exec(compile(ast.Module(body=[tune],type_ignores=[]),str(path),'exec'),ns)
        sdr.tune.assert_not_called()

    def test_sdr_only_skips_satellite_setup_squelch_and_agc(self):
        path=Path(__file__).resolve().parents[3]/'bqe_track_continuously.py'
        tree=ast.parse(path.read_text(encoding='utf-8'))
        startup=next(n for n in ast.walk(tree) if isinstance(n,ast.Try) and
                     any(isinstance(c,ast.If) and any(isinstance(t,ast.Name) and t.id=='satellite_mode_required'
                         for t in ast.walk(c.test)) for c in n.body))
        index=next(i for i,c in enumerate(startup.body) if isinstance(c,ast.If) and
                   any(isinstance(t,ast.Name) and t.id=='satellite_mode_required' for t in ast.walk(c.test)))
        radio=Mock();setup=Mock()
        ns=dict(sdr_only=True,satellite_mode_required=True,rig=radio,do_satellite_setup=setup,
                squelch_level=.5,agc='fast')
        exec(compile(ast.Module(body=startup.body[index:index+3],type_ignores=[]),str(path),'exec'),ns)
        setup.assert_not_called()
        radio.rigctld_set_squelch_level.assert_not_called()
        radio.rigctld_set_agc.assert_not_called()


if __name__=='__main__':unittest.main()
