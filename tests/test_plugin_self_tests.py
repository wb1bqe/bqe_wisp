"""Regression checks for the diagnostics runner, not part of the user allowlist."""
import json
import ast
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
from plugins import self_tests as runner


class RunnerTests(unittest.TestCase):
    def test_active_pass_skips_without_starting_worker(self):
        with patch.object(runner.subprocess,'Popen') as popen:
            result=runner.run_suite('sdr',lambda:True)
            self.assertEqual(result['status'],'warning')
            popen.assert_not_called()

    def test_pass_beginning_kills_only_test_worker(self):
        child=Mock();child.poll.return_value=None
        with patch.object(runner.subprocess,'Popen',return_value=child):
            result=runner.run_suite('sdr',Mock(side_effect=[False,True]))
        self.assertEqual(result['status'],'warning')
        child.kill.assert_called_once();child.wait.assert_called_once()

    def test_timeout_is_failure_and_reaps_worker(self):
        child=Mock();child.poll.return_value=None
        with patch.object(runner.subprocess,'Popen',return_value=child), \
             patch.object(runner.time,'monotonic',side_effect=[0,21]):
            result=runner.run_suite('sdr')
        self.assertEqual(result['status'],'failed')
        self.assertIn('Timed out',result['message'])
        child.kill.assert_called_once();child.wait.assert_called_once()

    def test_missing_runtime_is_warning(self):
        with patch.object(runner.subprocess,'Popen',side_effect=FileNotFoundError('missing python')):
            self.assertEqual(runner.run_suite('sdr')['status'],'warning')

    def test_worker_result_and_runtime_are_retained(self):
        def launch(args,**kwargs):
            Path(args[-1]).write_text(json.dumps(dict(status='passed',tests=4,message='four passed')))
            self.assertEqual(kwargs['env']['PYTHONDONTWRITEBYTECODE'],'1')
            self.assertEqual(kwargs['env']['OMP_NUM_THREADS'],'1')
            return Mock(poll=Mock(return_value=0),returncode=0)
        with patch.object(runner.subprocess,'Popen',side_effect=launch):
            result=runner.run_suite('sdr')
        self.assertEqual(result['tests'],4)
        self.assertIn('python',result['runtime'].lower())

    def test_crash_is_not_reported_as_pass(self):
        with patch.object(runner.subprocess,'Popen',return_value=Mock(poll=Mock(return_value=2),returncode=2)):
            self.assertEqual(runner.run_suite('sdr')['status'],'failed')

    def test_missing_dependency_is_warning_not_failure(self):
        with patch.object(runner,'install_guards'), patch.object(runner.importlib,'import_module',side_effect=ModuleNotFoundError('scipy')):
            result=runner.run_child('sdr','unused')
        self.assertEqual(result['status'],'warning')
        self.assertEqual(result['tests'],0)

    def test_dependency_error_and_assertion_failure_are_distinct(self):
        class Missing(unittest.TestCase):
            def runTest(self): raise ModuleNotFoundError('missing optional package', name='optional_package')
        class Broken(unittest.TestCase):
            def runTest(self): self.assertTrue(False,'broken decoder')
        for cases,expected in [([Missing()],'warning'),([Missing(),Broken()],'failed')]:
            with patch.object(runner,'install_guards'), patch.object(runner.importlib,'import_module'), \
                 patch.object(runner.importlib.util,'spec_from_file_location',return_value=Mock()), \
                 patch.object(runner.importlib.util,'module_from_spec',return_value=types.ModuleType('fake')), \
                 patch.object(runner.unittest.TestLoader,'loadTestsFromNames',return_value=unittest.TestSuite(cases)):
                result=runner.run_child('sdr','unused')
            self.assertEqual(result['status'],expected)

    def test_duplicate_requests_do_not_start_tests(self):
        runner.RUN_LOCK.acquire()
        try:
            with patch.object(runner,'run_suite') as suite:
                self.assertIn('[WARNING]',runner.run_plugin_self_tests())
                suite.assert_not_called()
        finally:runner.RUN_LOCK.release()

    def test_report_has_distinct_failure_and_warning_markers(self):
        with patch.object(runner,'SUITES',{'a':('A',),'b':('B',)}), patch.object(runner,'run_suite',side_effect=[
                dict(status='warning',message='dependency missing'),dict(status='failed',message='bad result')]):
            report=runner.run_plugin_self_tests()
        self.assertIn('[WARNING] A:',report);self.assertIn('[FAIL] B:',report)

    def test_web_command_opt_in_and_report_counts(self):
        source=(runner.ROOT/'bqe_wisp.py').read_text(encoding='utf-8')
        function=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='handle_web_command')
        diagnostic=Mock(return_value='[WARNING] dependency missing\n[FAIL] bad test\n')
        ns={'run_environment_diagnostics':diagnostic}
        exec(compile(ast.Module(body=[function],type_ignores=[]),'webcommand','exec'),ns)
        for payload,expected in [({},False),({'include_plugin_tests':'true'},False),({'include_plugin_tests':True},True)]:
            result=ns['handle_web_command']('environment_diagnostics',payload)
            diagnostic.assert_called_with(emit=False,include_plugin_tests=expected)
            self.assertEqual((result['warnings'],result['failures']),(1,1))
            self.assertTrue(result['ok'])


if __name__=='__main__':unittest.main()
