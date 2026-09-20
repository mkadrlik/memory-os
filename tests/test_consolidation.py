"""Offline regression contracts; all writes are confined to temporary paths."""
import asyncio
import datetime
import importlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch, AsyncMock, Mock

ROOT = Path(__file__).resolve().parents[1]
configured_root = os.environ.get('MEMORY_OS_ROOT', '').strip()
if configured_root and Path(configured_root).expanduser().resolve() != ROOT:
    raise RuntimeError(
        'MEMORY_OS_ROOT points outside the checkout under test. '
        'Run python scripts/test_offline.py to use an isolated environment, '
        'or unset MEMORY_OS_ROOT before running unittest directly.'
    )
sys.path[:0] = [str(ROOT), str(ROOT/'scripts'), str(ROOT/'docker/worker')]
from tasks import reflection as r
from services import llm
from tasks.file_ingestion import get_tags_from_frontmatter
from icarus import hooks, state, lifecycle
from scripts import wiki_continuous_ingest as wi
from scripts import context_enhancer as ce
import hermes_env

class JsonTests(unittest.TestCase):
    def test_valid_wrapped_fenced_and_braces_in_strings(self):
        verdict = {'contradiction_found': False, 'severity': 'low', 'explanation': 'quoted } and { braces'}
        for value in (verdict, {'result': verdict}):
            self.assertEqual(r._extract_json_object('prose ```json\n'+json.dumps(value)+'```'), verdict)

    def test_invalid_schema_is_not_a_verdict(self):
        for obj in ({}, {'contradiction_found':'false'}, {'contradiction_found':0},
                    {'contradiction_found':False,'severity':'invented','explanation':'ok'},
                    {'contradiction_found':False,'severity':'low','explanation':''}):
            self.assertIsNone(r._extract_json_object(json.dumps(obj)))
        self.assertIsNone(r._extract_json_object('reasoning without any verdict'))

    def test_yaml_scalars(self):
        self.assertEqual(get_tags_from_frontmatter({'tags':['one',datetime.date(2026,6,12),3,False,None]}), ['one','2026-06-12','3','False'])

class ReflectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_repair_then_abstention(self):
        good = json.dumps({'contradiction_found':False,'severity':'low','explanation':'supported'})
        with patch.object(r,'ollama_chat',AsyncMock(side_effect=['invalid',good])) as call:
            verdict, attempt = await r._analyze_chunk('prompt')
            self.assertEqual(attempt,2); self.assertFalse(verdict['contradiction_found'])
            self.assertEqual(call.call_count,2)
        with patch.object(r,'ollama_chat',AsyncMock(return_value='invalid')):
            self.assertEqual(await r._analyze_chunk('prompt'),(None,0))

    async def test_batch_invalid_json_creates_no_points(self):
        q=NS(scroll=AsyncMock(return_value=([NS(id=42,payload={'text':'memory'})],None)), upsert=AsyncMock(), set_payload=AsyncMock())
        with patch.object(r,'ollama_chat',AsyncMock(return_value='reasoning only')), patch.object(r,'get_embedding',AsyncMock()) as embedding:
            out=await r.reflect_on_memories(q)
        self.assertEqual(out['processed'],0); q.upsert.assert_not_called(); q.set_payload.assert_not_called(); embedding.assert_not_called()

    async def test_batch_fenced_json_is_structured(self):
        q=NS(scroll=AsyncMock(return_value=([NS(id=42,payload={'text':'memory'})],None)), upsert=AsyncMock(), set_payload=AsyncMock())
        data={k:['supported'] for k in ('patterns','connections','insights','actions')}
        with patch.object(r,'ollama_chat',AsyncMock(return_value='```json\n'+json.dumps(data)+'\n```')), patch.object(r,'get_embedding',AsyncMock(return_value=[0.1]*3)):
            out=await r.reflect_on_memories(q)
        self.assertEqual(out['reflection'],data); self.assertEqual(out['processed'],1)
        q.upsert.assert_awaited_once()

    async def test_selector_pages_and_prefers_unseen(self):
        def p(i,count,**kw): return NS(id=i,payload={'reflection_count':count,**kw})
        q=NS(scroll=AsyncMock(side_effect=[([p('old',2),p('archived',0,archived=True)],'next'),([p('new',0)],None)]))
        selected=await r._select_chunks(q,1)
        self.assertEqual([p.id for p in selected],['new']); self.assertEqual(q.scroll.call_count,2)

    async def _micro(self, verdict, payload=None):
        point=NS(id='first-id',payload={'text':'fact','confidence_score':0.8,**(payload or {})})
        q=NS(retrieve=AsyncMock(return_value=[NS(vector={'dense':[0.1]})]),set_payload=AsyncMock())
        response=Mock(); response.raise_for_status.return_value=None
        response.json.return_value={'result':[{'id':str(i),'payload':{'text':'neighbor'}} for i in range(2)]}
        client=NS(post=AsyncMock(return_value=response))
        cm=AsyncMock(); cm.__aenter__.return_value=client
        with patch.object(r,'_select_chunks',AsyncMock(return_value=[point])), patch.object(r,'get_budget_for_hour',return_value=0), patch.object(r,'increment_budget'), patch.object(r,'_analyze_chunk',AsyncMock(return_value=(verdict,1 if verdict else 0))), patch('httpx.AsyncClient',return_value=cm):
            result=await r.micro_reflection(q)
        return result,q

    async def test_unanalyzed_never_mutates_confidence(self):
        result,q=await self._micro(None)
        self.assertEqual(result['unanalyzed'],1); self.assertEqual(result['consistencies'],0); q.set_payload.assert_not_called()

    async def test_contradiction_stays_frozen_across_consistent_cycles(self):
        good={'contradiction_found':False,'severity':'low','explanation':'ok'}
        for old in ({'reflection_notes':'[CONTRADICTION HIGH] conflict'}, {'reflection_notes':'[CONSISTENT frozen] ok'}, {'contradiction_unresolved':True}):
            result,q=await self._micro(good,old)
            payload=q.set_payload.call_args.kwargs['payload']
            self.assertEqual(payload['confidence_score'],0.8); self.assertTrue(payload['contradiction_unresolved']); self.assertEqual(result['frozen'],1)

    async def test_high_contradiction_reduces_confidence(self):
        _,q=await self._micro({'contradiction_found':True,'severity':'high','explanation':'conflict'})
        self.assertEqual(q.set_payload.call_args.kwargs['payload']['confidence_score'],0.6)

    async def test_explicit_resolution_allows_consistent_reflection(self):
        from scripts.resolve_contradiction import resolution_payload
        previous = {'confidence_score': 0.8, 'reflection_count': 3,
                    'contradiction_unresolved': True,
                    'reflection_notes': '[CONSISTENT frozen] old conflict'}
        resolved = {**previous, **resolution_payload(previous, actor='reviewer', reason='source checked')}
        good = {'contradiction_found': False, 'severity': 'low', 'explanation': 'supported'}
        result, q = await self._micro(good, resolved)
        update = q.set_payload.call_args.kwargs['payload']
        self.assertFalse(update['contradiction_unresolved'])
        self.assertEqual(update['confidence_score'], 0.85)
        self.assertEqual(update['reflection_count'], 1)
        self.assertEqual(result['frozen'], 0)

class LlmTests(unittest.IsolatedAsyncioTestCase):
    def test_explicit_backend_and_missing_key(self):
        with patch.object(llm,'LLM_BACKEND','ollama'),patch.object(llm,'OPENROUTER_API_KEY','test'):
            self.assertFalse(llm._use_openrouter())
        with patch.object(llm,'LLM_BACKEND','openrouter'),patch.object(llm,'OPENROUTER_API_KEY',''):
            with self.assertRaises(ValueError):llm._use_openrouter()

    async def test_ollama_thinking_fallback(self):
        response=Mock(status_code=200);response.json.return_value={'response':'','thinking':'fenced JSON'}
        client=NS(post=AsyncMock(return_value=response));cm=AsyncMock();cm.__aenter__.return_value=client
        with patch.object(llm,'LLM_BACKEND','ollama'),patch.object(llm.httpx,'AsyncClient',return_value=cm):
            self.assertEqual(await llm.ollama_chat('prompt',json_mode=True),'fenced JSON')
        self.assertEqual(client.post.call_args.kwargs['json']['format'],'json')

    async def test_json_mode_400_retries_once(self):
        sent=[]
        async def post(url,**kw):
            sent.append(json.loads(json.dumps(kw['json'])))
            response=Mock(status_code=400 if len(sent)==1 else 200)
            response.json.return_value={'choices':[{'message':{'content':'{}'}}]}
            return response
        cm=AsyncMock();cm.__aenter__.return_value=NS(post=post)
        with patch.object(llm.httpx,'AsyncClient',return_value=cm):await llm._openrouter_chat('prompt',10,True)
        self.assertIn('response_format',sent[0]);self.assertNotIn('response_format',sent[1]);self.assertEqual(len(sent),2)

