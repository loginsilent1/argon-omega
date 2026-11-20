"""template_refs.py - the verbose answers the templates replace.

These exist for exactly one reason: the "800 tokens -> 60 tokens" claim must be a
tokenization of two real texts, not an opinion. Each constant is a competent, complete
answer to the same request a template handles - the kind of answer a helpful model
actually writes, with prose, notes and caveats.

Measured with the tokenizer reported by templates.TOKENIZER (see bench_all.bench_templates).
Nothing here is executed; it is reference text only.
"""

RETRY = '''Here's a complete example of adding a retry decorator with exponential backoff to your function.

First, let's create the decorator itself:

```python
import time
import functools
import logging

logger = logging.getLogger(__name__)


def with_retry(attempts: int = 3, base_delay: float = 0.2, max_delay: float = 30.0,
               exceptions: tuple = (Exception,)):
    """Retry a function with exponential backoff on the given exceptions.

    Args:
        attempts: total number of attempts, including the first one
        base_delay: delay before the second attempt, doubled each time
        max_delay: ceiling for the delay so a long outage does not sleep forever
        exceptions: which exceptions are treated as transient
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt == attempts:
                        logger.error("%s failed after %d attempts", fn.__name__, attempts)
                        raise
                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    logger.warning("%s failed (%s), retrying in %.1fs",
                                   fn.__name__, exc, delay)
                    time.sleep(delay)
            raise last_exc
        return wrapper
    return decorator
```

Now apply it to your function:

```python
@with_retry(attempts=3)
def fetch_user(user_id: int) -> dict:
    ...
```

Notes on the design:

- Only wrap operations that are safe to repeat. `fetch_user` is a read, so retrying is
  fine; wrapping a payment call would double-charge on a timeout.
- The delay is capped with `max_delay` so a 12-hour outage does not produce a sleep of
  several hours.
- `functools.wraps` preserves the docstring and signature, which keeps FastAPI and
  `inspect.signature` working.
- If you want jitter to avoid a thundering herd, multiply the delay by
  `random.uniform(0.5, 1.5)` before sleeping.

If you tell me which exception you want to treat as transient, I can narrow the
`exceptions` tuple so a `ValueError` is not retried pointlessly.
'''

TEST = '''Here is a pytest module covering the happy path and the failure modes that actually
break in production.

```python
import pytest

from config import parse_config, ConfigError


def test_parse_config_happy_path(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"name": "demo", "size": 3}')
    cfg = parse_config(path)
    assert cfg.name == "demo"
    assert cfg.size == 3


def test_parse_config_uses_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"name": "demo"}')
    cfg = parse_config(path)
    assert cfg.size == 10          # documented default


def test_parse_config_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError):
        parse_config(tmp_path / "nope.json")


def test_parse_config_rejects_bad_json(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json")
    with pytest.raises(ConfigError):
        parse_config(path)


@pytest.mark.parametrize("raw", ["[]", "null", "42"])
def test_parse_config_rejects_non_object(tmp_path, raw):
    path = tmp_path / "config.json"
    path.write_text(raw)
    with pytest.raises(ConfigError):
        parse_config(path)
```

Why these tests:

- the happy path pins the return type, so a refactor to a plain dict fails loudly instead
  of silently changing every caller
- the defaults test documents behaviour that otherwise only lives in the code
- the two error cases are the ones that show up in production, usually as an unhandled
  exception in a service that should have started with a clear message
- parametrising the non-object cases keeps it to one test body instead of three

If `parse_config` also accepts a raw string, add one test for that entry point, otherwise
the branch stays uncovered and a future refactor can break it without any test noticing.
'''

ERROR = '''**What it is:** `KeyError` means a dict lookup used a key that does not exist. It is not a
type problem and it is not a bug in Python — the key simply is not in the mapping.

**Usual cause:** three shapes account for almost all of them:

1. reading `os.environ["TOKEN"]` when the variable was never exported
2. reading a JSON payload with a field the producer renamed
3. iterating and deleting from the same dict

**Fix:** use `.get()` when the key is optional and keep `[]` when it is required, so the
failure stays loud instead of turning into a `None` that fails three frames later:

```python
token = os.environ.get("TOKEN")
if token is None:
    raise RuntimeError("TOKEN is not set; export it before starting the app")
```

For the JSON case, validate at the boundary instead of sprinkling `.get()` everywhere:

```python
required = {"name", "size"}
missing = required - payload.keys()
if missing:
    raise ValueError(f"payload missing {sorted(missing)}")
```

**Check:** run the same input against the fixed code in a test that asserts the message,
so a future rename produces a readable error instead of a `KeyError` traceback.

If you paste the traceback's last frame — the file and line — I can point at the exact
lookup instead of the common causes.
'''

RENAME = '''Renaming `foo` to `bar` touches three things: the definition, the call sites, and any
string-based references.

**1. The diff**

```diff
-def foo(x, y):
+def bar(x, y):
     return x + y
```

**2. Call sites**

```bash
grep -rn "\\bfoo\\b" --include="*.py" .
```

Update each hit. A word-boundary grep matters: `foobar` and `foo_helper` must not match,
and a plain `sed s/foo/bar/` would corrupt both of them.

**3. String-based references**

Anything that looks the function up by name will not fail at import time — it will fail
later, at runtime, often in production:

```python
getattr(module, "foo")          # -> must become "bar"
HANDLERS = {"foo": bar}         # dict keys
patch("mymod.foo")              # unittest.mock target strings
```

**4. Verify**

```bash
python -m pytest -q
python -m mypy .
```

Then `git grep foo` again — it should return only the historical comment you intentionally
kept, if any.

Prefer an IDE rename or `pyright`/`rope` refactor over manual editing: it updates the
string references that `sed` cannot see.
'''

REFS = {"RETRY": RETRY, "TEST": TEST, "ERROR": ERROR, "RENAME": RENAME}
