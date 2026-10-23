"""Infer bounded portable data transducers from explicitly trusted factories.

The expression algebra/codecs and typed interface are supplied priors. Public
observer purity and model accuracy are empirical finite evidence, not proofs.
No source/private-state inspection, sandbox or arbitrary-replica guarantee.
"""
from __future__ import annotations
import base64
import itertools
import json
import math
import random
import re
import time
from collections import Counter
from typing import Callable

SCHEMA = "data-transducer/1"
SPEC_SCHEMA = "eidryl-surface/1"
MAX_SEQUENCE = 65536
MAX_INTEGER = 1000000
SAFE_INTEGER = 9007199254740991

class Refusal(Exception):
    """The experiment did not produce an accepted model."""
    def __init__(self, reason: str, *, evidence=None):
        super().__init__(reason)
        self.reason = reason
        self.evidence = evidence or {}

def pack(v):
    if type(v) is bytes:
        return {'bytes': base64.b64encode(v).decode()}
    if type(v) in (str, int, bool, type(None)):
        return v
    if type(v) in (list, tuple):
        return [pack(x) for x in v]
    raise TypeError(type(v).__name__)

def unpack(v):
    if type(v) is dict and set(v) == {'bytes'}:
        return base64.b64decode(v['bytes'])
    if type(v) is list:
        return [unpack(x) for x in v]
    return v

def kind(v):
    if type(v) is bytes:
        return 'bytes'
    if type(v) is str:
        return 'str'
    if type(v) is list and all((type(x) is str for x in v)):
        return 'list'
    if type(v) is int:
        return 'int'
    if type(v) is bool:
        return 'bool'
    if v is None:
        return 'none'
    return 'unsupported'

def eq(a, b):
    return type(a) is type(b) and a == b

def evaluate(expr, row):
    op = expr[0]
    if op == 'const':
        return unpack(expr[1])
    if op in ('s', 'a', 'c'):
        return row[op][expr[1]]
    if op == 'if':
        return evaluate(expr[2] if evaluate(expr[1], row) else expr[3], row)
    values = [evaluate(x, row) for x in expr[1:]]
    if op == 'len':
        return len(values[0])
    if op == 'add':
        return values[0] + values[1]
    if op == 'sub':
        return values[0] - values[1]
    if op == 'min':
        return min(values)
    if op == 'max':
        return max(values)
    if op == 'eq':
        return eq(values[0], values[1])
    if op == 'lt':
        return values[0] < values[1]
    if op == 'not':
        return not values[0]
    if op == 'at':
        return values[0][values[1]]
    if op == 'slice':
        return values[0][values[1]:values[2]]
    if op == 'concat':
        return values[0] + values[1]
    if op == 'single':
        return [values[0]]
    if op == 'reverse':
        return values[0][::-1]
    if op == 'tail':
        return values[0][-values[1]:] if values[1] > 0 else values[0][:0]
    if op == 'mod':
        return values[0] % values[1] if values[1] else 0
    if op == 'pad':
        source, n = values
        unit = '\x00' if type(source) is str else b'\x00' if type(source) is bytes else [None]
        return unit * max(0, n)
    if op == 'encode':
        return values[0].encode(values[1])
    raise ValueError(op)

def vector(expr, rows):
    result = []
    try:
        for row in rows:
            result.append(evaluate(expr, row))
        return result
    except (TypeError, ValueError, IndexError, OverflowError):
        return None

def key(values):
    return json.dumps([pack(v) for v in values], ensure_ascii=True, sort_keys=True, separators=(',', ':'))

def cost(expr):
    return len(json.dumps(expr, separators=(',', ':')))

