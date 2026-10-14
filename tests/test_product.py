"""Failures that could hide false parity or overwrite user work."""
import json
from pathlib import Path
import tempfile
import unittest

from eidryl import product, cli


class ProductContracts(unittest.TestCase):
    def test_json_rejects_duplicate_and_nonfinite_records(self):
        for data in ['{"a":1,"a":2}', '{"x":NaN}', '{"x":Infinity}']:
            with self.assertRaises(ValueError):
                product.loads(data)

    def test_recursive_equality_distinguishes_boolean_and_integer(self):
        self.assertFalse(product.equal([False, {"n": 1}], [0, {"n": True}]))
        self.assertFalse(product.equal({"bytes": "QQ=="}, {"bytes": "Qg=="}))
        self.assertTrue(product.equal({"a": [1, "β"]}, {"a": [1, "β"]}))

    def test_checker_refuses_missing_trace_and_type_corruption(self):
        native = {"initial": [0], "trace": [{"accepted": True, "value": 1, "state": [1]}]}
        good = {"ok": True, "initial": [0], "trace": native["trace"]}
        self.assertEqual(product.compare(native, good)["mismatches"], [])
        for bad in [{"ok": True, "initial": [0], "trace": []},
                    {"ok": True, "initial": [0], "trace": [{"accepted": True, "value": True, "state": [1]}]},
                    {"ok": True, "trace": native["trace"]}, {"ok": False, "error": "no response"}]:
            self.assertTrue(product.compare(native, bad)["mismatches"])

    def test_no_clobber_and_manifest_bind_every_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "artifact"
            product.write_bundle(out, {"hello.txt": b"original"})
            with self.assertRaises(FileExistsError):
                product.write_bundle(out, {"hello.txt": b"changed"})
            self.assertEqual((out / "hello.txt").read_bytes(), b"original")
            manifest = json.loads((out / "manifest.json").read_text())
            self.assertEqual(manifest["files"]["hello.txt"]["sha256"], product.digest(b"original"))
            self.assertEqual(manifest["files"]["hello.txt"]["bytes"], 8)
            for unsafe in ["../escape", "/absolute", "manifest.json", "nested/file"]:
                with self.assertRaises(ValueError):
                    product.write_bundle(Path(temporary) / "bad", {unsafe: b"x"})
            self.assertFalse((Path(temporary) / "bad").exists())

    def test_requests_validate_completely_before_oracle_use(self):
        model = {"factory": "test", "constructor_types": ["int"], "surface": {"go": ["int"]}}
        self.assertEqual(product.requests(model, [{"model": "test", "constructor": [3.0], "actions": [["go", [2.0]]]}])[0]["constructor"], [3])
        for bad in [[], [{"model": "test", "constructor": [True], "actions": []}],
                    [{"model": "test", "constructor": [1], "actions": [["go", [], "extra"]]}],
                    [{"model": "other", "constructor": [1], "actions": []}],
                    [{"model": "test", "constructor": [1], "actions": [["unknown", []]]}]]:
            with self.assertRaises(ValueError):
                product.requests(model, bad)

    def test_invalid_seed_and_observation_path_refuse_before_factory_import(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / "executed"
            factory = root / "factory.py"
            factory.write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('executed')\ndef factory(value): return None\n")
            surface = {"schema": "eidryl-surface/1", "id": "guard", "constructor_types": ["str"], "constructor_samples": [[""]], "actions": {"read": {"path": "read", "types": []}}}
            spec_path = root / "surface.json"
            spec_path.write_bytes(product.data(surface))
            code = cli.main(["learn", "--factory", str(factory) + ":factory", "--surface", str(spec_path), "--trust-factory", "--seed", "-1", "--out", str(root / "out")])
            self.assertEqual(code, 2)
            self.assertFalse(marker.exists())
            malformed = {"factory": "guard", "constructor_types": ["str"], "surface": {"read": []}, "fields": ["_private"]}
            with self.assertRaises(ValueError):
                cli._compatible(malformed, surface)
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
