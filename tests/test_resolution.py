import os
from contextlib import closing
from pathlib import Path
import subprocess
import sys
import unittest

from qdrant_client import QdrantClient, models
from scripts.resolve_contradiction import resolve, resolution_payload


class ResolutionTests(unittest.TestCase):
    def test_persist_preview_history_and_repeated_resolution(self):
        with closing(QdrantClient(':memory:')) as client:
            client.create_collection('test', vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE))
            original = {'text': 'evidence', 'confidence_score': 0.6,
                        'reflection_notes': '[CONTRADICTION HIGH] old evidence',
                        'reflection_count': 3, 'last_reflected': '2026-09-19',
                        'contradiction_unresolved': True}
            client.upsert('test', [models.PointStruct(id=1, vector=[1., 0.], payload=original)])
            def read():
                return client.retrieve('test', [1], with_vectors=True)[0]
            resolve(client, 'test', 1, actor='reviewer', reason='verified source')
            self.assertEqual(read().payload, original)
            resolve(client, 'test', 1, actor='reviewer', reason='verified source', apply=True)
            point = read()
            self.assertEqual(point.payload['confidence_score'], 0.6)
            self.assertEqual(point.payload['text'], 'evidence')
            self.assertEqual(point.vector, [1., 0.])
            self.assertEqual(point.payload['reflection_count'], 0)
            self.assertFalse(point.payload['contradiction_unresolved'])
            event = point.payload['contradiction_resolutions'][0]
            self.assertEqual(event['previous']['reflection_notes'], original['reflection_notes'])
            self.assertEqual(event['previous']['reflection_count'], 3)
            self.assertEqual(event['actor'], 'reviewer')
            self.assertEqual(event['reason'], 'verified source')
            with self.assertRaises(ValueError):
                resolve(client, 'test', 1, actor='reviewer', reason='again', apply=True)
            client.set_payload('test', {'contradiction_unresolved': True}, points=[1])
            resolve(client, 'test', 1, actor='reviewer', reason='second review', apply=True)
            self.assertEqual(read().payload['contradiction_resolutions'][0], event)
            self.assertEqual(len(read().payload['contradiction_resolutions']), 2)
            with self.assertRaises(ValueError):
                resolve(client, 'test', 2, actor='reviewer', reason='missing', apply=True)

    def test_invalid_input_and_legacy_markers(self):
        for marker in ('[CONTRADICTION HIGH] evidence', '[CONSISTENT frozen] evidence'):
            payload = {'reflection_notes': marker}
            self.assertFalse(resolution_payload(payload, actor='a', reason='r')['contradiction_unresolved'])
            for actor, reason in (('', 'r'), ('a', '   ')):
                with self.assertRaises(ValueError):
                    resolution_payload(payload, actor=actor, reason=reason)
            with self.assertRaises(ValueError):
                resolution_payload({**payload, 'contradiction_resolutions': {}}, actor='a', reason='r')

    def test_foreign_checkout_fails_before_importing_icarus(self):
        root = Path(__file__).resolve().parents[1]
        env = dict(os.environ, MEMORY_OS_ROOT=str(root.parent / 'foreign-checkout'))
        code = '''
import sys
try:
    import tests.test_consolidation
except RuntimeError as exc:
    assert 'MEMORY_OS_ROOT points outside' in str(exc)
    assert 'icarus' not in sys.modules
else:
    raise AssertionError('foreign checkout accepted')
'''
        result = subprocess.run([sys.executable, '-c', code], cwd=root, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