def catalog(rows):
    """Enumerate bounded primitive programs, merging identical observed vectors."""
    outputs = {}

    def put(expr):
        values = vector(expr, rows)
        if values is None or any((kind(v) == 'unsupported' for v in values)):
            return
        token = key(values)
        old = outputs.get(token)
        if old is None or cost(expr) < cost(old):
            outputs[token] = expr
    for scope in ['s', 'a', 'c']:
        for i in range(len(rows[0][scope])):
            put([scope, i])
    for value in [None, False, True, 0, 1, -1, '', [], b'']:
        put(['const', pack(value)])
    for scope in ['s', 'y']:
        for i in range(len(rows[0][scope])):
            vals = [r[scope][i] for r in rows]
            if all((eq(vals[0], v) for v in vals)):
                put(['const', pack(vals[0])])
    atoms = list(outputs.values())
    seq = [e for e in atoms if kind(evaluate(e, rows[0])) in ['str', 'bytes', 'list']]
    for expr in seq:
        put(['len', expr])
    ints = [e for e in outputs.values() if kind(evaluate(e, rows[0])) == 'int']
    for a, b in itertools.product(ints, repeat=2):
        for op in ['add', 'sub', 'min', 'max', 'mod']:
            put([op, a, b])
    ints = [e for e in outputs.values() if kind(evaluate(e, rows[0])) == 'int']
    ints = sorted(ints, key=cost)[:72]
    numeric_atoms = [e for e in atoms if kind(evaluate(e, rows[0])) == 'int'] + [['len', e] for e in seq]
    deeper = ints
    for depth in range(2):
        for a, b in itertools.product(deeper, numeric_atoms):
            for op in ['min', 'max']:
                put([op, a, b])
        deeper = sorted([e for e in outputs.values() if kind(evaluate(e, rows[0])) == 'int'], key=cost)[:240]
    remainders = [e for e in outputs.values() if e[0] == 'mod']
    for a, b in itertools.product(numeric_atoms, remainders):
        put(['sub', a, b])
    strings = [e for e in atoms if kind(evaluate(e, rows[0])) == 'str']
    for text in strings:
        put(['single', text])
        for codec in ['utf-8', 'utf-16-le', 'utf-16-be']:
            put(['encode', text, ['const', codec]])
    seq = [e for e in outputs.values() if kind(evaluate(e, rows[0])) in ['str', 'bytes', 'list']]
    for expr in list(seq):
        put(['reverse', expr])
        for number in ints:
            put(['at', expr, number])
            put(['tail', expr, number])
            for other in ints:
                put(['slice', expr, number, other])
    byte_args = [e for e in seq if kind(evaluate(e, rows[0])) == 'bytes' and e[0] == 'encode']
    residuals = set()
    for row in rows:
        for actual in row['y']:
            if type(actual) is not bytes:
                continue
            for expr in byte_args:
                encoded = evaluate(expr, row)
                if encoded and actual.endswith(encoded):
                    residual = actual[:-len(encoded)]
                    if len(residual) <= 4:
                        residuals.add(residual)
    for residual in residuals:
        put(['const', pack(residual)])
    seq = [e for e in outputs.values() if kind(evaluate(e, rows[0])) in ['str', 'bytes', 'list'] and e[0] in ['s', 'a', 'c', 'const', 'single', 'encode']]
    composed = []
    for a, b in itertools.product(seq, repeat=2):
        if type(evaluate(a, rows[0])) is not type(evaluate(b, rows[0])):
            continue
        expr = ['concat', a, b]
        put(expr)
        composed.append(expr)
    for expr in composed:
        put(['len', expr])
        for n in numeric_atoms:
            put(['add', n, ['len', expr]])
        for n in ints:
            put(['tail', expr, n])
    for expr in seq:
        put(['len', expr])
        for n in numeric_atoms:
            put(['add', n, ['len', expr]])
    predicates = []
    for a, b in itertools.product(ints[:20], repeat=2):
        for op in ['eq', 'lt']:
            expr = [op, a, b]
            v = vector(expr, rows)
            if v is not None and any(v) and (not all(v)):
                predicates.append((expr, v))
                put(expr)
                put(['not', expr])
    return (outputs, predicates)

def fit_concat(target, rows, outputs):
    if not target or kind(target[0]) not in ['str', 'bytes', 'list']:
        return None
    typed = type(target[0])
    base = []
    numbers = []
    for e in outputs.values():
        v = evaluate(e, rows[0])
        if type(v) is typed and e[0] in ['s', 'a', 'c', 'const', 'single', 'encode', 'concat']:
            base.append(e)
        if type(v) is int:
            numbers.append(e)
    numbers = sorted(numbers, key=cost)[:4096]
    sources = [e for e in base if e[0] == 's']
    empty = ['const', pack(target[0][:0])]
    if empty not in base:
        base.append(empty)
    suffixes = {}
    prefixes = []

    def suffix(e):
        vals = vector(e, rows)
        if vals is not None and all((type(v) is typed for v in vals)):
            k = key(vals)
            if k not in suffixes or cost(e) < cost(suffixes[k]):
                suffixes[k] = e

    def prefix(e):
        vals = vector(e, rows)
        if vals is not None and all((type(v) is typed and t[:len(v)] == v for v, t in zip(vals, target))):
            prefixes.append((e, vals))
    for e in base:
        suffix(e)
        prefix(e)
    zero = ['const', 0]
    for source, n in itertools.product(sources, numbers):
        for part in [['slice', source, zero, n], ['slice', source, n, ['len', source]]]:
            suffix(part)
            prefix(part)
        if typed in (str, bytes):
            prefix(['concat', ['slice', source, zero, n], ['pad', source, ['sub', n, ['len', source]]]])
    middle = [(e, vector(e, rows)) for e in base]
    solutions = []
    for left, lv in prefixes:
        for mid, mv in middle:
            if mv is None or not all((type(v) is typed for v in mv)):
                continue
            residual = []
            valid = True
            for l, m, t in zip(lv, mv, target):
                first = l + m
                if t[:len(first)] != first:
                    valid = False
                    break
                residual.append(t[len(first):])
            if not valid:
                continue
            right = suffixes.get(key(residual))
            if right is None:
                continue
            e = ['concat', left, right] if mid == empty else ['concat', left, ['concat', mid, right]]
            solutions.append(e)
    return min(solutions, key=cost) if solutions else None

def fit(target, rows, cache=None):
    if target and all((eq(target[0], v) for v in target)):
        return ['const', pack(target[0])]
    outputs, predicates = cache or catalog(rows)
    token = key(target)
    if token in outputs:
        return outputs[token]
    composite = fit_concat(target, rows, outputs)
    if composite is not None:
        return composite
    candidates = [(expr, vector(expr, rows)) for expr in outputs.values()]
    for predicate, mask in predicates:
        yes = [expr for expr, vals in candidates if vals is not None and all((not m or eq(x, t) for x, t, m in zip(vals, target, mask)))]
        no = [expr for expr, vals in candidates if vals is not None and all((m or eq(x, t) for x, t, m in zip(vals, target, mask)))]
        if yes and no:
            return ['if', predicate, min(yes, key=cost), min(no, key=cost)]
    for predicate, mask in sorted(predicates, key=lambda p: cost(p[0]))[:48]:
        branches = []
        for truth in [True, False]:
            rs = [r for r, m in zip(rows, mask) if m == truth]
            ts = [t for t, m in zip(target, mask) if m == truth]
            if len(rs) < 3:
                break
            exact = [e for e, v in candidates if v is not None and all((m != truth or eq(x, t) for x, t, m in zip(v, target, mask)))]
            e = min(exact, key=cost) if exact else fit_concat(ts, rs, outputs)
            if e is None:
                break
            branches.append(e)
        if len(branches) == 2:
            return ['if', predicate, *branches]
    raise RuntimeError('Generic grammar cannot fit target data law')

