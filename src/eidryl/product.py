"""Bounded data IO, strict parity checking and recoverable export writing."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

from . import engine

ASSETS = Path(__file__).with_name("assets")


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON key: " + key[:100])
        value[key] = item
    return value


def loads(text):
    def invalid(value):
        raise ValueError("Nonfinite JSON number: " + value)
    return json.loads(text, object_pairs_hook=_pairs, parse_constant=invalid)


def read_json(path, limit=4 * 1024 * 1024):
    with Path(path).open("rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("JSON file exceeds byte budget")
    return loads(raw.decode("utf-8"))


def data(value):
    return (json.dumps(value, ensure_ascii=True, allow_nan=False, indent=2) + "\n").encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def equal(left, right):
    """Recursive JSON comparison with exact data types and complete cardinality."""
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return set(left) == set(right) and all(equal(left[k], right[k]) for k in left)
    if type(left) is list:
        return len(left) == len(right) and all(equal(a, b) for a, b in zip(left, right))
    return left == right


def compare(native, portable):
    failures = []
    trace = native["trace"]
    if type(portable) is not dict or portable.get("ok") is not True:
        return {"transitions": len(trace), "mismatches": [{"field": "response", "reason": "Portable execution failed or missing"}]}
    if "initial" not in portable or not equal(native["initial"], portable["initial"]):
        failures.append({"field": "initial", "reason": "Initial state differs or is missing"})
    answer = portable.get("trace")
    if type(answer) is not list or len(answer) != len(trace):
        failures.append({"field": "trace", "reason": "Trace cardinality differs or is missing"})
        return {"transitions": len(trace), "mismatches": failures}
    for index, (actual, predicted) in enumerate(zip(trace, answer)):
        if type(predicted) is not dict:
            failures.append({"transition": index, "field": "response", "reason": "Malformed transition"})
            continue
        fields = ["accepted", "state"] + (["value"] if actual["accepted"] else [])
        for field in fields:
            if field not in predicted or not equal(actual[field], predicted[field]):
                failures.append({"transition": index, "field": field, "reason": "Value or exact data type differs"})
    return {"transitions": len(trace), "mismatches": failures}


def requests(model, records):
    if type(records) is not list or not 1 <= len(records) <= 128:
        raise ValueError("Requests must contain 1..128 histories")
    result = []
    count = 0
    for record in records:
        if type(record) is not dict or set(record) != {"model", "constructor", "actions"} or record["model"] != model["factory"]:
            raise ValueError("Malformed request or model identity")
        constructor = record["constructor"]
        types = model["constructor_types"]
        if type(constructor) is not list or len(constructor) != len(types) or not all(engine._typed(v, t) for v, t in zip(constructor, types)):
            raise ValueError("Unsupported constructor types or bounds")
        constructor = [int(v) if t == "int" else v for v, t in zip(constructor, types)]
        actions = record["actions"]
        if type(actions) is not list or not 1 <= len(actions) <= 512:
            raise ValueError("A check history needs 1..512 actions")
        count += len(actions)
        if count > 4096:
            raise ValueError("Batch exceeds 4096 transitions")
        canonical = []
        for entry in actions:
            if type(entry) is not list or len(entry) != 2 or type(entry[0]) is not str or entry[0] not in model["surface"] or type(entry[1]) is not list:
                raise ValueError("Malformed action pair or unsupported action")
            types = model["surface"][entry[0]]
            if len(entry[1]) != len(types) or not all(engine._typed(v, t) for v, t in zip(entry[1], types)):
                raise ValueError("Unsupported action arity, types or bounds")
            canonical.append([entry[0], [int(v) if t == "int" else v for v, t in zip(entry[1], types)]])
        result.append({"model": record["model"], "constructor": constructor, "actions": canonical})
    return result


def runtime(model, records=None):
    node = shutil.which("node")
    if node is None:
        raise ValueError("Node.js is required for portable validation and comparison")
    raw = data({"model": model, "requests": records})
    if len(raw) > 12 * 1024 * 1024:
        raise ValueError("Portable input exceeds byte budget")
    # Only our installed fixed runner is executable. Imported model data stays JSON.
    process = subprocess.run([node, str(ASSETS / "runner.mjs")], input=raw,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    if process.returncode:
        raise ValueError("Portable validation failed: " + process.stderr.decode("utf-8", "replace")[:500])
    if len(process.stdout) > 32 * 1024 * 1024:
        raise ValueError("Portable output exceeds byte budget")
    value = loads(process.stdout.decode())
    if records is not None and (type(value) is not list or len(value) != len(records)):
        raise ValueError("Portable batch cardinality mismatch")
    return value


def write_bundle(output, files):
    """Create a new output directory exclusively. Never overwrite existing bytes."""
    if any(type(name) is not str or not name or name in {".", "..", "manifest.json"} or "/" in name or "\\" in name for name in files):
        raise ValueError("Bundle names must be plain files, excluding manifest.json")
    if not all(type(raw) is bytes for raw in files.values()):
        raise ValueError("Bundle members must be bytes")
    manifest = {"schema": "eidryl-manifest/1", "files": {name: {"sha256": digest(raw), "bytes": len(raw)} for name, raw in sorted(files.items())}}
    output = Path(output)
    output.mkdir()  # exclusive; parent must already exist
    created = []
    try:
        for name, raw in [*files.items(), ("manifest.json", data(manifest))]:
            target = output / name
            with target.open("xb") as handle:
                created.append(target)
                handle.write(raw)
        return manifest
    except BaseException:
        for target in reversed(created):
            target.unlink(missing_ok=True)
        output.rmdir()
        raise


def load_factory(reference):
    """Load user-authorized local Python code; explicitly not a sandbox."""
    path, separator, attribute = reference.rpartition(":")
    if not separator or not path or not attribute.isidentifier() or attribute.startswith("_"):
        raise ValueError("Factory must be a local file.py:public_function")
    source = Path(path).resolve(strict=True)
    if not source.is_file() or source.suffix != ".py":
        raise ValueError("Factory source must be a Python file")
    identity = "_eidryl_trusted_" + digest(str(source).encode())[:16]
    spec = importlib.util.spec_from_file_location(identity, source)
    module = importlib.util.module_from_spec(spec)
    # Trusted module may import sibling code using normal Python import semantics.
    sys.modules[identity] = module
    sys.path.insert(0, str(source.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    factory = getattr(module, attribute)
    if not callable(factory):
        raise ValueError("Selected factory is not callable")
    return factory
