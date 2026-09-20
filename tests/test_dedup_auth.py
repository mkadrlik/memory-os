"""Regression tests for authenticated pre-write deduplication.

Defect being guarded against (observed 2026-09-20 in the isolated lab): the dedup
search in ``tasks/file_ingestion.upsert_with_dedup`` posted to Qdrant without the
API key. With authentication enabled Qdrant answers 401, the generic
``except Exception`` swallowed it, and the code fell through to a plain insert — a
silent loss of deduplication.

These tests use mocked HTTP (no network, no live Qdrant). The mocked transport is
deliberately narrow: it covers the decision logic (headers, 401/403 handling, merge
vs insert). The real integration against an authenticated Qdrant is covered by the
lab run, not by this file.
"""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
configured_root = os.environ.get("MEMORY_OS_ROOT", "").strip()
if configured_root and Path(configured_root).expanduser().resolve() != ROOT:
    raise RuntimeError(
        "MEMORY_OS_ROOT points outside the checkout under test. "
        "Run python scripts/test_offline.py to use an isolated environment, "
        "or unset MEMORY_OS_ROOT before running unittest directly."
    )
sys.path[:0] = [str(ROOT), str(ROOT / "docker/worker")]

from tasks import file_ingestion as fi  # noqa: E402


class _Response:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {"result": []}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)

    def json(self):
        return self._payload


def _client(response=None, side_effect=None):
    """Stand-in for ``httpx.AsyncClient`` used as an async context manager."""
    inner = MagicMock()
    inner.post = AsyncMock(return_value=response, side_effect=side_effect)
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=inner)
    client.__aexit__ = AsyncMock(return_value=False)
    client._inner = inner
    return client


def _qdrant():
    q = MagicMock()
    q.set_payload = AsyncMock()
    q.upsert = AsyncMock()
    return q


class DedupAuthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._key = fi.QDRANT_API_KEY
        self.addCleanup(lambda: setattr(fi, "QDRANT_API_KEY", self._key))

    async def _run(self, response=None, key="secret-key", side_effect=None,
                   sparse_vector=None):
        """Returns (result, auth_error, http_client, qdrant_mock).

        ``sparse_vector`` defaults to a valid BM25-shaped payload because the
        worker always passes one: ``get_sparse_embedding`` raises if the model is
        unavailable and never returns None, so there is no dense-only fallback on
        the ingestion path.
        """
        if sparse_vector is None:
            sparse_vector = {"indices": [1, 7], "values": [0.5, 0.5]}
        fi.QDRANT_API_KEY = key
        client = _client(response, side_effect)
        qdrant = _qdrant()
        result, auth_error = None, None
        with patch.object(fi.httpx, "AsyncClient", return_value=client):
            try:
                result = await fi.upsert_with_dedup(
                    qdrant=qdrant,
                    collection="knowledge_base_lab",
                    dense_vector=[1.0, 0.0, 0.0, 0.0],
                    sparse_vector=sparse_vector,
                    payload={"text": "x", "tags": ["a"],
                             "created_at": "2026-01-01T00:00:00Z"},
                )
            except fi.QdrantAuthError as exc:
                auth_error = exc
        return (result or {}), auth_error, client, qdrant

    # ── header presence ───────────────────────────────────────────────────────
    async def test_sends_api_key_header_when_configured(self):
        _, _, client, _ = await self._run(_Response(200, {"result": []}))
        sent = client._inner.post.call_args.kwargs["headers"]
        self.assertEqual(sent.get("api-key"), "secret-key")
        self.assertEqual(sent.get("Content-Type"), "application/json")

    async def test_omits_api_key_header_when_not_configured(self):
        _, _, client, _ = await self._run(_Response(200, {"result": []}), key="")
        sent = client._inner.post.call_args.kwargs["headers"]
        self.assertNotIn("api-key", sent)

    # ── auth failure must not degrade into a silent insert ────────────────────
    async def test_401_fails_identifiably_without_upsert(self):
        result, error, _, qdrant = await self._run(_Response(401, {"status": "error"}))
        self.assertIsNotNone(error, "401 must raise QdrantAuthError")
        self.assertIn("401", str(error))
        self.assertEqual(result, {})
        qdrant.upsert.assert_not_called()
        qdrant.set_payload.assert_not_called()

    async def test_403_fails_identifiably_without_upsert(self):
        result, error, _, qdrant = await self._run(_Response(403, {"status": "error"}))
        self.assertIsNotNone(error, "403 must raise QdrantAuthError")
        self.assertIn("403", str(error))
        self.assertEqual(result, {})
        qdrant.upsert.assert_not_called()
        qdrant.set_payload.assert_not_called()

    # ── happy paths ───────────────────────────────────────────────────────────
    async def test_initial_insert_when_nothing_similar(self):
        result, error, _, qdrant = await self._run(_Response(200, {"result": []}))
        self.assertIsNone(error)
        self.assertEqual(result["status"], "upserted")
        qdrant.upsert.assert_awaited_once()
        qdrant.set_payload.assert_not_called()

    async def test_duplicate_above_threshold_is_merged(self):
        hit = {"id": "abc", "score": 0.97,
               "payload": {"tags": ["old"], "source_type": "ai", "importance_score": 0.4}}
        result, error, _, qdrant = await self._run(_Response(200, {"result": [hit]}))
        self.assertIsNone(error)
        self.assertEqual(result["status"], "dedup")
        self.assertEqual(result["existing_id"], "abc")
        qdrant.set_payload.assert_awaited_once()
        qdrant.upsert.assert_not_called()

    async def test_below_threshold_inserts(self):
        hit = {"id": "abc", "score": 0.10, "payload": {"tags": []}}
        result, _, _, qdrant = await self._run(_Response(200, {"result": [hit]}))
        self.assertEqual(result["status"], "upserted")
        qdrant.upsert.assert_awaited_once()

    async def test_insert_carries_dense_and_sparse_vectors(self):
        """The inserted point must carry both vectors, as the collection expects."""
        result, error, _, qdrant = await self._run(_Response(200, {"result": []}))
        self.assertIsNone(error)
        self.assertEqual(result["status"], "upserted")
        point = qdrant.upsert.call_args.kwargs["points"][0]
        self.assertEqual(point.vector["dense"], [1.0, 0.0, 0.0, 0.0])
        # PointStruct normalises the sparse payload into a SparseVector model
        self.assertEqual(list(point.vector["sparse"].indices), [1, 7])

    async def test_transient_error_still_falls_back_to_insert(self):
        """A non-auth transport error keeps the historical fail-open behaviour."""
        result, error, _, qdrant = await self._run(side_effect=httpx.ConnectError("boom"))
        self.assertIsNone(error)
        self.assertEqual(result["status"], "upserted")
        qdrant.upsert.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