class ProfileTests(unittest.TestCase):
    def test_missing_profile_db_never_reads_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(state,'hermes_home',return_value=Path(tmp)/'missing'),patch.object(Path,'exists',lambda p: str(p).endswith('/.hermes/state.db')):
                self.assertIsNone(hooks._resolve_state_db())

    def test_custom_home_gets_own_fabric(self):
        with patch.dict(os.environ,{'HERMES_HOME':'/tmp/custom-profile','FABRIC_DIR':''}):
            self.assertEqual(hermes_env.fabric_dir(),Path('/tmp/custom-profile/fabric'))

class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.home=Path(self.temp.name)
        for attr,value in [('HERMES_HOME',self.home),('FABRIC_DIR',self.home/'fabric'),('AGENT_NAME','test')]:
            p=patch.object(state,attr,value);p.start();self.addCleanup(p.stop)
        p=patch.object(state,'hermes_home',return_value=self.home);p.start();self.addCleanup(p.stop)
        lifecycle._sessions.clear()

    def turn(self,sid,user='A substantive request about backups',turn='one'):
        lifecycle.on_session_start(sid)
        lifecycle.post_llm_call(sid,user,'Detailed result for '+sid+': '+'evidence '*30,turn_id=turn)
        lifecycle.on_session_end(sid)

    def test_interleaved_capture_and_restart_dedup(self):
        seen=[]
        def extract(text):
            seen.append(text);return [{'type':'note','content':text,'summary':text[-80:]}]
        with patch.object(hooks,'_llm_extract_entries',side_effect=extract):
            for sid in ('alice','bob'):
                lifecycle.post_llm_call(sid,'request for '+sid,('specific '+sid+' ')*30,turn_id='one')
            lifecycle.on_session_end('bob');lifecycle.on_session_end('alice');lifecycle.on_session_end('alice')
            lifecycle._sessions.clear()
            self.turn('alice')
        self.assertEqual(len(seen),2)
        for text in seen:self.assertNotEqual('alice' in text,'bob' in text)
        self.assertEqual(len(list((self.home/'fabric').glob('*.md'))),2)

    def test_opt_out_cron_and_interrupted_capture(self):
        with patch.object(hooks,'_llm_extract_entries') as extract:
            self.turn('alice','Não memorize esta conversa sobre infraestrutura')
            self.turn('cron_job')
            lifecycle.post_llm_call('bob','specific request','evidence '*30)
            lifecycle.on_session_end('bob',interrupted=True);lifecycle.on_session_end('bob')
            extract.assert_not_called()

    def test_daily_cap_and_normalized_content(self):
        with patch.dict(os.environ,{'ICARUS_MAX_DAILY_ENTRIES':'1'}):
            a=state.write_entry('note','specific first content','summary',automatic=True)
            b=state.write_entry('note',' SPECIFIC first   content ','other',automatic=True)
            self.assertEqual(a,b)
            with self.assertRaises(RuntimeError):state.write_entry('note','different content','second',automatic=True)
            self.assertTrue(state.write_entry('note','manual content','manual'))

    def test_recall_gate_is_per_session(self):
        item={'id':'item','summary':'backups on Tuesday','agent':'test'}
        with patch.object(state,'recall',return_value=[item]),patch.object(state,'log_recall'),patch.object(hooks,'_search_qdrant_with_status',return_value=([],None)),patch.object(hooks,'_search_sessions',return_value=[]):
            a=lifecycle.pre_llm_call('a','find Tuesday backups')
            b=lifecycle.pre_llm_call('b','find Tuesday backups')
            self.assertIsNotNone(a);self.assertIsNotNone(b)
            self.assertIsNone(lifecycle.pre_llm_call('a','find Tuesday backups'))

