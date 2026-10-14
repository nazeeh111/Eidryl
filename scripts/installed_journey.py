"""Run from outside the checkout with an installed wheel and Node.js."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import zipfile

import eidryl
from eidryl import product


def main():
    root = Path(sys.argv[1]).resolve(strict=True)
    source_root = root / "src"
    assert not Path(eidryl.__file__).resolve().is_relative_to(source_root), "Requires an installed package"
    with tempfile.TemporaryDirectory(prefix="eidryl-installed-") as temporary:
        sandbox = Path(temporary)
        def cli(*args, expected=0):
            result = subprocess.run([sys.executable, "-I", "-m", "eidryl", *map(str, args)], cwd=sandbox, capture_output=True, text=True, timeout=180)
            assert result.returncode == expected, result.stdout + result.stderr
            return result
        for name in ["text", "sequence", "encoded"]:
            cli("check", "--factory", str(root / "examples/factories.py") + ":" + name,
                "--surface", root / "examples" / (name + ".surface.json"), "--trust-factory",
                "--model", root / "examples" / (name + ".model.json"),
                "--requests", root / "examples" / (name + ".requests.json"),
                "--out", sandbox / (name + "-check"), "--budget", "6000")
            assert product.read_json(sandbox / (name + "-check/check.json"))["passed"] is True
        factory = sandbox / "accumulator.py"
        factory.write_text("""class Accumulator:
    def __init__(self, initial): self.value = initial
    def increment(self, delta):
        self.value += delta
        return self.value
def create(initial): return Accumulator(initial)
""")
        spec = {"schema": "eidryl-surface/1", "id": "installed-accumulator", "constructor_types": ["int"], "constructor_samples": [[0], [4], [-3]], "actions": {"r47": {"path": "increment", "types": ["int"]}}, "prefix_depth": 1, "int_samples": [-3, 0, 2, 7]}
        spec_path = sandbox / "surface.json"
        spec_path.write_bytes(product.data(spec))
        cli("learn", "--factory", str(factory) + ":create", "--surface", spec_path,
            "--trust-factory", "--budget", "6000", "--out", sandbox / "learned")
        model_path = sandbox / "learned/model.json"
        request = {"model": "installed-accumulator", "constructor": [19], "actions": [["r47", [37]], ["r47", [-11]]]}
        requests_path = sandbox / "requests.json"
        requests_path.write_bytes(product.data([request]))
        cli("check", "--factory", str(factory) + ":create", "--surface", spec_path,
            "--trust-factory", "--model", model_path, "--requests", requests_path,
            "--out", sandbox / "checked")
        measured = product.read_json(sandbox / "checked/check.json")
        assert [r["value"] for r in measured["records"][0]["native"]["trace"]] == [56, 45]
        request_path = sandbox / "request.json"
        request_path.write_bytes(product.data(request))
        cli("export", "--model", model_path, "--request", request_path,
            "--evidence", sandbox / "checked/check.json", "--out", sandbox / "portable")
        portable = sandbox / "portable"
        manifest = product.read_json(portable / "manifest.json")
        for name, expected in manifest["files"].items():
            raw = (portable / name).read_bytes()
            assert product.digest(raw) == expected["sha256"] and len(raw) == expected["bytes"]
        with zipfile.ZipFile(portable / "portable.zip") as archive:
            assert archive.testzip() is None
            assert len(archive.namelist()) == 11
            for name in archive.namelist():
                assert "/" not in name and archive.read(name) == (portable / name).read_bytes()
        response = product.runtime(product.read_json(model_path), [request])[0]
        assert response["ok"] is True and [row["value"] for row in response["trace"]] == [56, 45]
        cli("learn", "--factory", str(factory) + ":create", "--surface", spec_path,
            "--trust-factory", "--budget", "1", "--out", sandbox / "refused", expected=2)
        assert not (sandbox / "refused/model.json").exists()
        assert product.read_json(sandbox / "refused/refusal.json")["evidence"]["exhausted"] is True
        print("PASS installed package: 3 native models, new opaque-action factory learn/check/export, ZIP members/hashes, sticky refusal; no browser claim.")


if __name__ == "__main__":
    main()
