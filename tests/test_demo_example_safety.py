"""The synthetic example must never inherit a target: it writes real memories.

These tests are offline: they never contact Redis, Qdrant or any endpoint. They
exist because the example, when first written, silently defaulted to
`http://localhost:6333` / `localhost:6379` — which on a working machine is the
live installation, not a lab.
"""
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "demo_decision_recall.py"
TARGET_KEYS = ("QDRANT_URL", "QDRANT_COLLECTION", "COLLECTION_NAME", "QDRANT_API_KEY",
               "REDIS_HOST", "REDIS_PORT", "REDIS_PASSWORD", "EMBEDDING_API_BASE",
               "EMBEDDING_MODEL", "EMBEDDING_DIMS", "EMBEDDING_API_KEY",
               "OPENROUTER_API_KEY", "ICARUS_SPARSE_QUERY_ENABLED", "FASTEMBED_VENV",
               "FASTEMBED_SITEPKGS", "HERMES_HOME", "STATE_DB_PATH", "WIKI_PATH")


def scrubbed_env(**overrides):
    env = {k: v for k, v in os.environ.items() if k not in TARGET_KEYS}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(overrides)
    return env


def run_script(args=(), **overrides):
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          cwd=ROOT, env=scrubbed_env(**overrides),
                          capture_output=True, text=True, timeout=60)


class DemoExampleTargetTests(unittest.TestCase):
    def test_refuses_to_run_without_an_explicit_target(self):
        p = run_script()
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        out = p.stdout
        self.assertIn("ABORTADO", out)
        for name in ("QDRANT_URL", "REDIS_HOST", "EMBEDDING_API_BASE", "COLLECTION_NAME"):
            self.assertIn(name, out)
        # Nothing may be enqueued or written: it fails before touching the stack.
        self.assertNotIn("registrando", out)

    def test_partial_configuration_is_still_refused(self):
        p = run_script(QDRANT_URL="http://127.0.0.1:28333")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("REDIS_HOST", p.stdout)
        self.assertIn("EMBEDDING_API_BASE", p.stdout)
        self.assertNotIn("QDRANT_URL=", p.stdout.split("Faltando:")[1])

    def test_cleanup_also_requires_an_explicit_target(self):
        p = run_script(["--cleanup"], )
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("ABORTADO", p.stdout)

    def test_target_config_accepts_both_collection_variable_names(self):
        sys.path.insert(0, str(ROOT))
        from scripts import demo_decision_recall as demo
        base = dict(QDRANT_URL="http://127.0.0.1:28333", REDIS_HOST="127.0.0.1",
                    EMBEDDING_API_BASE="http://127.0.0.1:8080/v1")
        saved = {k: os.environ.get(k) for k in (*TARGET_KEYS, "QDRANT_COLLECTION")}
        try:
            os.environ.update(base)
            os.environ["COLLECTION_NAME"] = "knowledge_base_lab"
            os.environ.pop("QDRANT_COLLECTION", None)
            collection, missing = demo.target_config()
            self.assertEqual(collection, "knowledge_base_lab")
            self.assertEqual(missing, [])
            os.environ.pop("COLLECTION_NAME", None)
            os.environ["QDRANT_COLLECTION"] = "knowledge_base_lab"
            self.assertEqual(demo.target_config(), ("knowledge_base_lab", []))
            os.environ.pop("QDRANT_COLLECTION", None)
            collection, missing = demo.target_config()
            self.assertEqual(collection, "")
            self.assertIn("COLLECTION_NAME (or QDRANT_COLLECTION)", missing)
        finally:
            for key in (*TARGET_KEYS, "QDRANT_COLLECTION"):
                os.environ.pop(key, None)
                previous = saved.get(key)
                if previous is not None:
                    os.environ[key] = previous

    def test_source_has_no_baked_in_target(self):
        source = SCRIPT.read_text()
        self.assertNotIn("localhost", source)
        # The usage example points at a lab endpoint, not at a default install.
        self.assertIn("127.0.0.1:28333", source)
        self.assertNotIn('"http://localhost', source)

    def test_negative_control_cannot_silently_pass_on_a_tiny_collection(self):
        source = SCRIPT.read_text()
        self.assertIn("NAO CONCLUSIVO", source)
        self.assertIn("INJECTION_WINDOW", source)
        self.assertIn("inconclusive", source)

    def test_cleanup_documents_its_filter(self):
        source = SCRIPT.read_text()
        self.assertIn('"key": "tags"', source)
        self.assertIn('"exact": True', source)


if __name__ == "__main__":
    unittest.main()
