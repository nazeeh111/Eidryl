"""Native transfer tests and meaningful failure controls, with adapters outside the engine."""
from __future__ import annotations
import collections
import copy
import io
import json
import unittest
from unittest import mock
from eidryl import engine


def surface(identity, constructor_types, constructor_samples, methods, **options):
    return {'schema': 'eidryl-surface/1', 'id': identity, 'constructor_types': constructor_types, 'constructor_samples': constructor_samples, 'actions': {name: {'path': path, 'types': types} for name, path, types in methods}, **options}


TEXT = surface('native-text', ['str'], [[s] for s in ['', 'A', 'alpha', 'βeta', '🙂Z', '漢字', 'a\nb', 'é\u0301']], [('a1', 'write', ['str']), ('a2', 'read', ['int']), ('a3', 'seek', ['int']), ('a4', 'getvalue', []), ('a5', 'tell', [])])
SEQUENCE = surface('native-sequence', ['list', 'int'], [[xs, n] for xs in [[], ['A'], ['one', 'βeta', '🧭', 'four']] for n in [-1, 0, 1, 3, 5]], [('s1', 'append', ['str']), ('s2', 'extend', ['list']), ('s3', 'pop', []), ('s4', 'popleft', []), ('s5', 'reverse', []), ('s6', 'rotate', ['int'])])
ENCODED = surface('native-encoded', [], [[]] * 8, [('e1', 'write', ['str']), ('e2', 'buffer.seek', ['int']), ('e3', 'buffer.truncate', ['int']), ('e4', 'seek', ['int']), ('e5', 'buffer.getvalue', []), ('e6', 'tell', [])])


def encoded_factory():
    return io.TextIOWrapper(io.BytesIO(), encoding='utf-16', write_through=True)


def sequence_factory(values, capacity):
    return collections.deque(values, maxlen=capacity)


def predict(model, constructor, actions):
    row = {'s': [], 'a': [], 'c': constructor}
    if engine.evaluate(model['constructor_guard'], row) is not True:
        raise engine.Refusal('Constructor outside learned domain')
    state = [engine.evaluate(expr, row) for expr in model['initial']]
    mode = model['initial_mode']
    result = []
    for action, args in actions:
        rule = model['rules'][action].get(str(mode))
        if rule is None:
            raise engine.Refusal('Unsupported mode')
        row = {'s': state, 'a': args, 'c': constructor}
        if engine.evaluate(rule['guard'], row) is not True:
            result.append({'accepted': False, 'state': engine._dump(state)})
            continue
        value = engine.evaluate(rule['return'], row)
        next_state = [engine.evaluate(expr, row) for expr in rule['state']]
        mode = engine.evaluate(rule['mode'], row)
        state = next_state
        result.append({'accepted': True, 'value': engine.pack(value), 'state': engine._dump(state)})
    return result