def fit_labels(target, rows, labels):
    if any((v not in labels for v in target)):
        raise RuntimeError('Unknown categorical label')
    if all((eq(target[0], v) for v in target)):
        return ['const', pack(target[0])]
    _, predicates = catalog(rows)
    for predicate, mask in predicates:
        yes = [v for v, m in zip(target, mask) if m]
        no = [v for v, m in zip(target, mask) if not m]
        if yes and no and all((eq(yes[0], v) for v in yes)) and all((eq(no[0], v) for v in no)):
            return ['if', predicate, ['const', pack(yes[0])], ['const', pack(no[0])]]
    raise RuntimeError('Categorical partition unavailable in bounded grammar')

_PATH = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*\Z")
_ID = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,127}\Z")
_DEFAULT_TEXT = ['', 'A', 'alpha', 'βeta', '🙂Z', '漢字', 'a\nb', 'é\u0301']
_DEFAULT_INT = [-2, -1, 0, 1, 2, 3, 5, 9, 17]
_DEFAULT_LIST = [[], ['A'], ['βeta', '🙂Z'], ['alpha', '', '漢字']]


def _typed(value, type_name):
    if type_name == 'str':
        return type(value) is str and len(value) <= MAX_SEQUENCE and not any(0xD800 <= ord(c) <= 0xDFFF for c in value)
    if type_name == 'int':
        return (type(value) is int and abs(value) <= MAX_INTEGER) or (type(value) is float and math.isfinite(value) and value.is_integer() and abs(value) <= MAX_INTEGER)
    if type_name == 'list':
        return type(value) is list and len(value) <= MAX_SEQUENCE and all(_typed(v, 'str') for v in value)
    return False


def validate_spec(spec):
    """Return a normalized strict interface declaration; no target is executed."""
    required = {'schema', 'id', 'constructor_types', 'constructor_samples', 'actions'}
    optional = {'text_samples', 'int_samples', 'list_samples', 'prefix_depth'}
    if type(spec) is not dict or not required <= set(spec) or set(spec) - required - optional:
        raise Refusal('Invalid surface specification fields')
    if spec['schema'] != SPEC_SCHEMA or type(spec['id']) is not str or not _ID.fullmatch(spec['id']):
        raise Refusal('Invalid surface schema or identity')
    types = spec['constructor_types']
    if type(types) is not list or len(types) > 8 or any(t not in ('str', 'int', 'list') for t in types):
        raise Refusal('Unsupported constructor types')
    samples = spec['constructor_samples']
    if type(samples) is not list or not 1 <= len(samples) <= 32:
        raise Refusal('Constructor samples must contain 1..32 argument lists')
    for sample in samples:
        if type(sample) is not list or len(sample) != len(types) or not all(_typed(v, t) for v, t in zip(sample, types)):
            raise Refusal('Malformed constructor sample')
    actions = spec['actions']
    if type(actions) is not dict or not 1 <= len(actions) <= 16:
        raise Refusal('Actions must contain 1..16 declarations')
    for name, action in actions.items():
        if type(name) is not str or not _ID.fullmatch(name) or type(action) is not dict or set(action) != {'path', 'types'}:
            raise Refusal('Malformed action declaration')
        if type(action['path']) is not str or len(action['path']) > 128 or not _PATH.fullmatch(action['path']):
            raise Refusal('Action paths must contain public attributes only')
        if type(action['types']) is not list or len(action['types']) > 8 or any(t not in ('str', 'int', 'list') for t in action['types']):
            raise Refusal('Unsupported action argument types')
    normalized = json.loads(json.dumps(spec, ensure_ascii=True))
    normalized['constructor_samples'] = [[int(v) if t == 'int' else v for v, t in zip(sample, types)] for sample in normalized['constructor_samples']]
    for name, default, type_name in [('text_samples', _DEFAULT_TEXT, 'str'), ('int_samples', _DEFAULT_INT, 'int'), ('list_samples', _DEFAULT_LIST, 'list')]:
        values = normalized.setdefault(name, json.loads(json.dumps(default, ensure_ascii=True)))
        if type(values) is not list or not 1 <= len(values) <= 16 or not all(_typed(v, type_name) for v in values):
            raise Refusal('Invalid ' + name)
        if type_name == 'int':
            normalized[name] = [int(v) for v in values]
    depth = normalized.setdefault('prefix_depth', 3)
    if type(depth) is not int or not 0 <= depth <= 3:
        raise Refusal('Prefix depth must be an integer in 0..3')
    # Reject oversized ordinary and synthesized-boundary probes before target execution.
    for action in normalized['actions'].values():
        for boundary in (False, True):
            _check_probe_width([_probe_values(normalized, t, boundary) for t in action['types']])
    return normalized


