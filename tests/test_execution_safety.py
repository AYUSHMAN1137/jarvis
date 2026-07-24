import threading
import unittest

from app.services.agent import action_sink
from app.services.agent.execution import ExecutionContext, ExecutionCoordinator, trusted_confirmation_for
from app.services.agent.tool_registry import ToolRegistry, ToolSpec


class NullMemory:
    def record_action(self, *args, **kwargs): pass


class NullPhase4:
    def publish_action_done(self, *args, **kwargs): pass
    def publish_execution_completed(self, *args, **kwargs): pass


class ExecutionSafetyTests(unittest.TestCase):
    def test_frontend_dispatch_has_correlation(self):
        reg = ToolRegistry()
        def frontend():
            action_sink.add_open('https://example.com')
            return 'queued'
        reg.register(ToolSpec('frontend', 'test', frontend, category='web'))
        action_sink.reset()
        c = ExecutionCoordinator(reg, NullMemory(), NullPhase4())
        ctx = ExecutionContext('open', 'direct')
        action = c.action('frontend', {})
        result = c.execute_action(ctx, action, confirmation_already_checked=True)
        meta = result.frontend_actions['_meta']
        self.assertEqual(meta['execution_id'], ctx.execution_id)
        self.assertEqual(meta['action_id'], action.action_id)
        self.assertTrue(meta['dispatch_id'])

    def test_confirmation_scope_exists_only_during_exact_execution(self):
        seen = []
        reg = ToolRegistry()
        reg.register(ToolSpec('danger', 'test', lambda: seen.append(trusted_confirmation_for('danger')) or 'ok',
                              dangerous=True))
        c = ExecutionCoordinator(reg, NullMemory(), NullPhase4())
        ctx = ExecutionContext('do', 'confirmed_action')
        c.execute_action(ctx, c.action('danger', {}), confirmation_already_checked=True)
        self.assertEqual(seen, [True])
        self.assertFalse(trusted_confirmation_for('danger'))

    def test_action_sink_is_thread_isolated(self):
        results = {}
        def worker(key, url):
            action_sink.reset(); action_sink.add_open(url); results[key] = action_sink.collect()['wopens']
        threads = [threading.Thread(target=worker, args=('a', 'https://a.example')),
                   threading.Thread(target=worker, args=('b', 'https://b.example'))]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(results['a'], ['https://a.example'])
        self.assertEqual(results['b'], ['https://b.example'])


if __name__ == '__main__': unittest.main()
