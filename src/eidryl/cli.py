"""Learn, check and export empirical models with explicit local execution."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import subprocess
import io
import zipfile

from . import __version__, engine, product


def _target(parser):
    parser.add_argument("--factory", required=True, help="Trusted local file.py:factory; executes Python code")
    parser.add_argument("--surface", required=True, type=Path, help="Typed eidryl-surface/1 JSON declaration")
    parser.add_argument("--trust-factory", action="store_true", help="Acknowledge execution of this local factory, without a sandbox")
    parser.add_argument("--budget", type=int, default=6000, help="Total charged oracle operations, default 6000")


def _parser():
    parser = argparse.ArgumentParser(description="Learn portable models of trusted resettable APIs.")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    learn = commands.add_parser("learn", help="Experiment with a factory and infer an executable model")
    _target(learn)
    learn.add_argument("--seed", type=int, default=11)
    learn.add_argument("--out", required=True, type=Path, help="New output directory; existing directories refuse")
    check = commands.add_parser("check", help="Compare a model with real native execution on supplied histories")
    _target(check)
    check.add_argument("--model", required=True, type=Path)
    check.add_argument("--requests", required=True, type=Path)
    check.add_argument("--out", required=True, type=Path)
    export = commands.add_parser("export", help="Export an executable studio and fixed interpreter")
    export.add_argument("--model", required=True, type=Path)
    export.add_argument("--request", type=Path, help="Optional starting history; defaults to empty text/list and integer 3")
    export.add_argument("--evidence", type=Path, help="Optional recorded native check receipt")
    export.add_argument("--out", required=True, type=Path)
    return parser


def _spec(args):
    if not args.trust_factory:
        raise ValueError("Factory execution requires --trust-factory. It is local trusted execution, not a sandbox.")
    if type(args.budget) is not int or not 1 <= args.budget <= 250000:
        raise ValueError("Oracle budget must be 1..250000")
    return engine.validate_spec(product.read_json(args.surface))


def _compatible(model, spec):
    surface = {name: action["types"] for name, action in spec["actions"].items()}
    if model["factory"] != spec["id"] or model["constructor_types"] != spec["constructor_types"] or model["surface"] != surface:
        raise ValueError("Model identity and typed surface must match the declared oracle")
    if any(field != "iteration" and not engine._PATH.fullmatch(field[:-2] if field.endswith("()") else field) for field in model["fields"]):
        raise ValueError("Model observation fields must name public paths or iteration")


def learn(args):
    spec = _spec(args)
    if type(args.seed) is not int or not 0 <= args.seed <= 4294967295:
        raise ValueError("Seed must be an unsigned 32-bit integer")
    factory = product.load_factory(args.factory)
    try:
        result = engine.learn_with_evidence(factory, spec, seed=args.seed, query_budget=args.budget)
    except engine.Refusal as failure:
        # Refusal evidence contains no accepted model. Preserve measured attempted operations.
        product.write_bundle(args.out, {"refusal.json": product.data({"schema": "eidryl-refusal/1", "reason": str(failure), "evidence": failure.evidence})})
        print("Refused: " + str(failure), file=sys.stderr)
        return 2
    product.write_bundle(args.out, {"model.json": product.data(result["model"]), "surface.json": product.data(spec), "evidence.json": product.data(result["evidence"])})
    print(f"Learned {result['model']['training_records']} observations, {result['model']['latent_modes']} hidden modes. Output: {args.out}")
    print("Empirical model within a supplied grammar; run check on new histories before relying on it.")
    return 0


def check(args):
    spec = _spec(args)
    model = product.read_json(args.model)
    product.runtime(model)  # strict portable validation before any factory execution
    _compatible(model, spec)
    requests = product.requests(model, product.read_json(args.requests, 8 * 1024 * 1024))
    predicted = product.runtime(model, requests)
    factory = product.load_factory(args.factory)
    remaining = args.budget
    records = []
    refusal = None
    for request, answer in zip(requests, predicted):
        try:
            native = engine.observe(factory, spec, request["constructor"], request["actions"], fields=model["fields"], query_budget=remaining)
        except engine.Refusal as failure:
            refusal = {"reason": str(failure), "evidence": failure.evidence}
            if failure.evidence:
                remaining -= failure.evidence.get("charged_operations", 0)
            break
        remaining -= native["charged_operations"]
        records.append({"request": request, "native": native, "portable": answer, "comparison": product.compare(native, answer)})
        if remaining <= 0 and len(records) != len(requests):
            refusal = {"reason": "Total check oracle budget exhausted"}
            break
    mismatches = sum(len(r["comparison"]["mismatches"]) for r in records)
    passed = refusal is None and len(records) == len(requests) and mismatches == 0
    receipt = {"schema": "eidryl-check/1", "passed": passed, "model_sha256": product.digest(product.data(model)), "requested_histories": len(requests), "completed_histories": len(records), "transitions": sum(r["comparison"]["transitions"] for r in records), "mismatches": mismatches, "charged_operations": args.budget - remaining, "refusal": refusal, "records": records,
               "scope": "Recorded native comparisons on these histories. No universal equivalence, exception-message parity or future oracle proof."}
    product.write_bundle(args.out, {"check.json": product.data(receipt), "model.json": product.data(model), "requests.json": product.data(requests)})
    print(f"{'PASS' if passed else 'FAIL'}: {receipt['transitions']} transitions, {mismatches} mismatches, {len(records)}/{len(requests)} complete histories. Output: {args.out}")
    return 0 if passed else 1


def export(args):
    model = product.read_json(args.model)
    product.runtime(model)
    if args.request:
        request = product.read_json(args.request)
        product.requests(model, [request])
    else:
        # Models do not serialize constructor samples. These fixed defaults must pass
        # the model guard below; otherwise export requires an explicit --request.
        sample = {"str": "", "int": 3, "list": []}
        label = next(iter(model["surface"]))
        request = {"model": model["factory"], "constructor": [sample[t] for t in model["constructor_types"]], "actions": [[label, [sample[t] for t in model["surface"][label]]]]}
    starting = product.runtime(model, [request])[0]
    if not starting.get("ok"):
        raise ValueError("Starting history cannot initialize this model: " + starting.get("error", "unknown refusal") + ". Supply --request with a supported constructor.")
    recorded = product.read_json(args.evidence, 16 * 1024 * 1024) if args.evidence else None
    if recorded is not None and (type(recorded) is not dict or recorded.get("schema") != "eidryl-check/1" or recorded.get("model_sha256") != product.digest(product.data(model))):
        raise ValueError("Recorded check receipt must belong to this model")
    files = {name: (product.ASSETS / name).read_bytes() for name in ["index.html", "studio.mjs", "studio.css", "runtime.mjs", "runner.mjs", "README.md"]}
    files.update({"model.json": product.data(model), "request.json": product.data(request), "recorded.json": product.data(recorded), "README.txt": b"Eidryl portable model\nServe this directory with python3 -m http.server 8000 and open http://localhost:8000.\nThe studio predicts with the fixed interpreter. It does not run the original API.\nRecorded evidence is imported, unsigned data, not independent authentication.\nNode batch: node runner.mjs < batch.json, where batch has model and requests keys.\nNo dependency download, model API or account is required.\n", "LICENSE.txt": (product.ASSETS / "LICENSE.txt").read_bytes()})
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as handle:
        for name, raw in sorted(files.items()):
            member = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            member.compress_type = zipfile.ZIP_DEFLATED
            member.external_attr = 0o100644 << 16
            handle.writestr(member, raw)
    files["portable.zip"] = archive.getvalue()
    product.write_bundle(args.out, files)
    print("Exported executable studio and portable.zip: " + str(args.out))
    return 0


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        if args.out.exists() or args.out.is_symlink():
            raise FileExistsError("Output already exists; choose a new directory")
        return {"learn": learn, "check": check, "export": export}[args.command](args)
    except (engine.Refusal, ValueError, OSError, RecursionError, TimeoutError, subprocess.TimeoutExpired) as error:
        print("Refused: " + str(error)[:800], file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