def _probe_values(spec, type_name, boundary):
    values = spec[{'str': 'text_samples', 'int': 'int_samples', 'list': 'list_samples'}[type_name]]
    if not boundary:
        return values
    if type_name == 'int':
        return sorted(set([min(values), 0, 1, max(values)]))
    return [min(values, key=len), max(values, key=len)]


def _check_probe_width(values):
    cardinality = 1
    for sample in values:
        cardinality *= len(sample)
        if cardinality > 128:
            raise Refusal('Argument sample product exceeds supported probe width')
    return cardinality


def _arguments(spec, action, args):
    if type(action) is not str or action not in spec['actions'] or type(args) is not list:
        raise Refusal('Unsupported action record')
    types = spec['actions'][action]['types']
    if len(args) != len(types) or not all(_typed(v, t) for v, t in zip(args, types)):
        raise Refusal('Unsupported action arity, types or bounds')
    return [int(v) if t == 'int' else v for v, t in zip(args, types)]


def call(target, spec, action, args):
    """Invoke one declared public path on an explicitly trusted object."""
    args = _arguments(spec, action, args)
    current = target
    names = spec['actions'][action]['path'].split('.')
    for name in names[:-1]:
        current = getattr(current, name)
    return getattr(current, names[-1])(*args)


def _query(target, field):
    if field == 'iteration':
        values = list(itertools.islice(iter(target), MAX_SEQUENCE + 1))
        if len(values) > MAX_SEQUENCE:
            raise Refusal('Iteration observation exceeds value bound')
        return values
    current = target
    path = field[:-2] if field.endswith('()') else field
    for name in path.split('.'):
        current = getattr(current, name)
    return current() if field.endswith('()') else current


