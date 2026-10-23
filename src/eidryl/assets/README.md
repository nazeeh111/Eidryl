# Eidryl

**Learn portable executable models from a running stateful API.**

A stream remembers a cursor. A bounded queue changes how it drops elements. A text encoder can remember something invisible in its public state. Eidryl experiments with an explicitly trusted constructor, learns data transformations and hidden modes, then runs the resulting model in JavaScript without the original component.

The first release targets resettable local Python objects with typed text, integer and string-list inputs. It is a tool for creating inspectable test doubles and investigating behavior, with an elementary supplied grammar. It does not replicate arbitrary applications or devices.

## Try it

Python 3.11+ runs the learner. Node 20+ validates and checks the portable runtime. There are no Python runtime package dependencies, accounts, billed model APIs or remote execution services.

This portable folder already contains the browser lab. Serve it with `python -m http.server 8000` and open http://localhost:8000. Learning a new model needs the Python CLI and source examples:

```sh
git clone https://github.com/nazeeh111/Eidryl.git
cd Eidryl
python -m pip install --no-deps .
eidryl --help
```

Alternatively, download the wheel from the [v0.1.0 release](https://github.com/nazeeh111/Eidryl/releases/tag/v0.1.0) and install the file from where you saved it. The examples below still need the source checkout; this portable folder does not include a `dist/` wheel or the example factories.

From the source directory, declare a public API and learn from its constructor:

```sh
eidryl learn --factory examples/factories.py:text \
  --surface examples/text.surface.json --trust-factory \
  --budget 150000 --out learned-text
eidryl check --factory examples/factories.py:text \
  --surface examples/text.surface.json --trust-factory \
  --model learned-text/model.json --requests examples/text.requests.json \
  --budget 6000 --out checked-text
eidryl export --model learned-text/model.json \
  --evidence checked-text/check.json --out text-studio
python -m http.server 8000 --directory text-studio
```

If the default constructor is outside the learned guard, export refuses before writing; provide `--request` with a supported starting history.

Open http://localhost:8000. The studio executes the actual learned model. Edit a constructor or action history, inspect each return and state transition, follow hidden modes, inspect the inferred expressions, import a model, and download a session. Running an edited browser history produces a prediction, not a fresh native comparison.

`--trust-factory` acknowledges that the selected local Python file executes with your own authority. Eidryl is not a sandbox. Only experiment with code and resources you control and trust. The factory must create a fresh resettable object; external effects are outside the model.

## How it learns

1. A surface file supplies constructor samples, argument types and public action paths. It supplies no method-effect implementation. Labels can be opaque.
2. The learner discovers bounded public observations and empirically checks repeated queries and future behavior. Observation purity is tested on finite histories, not proven.
3. It probes histories and chooses representative states across sequence lengths, integer positions and learned modes. A finite suffix experiment can expose hidden state when the same visible state and action produce different outputs.
4. It infers typed expression trees for constructor guards, returns, state updates, invalid-operation guards and mode transitions. Sequence edits and queue rotations are assembled from elementary slices, concatenation and arithmetic, without whole-operation splice or rotate templates.
5. A fixed data-only interpreter runs the model. `check` compares complete histories with real native execution and requires exact recursive types and every expected response.

The supplied algebra, UTF-8/UTF-16 codecs, manual signatures and constructor samples are priors. This is empirical bounded synthesis, not a new general learning algorithm or a universal equivalence proof. Errors outside learned support refuse; their wording does not reproduce native exception messages.

## Budgets and failure

The default 6,000-operation budget is a real ceiling. The exhaustive included native recipes need an explicit larger budget: the measured text recipe charged 35,612 calls, queue 8,313 and encoder 138,786. Charges include factory invocations, public attribute inspection, actions, observations and required cleanup. They do not measure the target's internal CPU work.

An API declaration whose ordinary or boundary argument grid exceeds 128 combinations refuses before target execution; reduce the supplied sample sets or arity.

A failed learning run writes `refusal.json` with measured evidence and no accepted model. Output directories must be new; existing work is never overwritten. A check failure retains native and portable records and returns a failing exit status. A successful check only supports the tested histories.

Portable models are JSON, never imported code or `eval`. Schemas, action shapes, Unicode, expression depth, evaluation steps, materialized values and response sizes are bounded. The portable studio is a directory served over ordinary HTTP; an actual offline `file://` browser journey has not been verified.

## Evidence

Native tests cover CPython `io.StringIO`, `collections.deque`, and a UTF-16 `io.TextIOWrapper` backed by `io.BytesIO`. These are real runtime targets, not simulated hardware. Their source implementation is not used for inference.

Earlier proof snapshots are retained privately as failed evidence: independent review found a beyond-EOF cursor rule, malformed-action acceptance, insufficient checking, and an underdetermined encoder suffix rule. Those failures drove generic inference and checker changes. Current release validation is reported in the release evidence, with measured regressions separated from newly selected histories. No universal correctness, measured 10x improvement, outside adoption or security credit is claimed.

## Surface format

```json
{
  "schema": "eidryl-surface/1",
  "id": "my-component",
  "constructor_types": ["str"],
  "constructor_samples": [[""], ["hello"], ["βeta"]],
  "actions": {"advance": {"path": "write", "types": ["str"]}}
}
```

Supported types: `str`, `int` (bounded integral JSON values), and `list` of strings. Optional `text_samples`, `int_samples`, `list_samples`, and `prefix_depth` customize finite experiments. Public dotted paths may reach child objects. A declared action name is an identity, not a built-in semantic operation.

Check requests are a JSON list of `{ "model": "my-component", "constructor": ["initial"], "actions": [["advance", ["new"]]] }`. All request shapes and types are validated before the factory runs. Python APIs `learn`, `learn_with_evidence`, `observe`, and `validate_spec` are available from `eidryl.engine`.

## Development and provenance

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py' -v
node --test tests/runtime.test.mjs tests/studio.test.mjs
```

Implementation is MIT licensed. Active automata learning and symbolic transducer synthesis are established areas; see [AALpy](https://github.com/DES-Lab/AALpy), [LearnLib](https://learnlib.de/), and [active learning of string transducers](https://arxiv.org/abs/2104.11758). Eidryl's contribution is this bounded experiment, inference, validation and portable inspection workflow.