class IngestTests(unittest.IsolatedAsyncioTestCase):
    async def _run_ingest(self,result,mode='/wiki'):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);wiki=root/'wiki';wiki.mkdir();(wiki/'one.md').write_text('sample')
            job=NS(job_id='testjob',result=AsyncMock(return_value=result))
            redis=NS(enqueue_job=AsyncMock(return_value=job),aclose=AsyncMock())
            with patch.object(wi,'WIKI_ROOT',wiki),patch.object(wi,'STATE_FILE',root/'state.json'),patch.object(wi,'FAILURES_FILE',root/'failed.json'),patch.object(wi,'redis_ready',AsyncMock(return_value=True)),patch.object(wi,'create_pool',AsyncMock(return_value=redis)),patch.dict(os.environ,{'WORKER_WIKI_ROOT':mode}):
                await wi.main(); saved=json.loads((root/'state.json').read_text())
            return saved,redis

    async def test_worker_error_does_not_advance_checkpoint(self):
        saved,_=await self._run_ingest({'status':'error'})
        self.assertIsNone(saved['one.md']['ingested_at'])

    async def test_success_and_worker_path_mapping(self):
        for root in ('/wiki','/native/wiki'):
            saved,redis=await self._run_ingest({'status':'upserted'},root)
            self.assertIsNotNone(saved['one.md']['ingested_at'])
            self.assertEqual(redis.enqueue_job.call_args.kwargs['file_path'],root+'/one.md')

class RecallTests(unittest.TestCase):
    def test_dimension_mismatch_is_visible(self):
        response=Mock();response.json.return_value={'data':[{'embedding':[1,2]}]}
        with patch.object(ce,'EMBEDDING_API_BASE','http://localhost/v1'),patch.object(ce,'EMBEDDING_DIMS',3),patch.object(ce,'EMBEDDING_REQUEST_RETRIES',0),patch.object(ce.requests,'post',return_value=response):
            self.assertIsNotNone(ce.embed_query_with_status('test').error)


class QueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_idle_uses_sorted_set_and_detects_running_jobs(self):
        import reflection_trigger as trigger
        class Redis:
            def __init__(self, pending=0,running=False):self.pending=pending;self.running=running;self.aclose=AsyncMock()
            async def scan_iter(self,match):
                if match=='arq:queue*':yield 'arq:queue'
                elif self.running:yield 'arq:in-progress:one'
            async def type(self,key):return 'zset'
            async def zcard(self,key):return self.pending
        for pending,running,expected in ((0,False,True),(1,False,False),(0,True,False)):
            redis=Redis(pending,running)
            with patch.object(trigger.aioredis,'Redis',return_value=redis):self.assertEqual(await trigger.is_idle(),expected)
            redis.aclose.assert_awaited()

class HybridTests(unittest.TestCase):
    def test_rrf_scores_are_not_cosine_thresholded(self):
        response=Mock();response.json.return_value={'result':{'points':[{'id':'one','score':0.25,'payload':{'text':'memory','source':'wiki'}}]}}
        with patch.object(ce.requests,'post',return_value=response):
            hybrid=ce.search_knowledge_base([0.1],([1],[0.5]),score_threshold=0.55)
            dense=ce.search_knowledge_base([0.1],None,score_threshold=0.55)
        self.assertEqual(len(hybrid),1);self.assertEqual(len(dense),0)

class MissingEmbeddingTests(unittest.TestCase):
    def test_missing_embedding_never_queries_qdrant(self):
        with patch.object(ce.requests,'post') as post, patch.object(ce,'lexical_search_in_vault',return_value=[{'id':'lexical'}]),patch.object(ce,'sqlite_keyword_search',return_value=[]):
            results,level,_,_=ce.search_with_fallback(None,([1],[1.0]),'query')
            self.assertEqual(level,'lexical');self.assertEqual(results,[{'id':'lexical'}]);post.assert_not_called()

if __name__=='__main__':unittest.main()
