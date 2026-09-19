import contextlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts import local_memory as memory
from setup.prepare_native import render

class LocalMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        (self.root/'wiki').mkdir();(self.root/'documents').mkdir()
        (self.root/'wiki/a.md').write_text('Unique quasar project document '+ 'orbit '*30)
        for name,value in [('ROOT',self.root),('DATA',self.root/'memory-index'),('DIMS',3)]:
            p=patch.object(memory,name,value);p.start();self.addCleanup(p.stop)

    def test_lexical_index_is_idempotent(self):
        self.assertEqual(memory.sync(lexical_only=True)['indexed'],1)
        self.assertEqual(memory.sync(lexical_only=True)['unchanged'],1)
        self.assertTrue(memory.search('quasar',semantic=False))

    def test_failed_embedding_does_not_advance_vector_checkpoint(self):
        with patch.object(memory,'prefetch'),patch.object(memory,'embed',side_effect=RuntimeError('offline')):
            self.assertEqual(memory.sync()['failed'],1)
        con=memory.connection()
        self.assertEqual(con.execute('SELECT count(*) FROM files').fetchone()[0],0);con.close()

    def test_vectors_retry_replace_and_delete_in_local_store(self):
        with patch.object(memory,'prefetch'),patch.object(memory,'embed',side_effect=lambda texts:[[0.1,0.2,0.3] for _ in texts]):
            self.assertEqual(memory.sync()['vectors'],1)
            self.assertEqual(memory.sync()['unchanged'],1)
            (self.root/'wiki/a.md').write_text('changed pulsar document')
            self.assertEqual(memory.sync()['vectors'],1)
            self.assertFalse(memory.search('quasar',semantic=False))
            self.assertTrue(memory.search('pulsar',semantic=False))
            (self.root/'wiki/a.md').unlink()
            self.assertEqual(memory.sync()['deleted'],1)

class NativeTests(unittest.TestCase):
    def test_generate_and_refuse_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);output=root/'staging'
            args=dict(output=output,profile=root/'profile',wiki=root/'wiki',repo=root/'repo',python=root/'python',qdrant=root/'qdrant',redis=root/'redis',name='lab',qdrant_port=26333,redis_port=26379)
            render(**args)
            self.assertFalse((root/'profile').exists())
            self.assertIn('WORKER_WIKI_ROOT=',(output/'worker.env').read_text())
            self.assertIn('noeviction',(output/'redis.conf').read_text())
            self.assertEqual(len(list(output.glob('*.service'))),3)
            with self.assertRaises(ValueError):render(**args)