def _portable(value):
    if type(value) is int and abs(value) > SAFE_INTEGER:
        raise Refusal('Observation integer is not portable to JavaScript')
    if kind(value) == 'unsupported':
        raise Refusal('Observation type is outside the supported grammar')
    if type(value) in (str, bytes, list) and len(value) > 1048576:
        raise Refusal('Observation exceeds the state value bound')
    if type(value) is str and any(0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise Refusal('Unpaired surrogate observation is not portable')
    if type(value) is list:
        # Detach captured state/returns from mutable public objects as well as validating items.
        return [_portable(item) for item in value]
    return value


def _dump(value):
    if type(value) is dict:
        return {k: _dump(v) for k, v in value.items()}
    if type(value) in (list, tuple):
        return [_dump(v) for v in value]
    return pack(value)


def _same_json(a, b):
    return json.dumps(_dump(a), sort_keys=True, ensure_ascii=True) == json.dumps(_dump(b), sort_keys=True, ensure_ascii=True)


class _Experiment:
    def __init__(self, factory, spec, budget):
        if not callable(factory):
            raise Refusal('Factory must be callable')
        if type(budget) is not int or not 1 <= budget <= 250000:
            raise Refusal('Query budget must be an integer in 1..250000')
        self.factory = factory
        self.spec = validate_spec(spec)
        self.limit = budget
        self.counts = Counter()
        self.used = 0
        self.exhausted = False
        self.live = {}
        self.fields = []
        self.records = []
        self.initial = []
        self.constructors = []
        self.rejected_fields = []
        self.mode_cache = {}
        self.failed_checked = set()
        self.input_violations = []

    def charge(self, kind_name, *, cleanup=False):
        self.used += 1
        self.counts[kind_name] += 1
        if self.used > self.limit:
            self.exhausted = True
        if self.exhausted and not cleanup:
            raise Refusal('Oracle query budget exhausted')

    @staticmethod
    def detached_inputs(values):
        return [list(v) if type(v) is list else v for v in values]

    def verify_inputs(self, operation, before, after):
        try:
            unchanged = _same_json(before, after)
        except (TypeError, ValueError, RecursionError):
            unchanged = False
        if unchanged:
            return
        try:
            captured_after = _dump(after)
        except (TypeError, ValueError, RecursionError):
            captured_after = '<nonportable argument mutation>'
        self.input_violations.append({'operation': operation, 'before': _dump(before), 'after': captured_after})
        raise Refusal('External argument mutation is outside the portable model contract')

    def new(self, constructor):
        copied = self.detached_inputs(constructor)
        self.charge('factory')
        try:
            obj = self.factory(*copied)
        except BaseException:
            self.verify_inputs('constructor', constructor, copied)
            raise
        if id(obj) in self.live:
            raise Refusal('Factory reused an object that is still part of an experiment')
        self.live[id(obj)] = obj
        try:
            self.verify_inputs('constructor', constructor, copied)
        except BaseException:
            self.release(obj)
            raise
        return obj

    def release(self, obj):
        if id(obj) not in self.live:
            return
        try:
            method = getattr(obj, 'close', None)
            if callable(method):
                # Required cleanup is still counted after exhaustion. It cannot yield a model.
                self.charge('cleanup', cleanup=True)
                method()
        except Exception:
            pass
        finally:
            self.live.pop(id(obj), None)

    def act(self, obj, action, args):
        copied = self.detached_inputs(args)
        self.charge('action')
        try:
            value = call(obj, self.spec, action, copied)
        except BaseException:
            self.verify_inputs('action:' + action, args, copied)
            raise
        self.verify_inputs('action:' + action, args, copied)
        return value

    def query(self, obj, field):
        self.charge('observation')
        return _portable(_query(obj, field))

    def state(self, obj, fields=None):
        return [self.query(obj, f) for f in (self.fields if fields is None else fields)]

    def result(self, obj, action, args):
        try:
            return {'ok': True, 'value': _portable(self.act(obj, action, args)), 'error': None}
        except Refusal:
            raise
        except Exception as exc:
            return {'ok': False, 'value': None, 'error': {'type': type(exc).__name__, 'message': str(exc)[:1024]}}

    def replay(self, constructor, prefix):
        obj = self.new(constructor)
        try:
            for action, args in prefix:
                result = self.result(obj, action, args)
                if not result['ok']:
                    raise Refusal('A previously successful prefix did not reset reproducibly')
            return obj
        except BaseException:
            self.release(obj)
            raise

    def samples(self, types, *, boundary=False):
        values = [_probe_values(self.spec, type_name, boundary) for type_name in types]
        _check_probe_width(values)
        return [list(v) for v in itertools.product(*values)]

    def alphabet(self):
        result = []
        for action, declaration in self.spec['actions'].items():
            args = []
            for t in declaration['types']:
                values = self.spec[{'str': 'text_samples', 'int': 'int_samples', 'list': 'list_samples'}[t]]
                if t == 'int':
                    args.append(0 if 0 in values else min(values, key=abs))
                else:
                    args.append(next((v for v in values if len(v)), values[0]))
            result.append((action, args))
        return result

    def discovery(self, constructor):
        obj = self.new(constructor)
        candidates = []
        queue = [('', obj)]
        seen = set()
        try:
            while queue:
                prefix, current = queue.pop(0)
                if id(current) in seen:
                    continue
                seen.add(id(current))
                if len(seen) > 16:
                    raise Refusal('Public observer graph exceeds 16 objects')
                for name in dir(current):
                    if name.startswith('_'):
                        continue
                    self.charge('attribute_inspection')
                    try:
                        value = getattr(current, name)
                    except Refusal:
                        raise
                    except Exception:
                        continue
                    path = prefix + name
                    if callable(value):
                        probe = self.new(constructor)
                        try:
                            value = self.query(probe, path + '()')
                        except Refusal as exc:
                            if self.exhausted:
                                raise
                            if 'type is outside' not in exc.reason and 'exceeds' not in exc.reason:
                                self.rejected_fields.append(path)
                            continue
                        except Exception:
                            continue
                        finally:
                            value = None
                            self.release(probe)
                        candidates.append(path + '()')
                    elif kind(value) != 'unsupported':
                        try:
                            _portable(value)
                        except Refusal:
                            continue
                        candidates.append(path)
                    elif prefix.count('.') < 2:
                        queue.append((path + '.', value))
        finally:
            self.release(obj)
        candidates.append('iteration')
        accepted = []
        alphabet = self.alphabet()
        prefixes = []
        for prefix in [[], alphabet[:1], alphabet[:2]]:
            probe = self.new(constructor)
            try:
                if all(self.result(probe, action, args)['ok'] for action, args in prefix):
                    prefixes.append(prefix)
            finally:
                self.release(probe)
        for field in sorted(set(candidates)):
            stable = True
            for prefix in prefixes:
                try:
                    a = self.replay(constructor, prefix)
                    b = self.replay(constructor, prefix)
                except Refusal:
                    raise
                try:
                    first = self.query(a, field)
                    second = self.query(a, field)
                    if not eq(first, second):
                        stable = False
                    for action, args in alphabet:
                        aa = self.replay(constructor, prefix)
                        bb = self.replay(constructor, prefix)
                        try:
                            baseline = self.result(aa, action, args)
                            self.query(bb, field)
                            actual = self.result(bb, action, args)
                            if not _same_json(baseline, actual):
                                stable = False
                                break
                            # Generic iteration is a separate witness for container mutation.
                            try:
                                self.charge('observation'); av = list(itertools.islice(iter(aa), MAX_SEQUENCE + 1))
                                self.charge('observation'); bv = list(itertools.islice(iter(bb), MAX_SEQUENCE + 1))
                                if not _same_json(av, bv):
                                    stable = False
                                    break
                            except Refusal:
                                raise
                            except (TypeError, ValueError):
                                pass
                            # A later declared operation detects additional query side effects.
                            for after, arguments in alphabet[:2]:
                                if not _same_json(self.result(aa, after, arguments), self.result(bb, after, arguments)):
                                    stable = False
                                    break
                        finally:
                            self.release(aa)
                            self.release(bb)
                        if not stable:
                            break
                except Refusal:
                    if self.exhausted:
                        raise
                    stable = False
                except Exception:
                    stable = False
                finally:
                    self.release(a)
                    self.release(b)
                if not stable:
                    break
            if stable:
                accepted.append(field)
            else:
                self.rejected_fields.append(field)
        if not accepted:
            raise Refusal('No empirical public observers fit the supported value grammar')
        self.fields = accepted

    def append(self, constructor, prefix, obj, action, args):
        before = self.state(obj)
        outcome = self.result(obj, action, args)
        after = self.state(obj)
        row = {'s': before, 'a': args, 'c': constructor, 'y': [outcome['value'], *after], 'ok': outcome['ok'], 'error': outcome['error'], 'action': action, 'prefix': list(prefix), 'next_prefix': prefix + [(action, args)] if outcome['ok'] else list(prefix)}
        self.records.append(row)
        if not outcome['ok'] and key(before) != key(after):
            raise Refusal('A failed operation changed observed state')
        if len(self.records) > 1600:
            raise Refusal('Probe record budget exceeded')
        return row

    def collect(self, seed):
        rng = random.Random(seed)
        for constructor in self.spec['constructor_samples']:
            try:
                obj = self.new(constructor)
            except Refusal:
                raise
            except Exception as exc:
                self.constructors.append({'s': [], 'a': [], 'c': constructor, 'y': [], 'ok': False, 'error': type(exc).__name__})
                continue
            try:
                self.constructors.append({'s': [], 'a': [], 'c': constructor, 'y': [], 'ok': True, 'error': None})
                self.initial.append({'s': [], 'a': [], 'c': constructor, 'y': self.state(obj)})
                prefix = []
                for _ in range(32):
                    action = rng.choice(list(self.spec['actions']))
                    choices = self.samples(self.spec['actions'][action]['types'])
                    args = rng.choice(choices)
                    row = self.append(constructor, prefix, obj, action, args)
                    prefix = row['next_prefix']
            finally:
                self.release(obj)
        if not self.initial:
            raise Refusal('No constructor sample succeeded')
        # Constant/redundant observer vectors are removed without naming their semantics.
        vectors = []
        retained = []
        fields = []
        for index, field in enumerate(self.fields):
            vector_values = [r['y'][index] for r in self.initial] + [v for r in self.records for v in (r['s'][index], r['y'][index + 1])]
            token = key(vector_values)
            if all(eq(vector_values[0], v) for v in vector_values) or token in vectors:
                continue
            vectors.append(token)
            retained.append(index)
            fields.append(field)
        if not fields or len(fields) > 8:
            raise Refusal('Supported models require 1..8 varying independent public observers')
        self.fields = fields
        for row in self.initial:
            row['y'] = [row['y'][i] for i in retained]
        for row in self.records:
            row['s'] = [row['s'][i] for i in retained]
            row['y'] = [row['y'][0]] + [row['y'][i + 1] for i in retained]
        self.explore()

    def explore(self):
        constructor = self.initial[0]['c']
        alphabet = self.alphabet()
        pending = [[]]
        reached = set()
        transitions = {key([r['action'], r['s'], r['a'], r['y'], r['ok']]) for r in self.records}
        while pending:
            prefix = pending.pop(0)
            branches = []
            for action, args in alphabet:
                obj = self.replay(constructor, prefix)
                try:
                    row = self.append(constructor, prefix, obj, action, args)
                finally:
                    self.release(obj)
                token = key([row['action'], row['s'], row['a'], row['y'], row['ok']])
                if token in transitions:
                    self.records.pop()
                else:
                    transitions.add(token)
                branches.append(row)
            fingerprint = key([branches[0]['s'], *[r['y'] for r in branches], *[r['ok'] for r in branches]])
            if fingerprint in reached:
                continue
            reached.add(fingerprint)
            if len(prefix) < self.spec['prefix_depth']:
                pending.extend(r['next_prefix'] for r in branches if r['ok'])

    def fingerprint(self, constructor, prefix, alphabet=None):
        values = []
        for action, args in (alphabet or self.alphabet()):
            obj = self.replay(constructor, prefix)
            try:
                outcome = self.result(obj, action, args)
                values.append([outcome['ok'], outcome['value'], outcome['error'], self.state(obj)])
            finally:
                self.release(obj)
        return _dump(values)

    def guard_failed_operations(self, detector):
        for row in self.records:
            if row['ok']:
                continue
            token = key([row['c'], row['s'], row['action'], row['a'], row['mode']])
            if token in self.failed_checked:
                continue
            self.failed_checked.add(token)
            suffixes = self.alphabet()
            if detector is not None:
                suffixes.append((detector['action'], [detector['argument']]))
            for action, args in suffixes:
                a = self.replay(row['c'], row['prefix'])
                b = self.replay(row['c'], row['prefix'])
                try:
                    failed = self.result(a, row['action'], row['a'])
                    if failed['ok']:
                        raise Refusal('Failed operation did not reset reproducibly')
                    first = [self.result(a, action, args), self.state(a)]
                    second = [self.result(b, action, args), self.state(b)]
                    if not _same_json(first, second):
                        raise Refusal('A failed operation changed a distinguishing future')
                finally:
                    self.release(a)
                    self.release(b)

    def detector(self):
        seen = {}
        alias = False
        for row in self.records:
            token = key([row['action'], row['s'], row['a']])
            response = key([row['ok'], row['y']])
            if token in seen and seen[token] != response:
                alias = True
                break
            seen[token] = response
        if not alias:
            return None
        for action, declaration in self.spec['actions'].items():
            if declaration['types'] != ['str']:
                continue
            for byte_index, value in enumerate(self.records[0]['s']):
                if type(value) is not bytes:
                    continue
                for position_index, value in enumerate(self.records[0]['s']):
                    if type(value) is not int:
                        continue
                    for codec in ['utf-8', 'utf-16-le', 'utf-16-be']:
                        signatures = []
                        for row in self.records:
                            obj = self.replay(row['c'], row['prefix'])
                            try:
                                before = self.state(obj)
                                outcome = self.result(obj, action, ['Z'])
                                after = self.state(obj)
                            finally:
                                self.release(obj)
                            if not outcome['ok']:
                                break
                            delta = after[byte_index][before[position_index]:after[position_index]]
                            basis = 'Z'.encode(codec)
                            if not delta.endswith(basis):
                                break
                            signatures.append(delta[:-len(basis)])
                        if len(signatures) == len(self.records) and 1 < len(set(signatures)) <= 4:
                            return {'action': action, 'argument': 'Z', 'byte_field': byte_index, 'position_field': position_index, 'basis': codec, 'signatures': sorted(set(signatures))}
        raise Refusal('Observed hidden-state alias is unsupported by the bounded suffix detector')

    def mode(self, constructor, prefix, detector):
        if detector is None:
            return 0
        token = key([constructor, prefix])
        if token in self.mode_cache:
            return self.mode_cache[token]
        obj = self.replay(constructor, prefix)
        try:
            before = self.state(obj)
            result = self.result(obj, detector['action'], [detector['argument']])
            after = self.state(obj)
        finally:
            self.release(obj)
        if not result['ok']:
            raise Refusal('Distinguishing suffix ceased to be supported')
        delta = after[detector['byte_field']][before[detector['position_field']]:after[detector['position_field']]]
        basis = detector['argument'].encode(detector['basis'])
        if not delta.endswith(basis) or delta[:-len(basis)] not in detector['signatures']:
            raise Refusal('A new latent residual fell outside the inferred mode classes')
        mode = detector['signatures'].index(delta[:-len(basis)])
        self.mode_cache[token] = mode
        return mode

    @staticmethod
    def coverage(state):
        """Joint sequence-length/integer-position coverage, independent of method names."""
        result = set()
        def bucket(v):
            return 'neg' if v < 0 else 'zero' if v == 0 else 'one' if v == 1 else 'small' if v < 5 else 'medium' if v < 17 else 'large'
        sequences = [(i, len(v)) for i, v in enumerate(state) if type(v) in (str, bytes, list)]
        integers = [(i, v) for i, v in enumerate(state) if type(v) is int]
        for i, length in sequences:
            result.add(('length', i, bucket(length)))
            for j, value in integers:
                relation = 'negative' if value < 0 else 'zero' if value == 0 else 'inside' if value < length else 'end' if value == length else 'beyond'
                result.add(('joint', i, j, bucket(length), bucket(value), relation))
        for i, value in integers:
            result.add(('integer', i, bucket(value)))
        return result

    def representatives(self):
        by_mode = {}
        unique = set()
        for row in self.records:
            for prefix, state, mode in [(row['prefix'], row['s'], row['mode']), (row['next_prefix'], row['y'][1:], row['next_mode'])]:
                token = key([mode, state])
                if token in unique:
                    continue
                unique.add(token)
                by_mode.setdefault(mode, []).append((row['c'], prefix, state))
        selected = {}
        for mode, candidates in by_mode.items():
            remaining = [(item, self.coverage(item[2])) for item in candidates]
            covered = set()
            chosen = []
            while remaining and len(chosen) < 16:
                index = max(range(len(remaining)), key=lambda i: (len(remaining[i][1] - covered), sum(len(v) for v in remaining[i][0][2] if type(v) in (str, bytes, list))))
                item, features = remaining.pop(index)
                if chosen and not features - covered:
                    break
                chosen.append(item)
                covered |= features
            selected[mode] = chosen
        return selected

    def boundary(self, detector):
        for row in self.records:
            row['mode'] = self.mode(row['c'], row['prefix'], detector)
            row['next_mode'] = self.mode(row['c'], row['next_prefix'], detector)
        known = {key([r['action'], r['s'], r['a'], r['y'], r['mode']]) for r in self.records}
        # New reached states become candidates again, covering cross-field combinations.
        for _ in range(3):
            for mode, representatives in self.representatives().items():
                for constructor, prefix, state in representatives:
                    for action, declaration in self.spec['actions'].items():
                        for args in self.samples(declaration['types'], boundary=True):
                            obj = self.replay(constructor, prefix)
                            try:
                                row = self.append(constructor, prefix, obj, action, args)
                            finally:
                                self.release(obj)
                            token = key([action, state, args, row['y'], mode])
                            if token in known:
                                self.records.pop()
                                continue
                            known.add(token)
                            row['mode'] = mode
                            row['next_mode'] = self.mode(constructor, row['next_prefix'], detector)
        self.guard_failed_operations(detector)

    def evidence(self):
        return _dump({'schema': 'eidryl-evidence/1', 'charged_operations': self.used, 'query_budget': self.limit, 'operation_counts': dict(self.counts), 'exhausted': self.exhausted, 'input_violations': self.input_violations, 'fields': self.fields, 'rejected_observers': self.rejected_fields, 'constructors': self.constructors, 'initial': self.initial, 'records': self.records, 'purity': 'finite repeated-query and distinguishing-future evidence, not proof', 'trusted_execution': True})

    def run(self, seed):
        valid = None
        best_score = -1
        for constructor in self.spec['constructor_samples']:
            try:
                obj = self.new(constructor)
            except Refusal:
                raise
            except Exception:
                continue
            self.release(obj)
            score = sum(abs(v) if type(v) is int else len(v) + (sum(len(x) for x in v) if type(v) is list else 0) for v in constructor)
            if score > best_score:
                best_score = score
                valid = constructor
        if valid is None:
            raise Refusal('No constructor sample succeeded')
        self.discovery(valid)
        self.collect(seed)
        detector = self.detector()
        self.boundary(detector)
        initial_modes = {self.mode(r['c'], [], detector) for r in self.initial}
        if len(initial_modes) != 1:
            raise Refusal('Constructor-dependent latent initial modes are unsupported')
        initial_cache = catalog(self.initial)
        initial_rules = [fit([r['y'][i] for r in self.initial], self.initial, initial_cache) for i in range(len(self.fields))]
        model = {'schema': SCHEMA, 'factory': self.spec['id'], 'constructor_types': self.spec['constructor_types'], 'constructor_guard': fit_labels([r['ok'] for r in self.constructors], self.constructors, [True, False]), 'fields': self.fields, 'initial': initial_rules, 'initial_mode': initial_modes.pop(), 'surface': {a: d['types'] for a, d in self.spec['actions'].items()}, 'rules': {}, 'latent_modes': len(detector['signatures']) if detector else 1, 'latent_evidence': _dump(detector), 'training_records': len(self.records)}
        for action in self.spec['actions']:
            model['rules'][action] = {}
            for mode in sorted({r['mode'] for r in self.records}):
                all_rows = [r for r in self.records if r['action'] == action and r['mode'] == mode]
                rows = [r for r in all_rows if r['ok']]
                if len(rows) < 3:
                    continue
                guard = fit_labels([r['ok'] for r in all_rows], all_rows, [True, False])
                cache = catalog(rows)
                laws = [fit([r['y'][i] for r in rows], rows, cache) for i in range(len(self.fields) + 1)]
                transition = fit_labels([r['next_mode'] for r in rows], rows, list(range(model['latent_modes'])))
                model['rules'][action][str(mode)] = {'return': laws[0], 'state': laws[1:], 'guard': guard, 'mode': transition, 'examples': len(rows), 'invalid_examples': len(all_rows) - len(rows)}
        if self.exhausted:
            raise Refusal('Oracle query budget exhausted')
        return model


def learn_with_evidence(factory: Callable, spec: dict, *, seed=11, query_budget=6000):
    """Return model plus the complete measured oracle receipt, or raise Refusal.

    The budget charges factories, inspected public attributes, action/query
    operations and cleanup. Cleanup may finish after exhaustion; no model then
    escapes. Internal work performed by the trusted target is not sandboxed.
    """
    if type(seed) is not int or not 0 <= seed <= 4294967295:
        raise Refusal('Seed must be an unsigned 32-bit integer')
    experiment = _Experiment(factory, spec, query_budget)
    start = time.monotonic()
    failure = None
    model = None
    try:
        model = experiment.run(seed)
    except Refusal as exc:
        failure = exc
    except (ValueError, TypeError, RuntimeError, IndexError, OverflowError) as exc:
        failure = Refusal('Unsupported inference: ' + str(exc))
    finally:
        for obj in list(experiment.live.values()):
            experiment.release(obj)
    if failure is None and experiment.exhausted:
        failure = Refusal('Oracle query budget exhausted')
    receipt = experiment.evidence()
    receipt['seconds'] = time.monotonic() - start
    if failure is not None:
        failure.evidence = receipt
        raise failure
    return {'model': model, 'evidence': receipt}


def learn(factory: Callable, spec: dict, *, seed=11, query_budget=6000):
    """Infer a data-transducer/1 model from an explicitly trusted factory."""
    return learn_with_evidence(factory, spec, seed=seed, query_budget=query_budget)['model']


def observe(factory: Callable, spec: dict, constructor: list, actions: list, *, fields=None, query_budget=6000):
    """Capture native history with detached inputs and immutable portable snapshots.

    Changes to passed constructor/action input values refuse because the model
    describes object state and returned values, not external argument effects.
    """
    experiment = _Experiment(factory, spec, query_budget)
    normalized = experiment.spec
    if type(constructor) is not list or len(constructor) != len(normalized['constructor_types']) or not all(_typed(v, t) for v, t in zip(constructor, normalized['constructor_types'])):
        raise Refusal('Malformed observation constructor')
    if type(actions) is not list or len(actions) > 512:
        raise Refusal('Observation action list exceeds supported shape or budget')
    constructor = [int(v) if t == 'int' else v for v, t in zip(constructor, normalized['constructor_types'])]
    canonical_actions = []
    for record in actions:
        if type(record) is not list or len(record) != 2:
            raise Refusal('Observation action records require exact pairs')
        canonical_actions.append([record[0], _arguments(normalized, record[0], record[1])])
    actions = canonical_actions
    try:
        if fields is None:
            experiment.discovery(constructor)
        else:
            if type(fields) is not list or not 1 <= len(fields) <= 32 or len(set(fields)) != len(fields) or any(type(f) is not str or (f != 'iteration' and not _PATH.fullmatch(f[:-2] if f.endswith('()') else f)) for f in fields):
                raise Refusal('Invalid observation fields')
            experiment.fields = fields
        obj = experiment.new(constructor)
        try:
            initial = experiment.state(obj)
            trace = []
            for action, args in actions:
                before = experiment.state(obj)
                outcome = experiment.result(obj, action, args)
                after = experiment.state(obj)
                trace.append({'action': action, 'args': _dump(args), 'accepted': outcome['ok'], 'value': pack(outcome['value']), 'error': outcome['error'], 'before': _dump(before), 'state': _dump(after)})
            result = {'schema': 'eidryl-observation/1', 'fields': experiment.fields, 'initial': _dump(initial), 'trace': trace}
        finally:
            experiment.release(obj)
        if experiment.exhausted:
            raise Refusal('Oracle query budget exhausted')
        result['charged_operations'] = experiment.used
        result['operation_counts'] = dict(experiment.counts)
        return result
    except Refusal as exc:
        exc.evidence = experiment.evidence()
        raise
    finally:
        for obj in list(experiment.live.values()):
            experiment.release(obj)