class FailureControls(unittest.TestCase):
    def test_cyclic_argument_mutation_is_capture_refusal_not_native_error(self):
        class Cyclic:
            value = 0
            def change(self, values):
                values.append(values)
                return 'native-success'
        spec = surface('cyclic-input', [], [[]], [('q', 'change', ['list'])])
        actions = [['q', [['caller-safe']]]]
        with self.assertRaises(engine.Refusal) as failure:
            engine.observe(Cyclic, spec, [], actions, fields=['value'])
        self.assertEqual(actions, [['q', [['caller-safe']]]])
        self.assertEqual(failure.exception.evidence['input_violations'][0]['after'], '<nonportable argument mutation>')
        self.assertNotIn('trace', failure.exception.evidence)

    def test_spec_strictness_before_factory_execution(self):
        invoked = []
        def factory(value):
            invoked.append(value)
            return io.StringIO(value)
        for alteration in [{'extra': True}, {'actions': {'bad': {'path': '_private', 'types': []}}}, {'constructor_samples': [[True]]}, {'prefix_depth': True}]:
            spec = copy.deepcopy(TEXT)
            spec.update(alteration)
            with self.subTest(alteration=alteration), self.assertRaises(engine.Refusal):
                engine.learn(factory, spec)
        self.assertEqual(invoked, [])

    def test_probe_width_refuses_before_factory_and_product(self):
        invoked = []
        def factory():
            invoked.append(True)
            return object()
        wide = surface('wide', [], [[]], [('go', 'apply', ['str'] * 8)], text_samples=[str(i) for i in range(16)])
        with mock.patch.object(engine.itertools, 'product', side_effect=AssertionError('Unsafe product enumeration')) as product:
            for operation in [lambda: engine.learn(factory, wide), lambda: engine.observe(factory, wide, [], [], fields=['value'])]:
                with self.assertRaisesRegex(engine.Refusal, 'probe width'):
                    operation()
            self.assertEqual(product.call_count, 0)
        self.assertEqual(invoked, [])
        # Boundary probes can be wider than a one-value caller sample corpus.
        boundary_wide = surface('boundary-wide', [], [[]], [('go', 'apply', ['int'] * 8)], int_samples=[17])
        with mock.patch.object(engine.itertools, 'product', side_effect=AssertionError('Unsafe product enumeration')) as product:
            with self.assertRaisesRegex(engine.Refusal, 'probe width'):
                engine.learn(factory, boundary_wide)
            self.assertEqual(product.call_count, 0)
        self.assertEqual(invoked, [])

    def test_allowed_probe_width_and_native_specs_are_unchanged(self):
        allowed = surface('allowed', [], [[]], [('go', 'apply', ['str'] * 7)], text_samples=['A', 'B'])
        experiment = engine._Experiment(lambda: object(), allowed, 100)
        self.assertEqual(len(experiment.samples(['str'] * 7)), 128)
        for spec in [TEXT, SEQUENCE, ENCODED]:
            old_normalized = copy.deepcopy(spec)
            old_normalized.setdefault('text_samples', ['', 'A', 'alpha', 'βeta', '🙂Z', '漢字', 'a\nb', 'é\u0301'])
            old_normalized.setdefault('int_samples', [-2, -1, 0, 1, 2, 3, 5, 9, 17])
            old_normalized.setdefault('list_samples', [[], ['A'], ['βeta', '🙂Z'], ['alpha', '', '漢字']])
            old_normalized.setdefault('prefix_depth', 3)
            self.assertEqual(engine.validate_spec(spec), old_normalized)

    def test_budget_refusal_is_sticky_and_measured(self):
        with self.assertRaises(engine.Refusal) as failed:
            engine.learn(io.StringIO, TEXT, query_budget=1)
        self.assertTrue(failed.exception.evidence['exhausted'])
        self.assertGreater(failed.exception.evidence['charged_operations'], 1)
        self.assertNotIn('model', failed.exception.evidence)

    def test_factory_reuse_is_refused(self):
        obj = io.StringIO('original')
        with self.assertRaises(engine.Refusal):
            engine.learn(lambda ignored: obj, TEXT, query_budget=20000)

    def test_failed_mutation_is_retained_and_refused(self):
        class Counter:
            def __init__(self, value):
                self.value = value
            def break_it(self):
                self.value += 1
                raise ValueError('changed first')
            def read(self):
                return self.value
        spec = surface('failed-mutation', ['int'], [[0], [2]], [('z1', 'break_it', []), ('z2', 'read', [])])
        with self.assertRaises(engine.Refusal) as failed:
            engine.learn(Counter, spec, query_budget=20000)
        self.assertIn('failed operation changed', str(failed.exception).lower())
        self.assertTrue(any(not r['ok'] and r['s'] != r['y'][1:] for r in failed.exception.evidence['records']))

    def test_hidden_failed_mutation_is_refused(self):
        class Hidden:
            def __init__(self, value):
                self.payload = value
                self._flag = False
            def fail(self):
                self._flag = not self._flag
                raise ValueError('hidden change')
            def add(self, value):
                answer = ('changed:' if self._flag else 'same:') + value
                self.payload += value
                return answer
        spec = surface('hidden-failure', ['str'], [['a'], ['bb']], [('r1', 'fail', []), ('r2', 'add', ['str'])])
        with self.assertRaises(engine.Refusal):
            engine.learn(Hidden, spec, query_budget=40000)

    def test_nonportable_list_elements_refuse_observation_and_learning(self):
        spec = surface('list-output', [], [[]], [('grow', 'append', ['str'])])
        for value in ['\ud800', '\udfff', 'x' * 1048577]:
            factory = lambda: collections.deque([value])
            with self.subTest(value_length=len(value)), self.assertRaises(engine.Refusal):
                engine.observe(factory, spec, [], [], fields=['iteration'])
            with self.subTest(learning_value_length=len(value)), self.assertRaises(engine.Refusal):
                engine.learn(factory, spec, query_budget=20000)
        # A malformed element appearing after an operation must also be refused.
        class LateList:
            def __init__(self):
                self.values = ['validΩ']
            def change(self, value):
                self.values = ['\ud800']
        late_spec = surface('late-list', [], [[]], [('change', 'change', ['str'])])
        with self.assertRaises(engine.Refusal):
            engine.observe(LateList, late_spec, [], [['change', ['owned']]], fields=['values'])

    def test_valid_unicode_list_outputs_remain_portable(self):
        spec = surface('valid-list-output', [], [[]], [('grow', 'append', ['str'])])
        original = ['Ω', '🧭', '𐐀', 'e\u0301']
        factory = lambda: collections.deque(original)
        model = engine.learn(factory, spec, query_budget=20000)
        native = engine.observe(factory, spec, [], [['grow', ['fresh𝄞']]], fields=model['fields'])
        inferred = predict(model, [], [['grow', ['fresh𝄞']]])
        self.assertTrue(engine._same_json(native['trace'][0]['state'], inferred[0]['state']))
        self.assertTrue(engine._same_json(original + ['fresh𝄞'], native['trace'][0]['state'][0]))

    def test_mutable_public_lists_are_captured_as_detached_history(self):
        class Bag:
            def __init__(self, values):
                self.items = list(values)
            def add(self, value):
                self.items.append(value)
                return self.items
            def peek(self):
                return self.items
        spec = surface('mutable-list', ['list'], [[['seed']], [['β', '🧭']], [[]]], [('grow', 'add', ['str']), ('peek', 'peek', [])])
        actions = [['grow', ['A']], ['grow', ['B']], ['peek', []]]
        native = engine.observe(Bag, spec, [['seed']], actions, fields=['items'])
        self.assertEqual(native['initial'], [['seed']])
        self.assertEqual(native['trace'][0]['before'], [['seed']])
        self.assertEqual(native['trace'][0]['state'], [['seed', 'A']])
        self.assertEqual(native['trace'][0]['value'], ['seed', 'A'])
        self.assertEqual(native['trace'][1]['before'], [['seed', 'A']])
        model = engine.learn(Bag, spec, query_budget=20000)
        inferred = predict(model, [['unseenΩ']], [['grow', ['𐐀']], ['grow', ['𝄞']], ['peek', []]])
        measured = engine.observe(Bag, spec, [['unseenΩ']], [['grow', ['𐐀']], ['grow', ['𝄞']], ['peek', []]], fields=model['fields'])
        for actual, expected in zip(measured['trace'], inferred):
            self.assertTrue(engine._same_json(actual['state'], expected['state']))
            self.assertTrue(engine._same_json(actual['value'], expected['value']))
        class FailingBag(Bag):
            def add(self, value):
                self.items.append(value)
                raise ValueError('changed list first')
        with self.assertRaisesRegex(engine.Refusal, 'failed operation changed'):
            engine.learn(FailingBag, spec, query_budget=20000)

    def test_constructor_and_action_input_lists_are_isolated(self):
        class HoldingBag:
            def __init__(self, values):
                self.items = values
            def add(self, value):
                self.items.append(value)
                return self.items
        spec = surface('holding-bag', ['list'], [[['seed']]], [('grow', 'add', ['str'])])
        original_spec = copy.deepcopy(spec)
        supplied = ['seed']
        captured = engine.observe(HoldingBag, spec, [supplied], [['grow', ['new']]], fields=['items'])
        self.assertEqual(supplied, ['seed'])
        self.assertEqual(spec, original_spec)
        self.assertEqual(captured['initial'], [['seed']])
        self.assertEqual(captured['trace'][0]['state'], [['seed', 'new']])
        class MutatingFactory:
            def __init__(self, values):
                values.pop()
                self.items = values
        with self.assertRaises(engine.Refusal) as refusal:
            engine.observe(MutatingFactory, spec, [supplied], [], fields=['items'])
        self.assertEqual(supplied, ['seed'])
        self.assertEqual(refusal.exception.evidence['input_violations'][0]['operation'], 'constructor')
        with self.assertRaises(engine.Refusal):
            engine.learn(MutatingFactory, spec, query_budget=20000)
        self.assertEqual(spec, original_spec)
        class ConsumingAPI:
            def __init__(self):
                self.items = []
            def consume(self, values):
                self.items.extend(values)
                values.clear()
        consuming = surface('consuming', [], [[]], [('consume', 'consume', ['list'])])
        values = ['ownedΩ']
        request = [['consume', [values]]]
        with self.assertRaises(engine.Refusal) as refusal:
            engine.observe(ConsumingAPI, consuming, [], request, fields=['items'])
        self.assertEqual(values, ['ownedΩ'])
        self.assertEqual(request, [['consume', [['ownedΩ']]]])
        self.assertEqual(refusal.exception.evidence['input_violations'][0]['before'], [['ownedΩ']])
        self.assertEqual(refusal.exception.evidence['input_violations'][0]['after'], [[]])
        with self.assertRaises(engine.Refusal):
            engine.learn(ConsumingAPI, consuming, query_budget=20000)
        self.assertEqual(engine.validate_spec(TEXT)['list_samples'], [[], ['A'], ['βeta', '🙂Z'], ['alpha', '', '漢字']])

    def test_integral_json_inputs_are_canonicalized(self):
        spec = surface('canonical', ['int'], [[3.0]], [('go', 'seek', ['int'])], int_samples=[0.0, 1.0])
        normalized = engine.validate_spec(spec)
        self.assertEqual(type(normalized['constructor_samples'][0][0]), int)
        obj = io.StringIO('abcd')
        engine.call(obj, normalized, 'go', [2.0])
        self.assertEqual(obj.tell(), 2)
        for bad in [True, 2.5, float('inf'), float('nan'), 10 ** 1000]:
            with self.subTest(value=str(bad)[:20]), self.assertRaises(engine.Refusal):
                engine.call(obj, normalized, 'go', [bad])
        self.assertEqual(obj.tell(), 2)


class NativeTransfer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = {}
        for name, factory, spec in [('text', io.StringIO, TEXT), ('sequence', sequence_factory, SEQUENCE), ('encoded', encoded_factory, ENCODED)]:
            print('native-learning-start ' + name, flush=True)
            cls.results[name] = engine.learn_with_evidence(factory, spec, query_budget=150000)
            print('native-learning-complete ' + name + ' ' + str(cls.results[name]['evidence']['charged_operations']), flush=True)

    def assert_native(self, name, factory, spec, constructor, actions):
        model = self.results[name]['model']
        actual = engine.observe(factory, spec, constructor, actions, fields=model['fields'], query_budget=20000)
        predicted = predict(model, constructor, actions)
        self.assertEqual(len(actual['trace']), len(predicted))
        for measured, inferred in zip(actual['trace'], predicted):
            self.assertEqual(type(inferred['accepted']), bool)
            self.assertEqual(measured['accepted'], inferred['accepted'])
            self.assertTrue(engine._same_json(measured['state'], inferred['state']))
            if measured['accepted']:
                self.assertTrue(engine._same_json(measured['value'], inferred['value']))

    def test_text_unseen_data_sparse_and_beyond_end_negative_read(self):
        actions = [['a3', [713]], ['a2', [-37]], ['a5', []], ['a1', ['fresh𐐀Ω']], ['a3', [3]], ['a2', [19]], ['a3', [-19]], ['a4', []]]
        self.assert_native('text', io.StringIO, TEXT, ['independent🧭e\u0301' * 17], actions)

    def test_sequence_unseen_capacity_and_rotation(self):
        actions = [['s2', [['new' + str(i) + '🛸' for i in range(23)]]], ['s6', [181]], ['s5', []], ['s6', [-127]], ['s3', []]] + [['s4', []]] * 21
        self.assert_native('sequence', sequence_factory, SEQUENCE, [['initialΩ'], 19], actions)

    def test_hidden_mode_nonzero_cursor_retained_suffix_regressions(self):
        self.assertEqual(self.results['encoded']['model']['latent_modes'], 2)
        for cursor in [1, 3, 8, 19]:
            for payload in ['', 'Ω', 'native🧭Q']:
                actions = [['e1', ['review-I🌋1abcd' * 7]], ['e4', [0]], ['e2', [cursor]], ['e1', [payload]], ['e5', []]]
                with self.subTest(cursor=cursor, payload=payload):
                    self.assert_native('encoded', encoded_factory, ENCODED, [], actions)

    def test_mode_and_guard_budget_receipts_and_helper_ablation(self):
        for result in self.results.values():
            self.assertFalse(result['evidence']['exhausted'])
            self.assertEqual(result['evidence']['charged_operations'], sum(result['evidence']['operation_counts'].values()))
            encoded = json.dumps(result['model'])
            self.assertNotIn('"splice"', encoded)
            self.assertNotIn('"rotate"', encoded)
            self.assertEqual(result['model']['training_records'], len(result['evidence']['records']))


if __name__ == '__main__':
    unittest.main()
