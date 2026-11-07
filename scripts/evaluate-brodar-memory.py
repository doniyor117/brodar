#!/usr/bin/env python3
"""Explicit live, synthetic memory probe; no Telegram connection or private chats.

Prints outputs and human grading rubrics. Does not claim automatic semantic scores.
Requires BRODAR_TEST_DATABASE_URL for a disposable, migrated restricted-role DB.
Uses the chosen provider's existing dedicated auth home. Each case gets a fresh
provider thread and audience. --mode search leaves notes empty; index adds a small
learned index. Compare records and timings between both modes before rollout.
"""
import argparse
import asyncio
import dataclasses
import json
import os
import sys
import time
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from brodar.learning import LearningStore
from brodar.postgres import Postgres
from brodar.runtime import Conversation
from brodar.scope import Scope
from brodar.settings import Settings
from brodar.broker import TurnCapability
from brodar.prompts import authorized,envelope

async def main(args):
    uri=os.environ.get('BRODAR_TEST_DATABASE_URL')
    if not uri:raise SystemExit('Set BRODAR_TEST_DATABASE_URL to a disposable migrated DB')
    cfg=dataclasses.replace(Settings.load(),database_url=uri)
    db=Postgres(uri)
    try:
        for case in json.loads((Path(__file__).resolve().parents[1]/'tests/brodar/memory_cases.json').read_text()):
            scope=Scope(int(uuid4().hex[:12],16),-11,instance_id='eval-'+uuid4().hex)
            store=LearningStore(db,scope)
            worker=Conversation(cfg,store,SimpleNamespace(),None,asyncio.Semaphore(1))
            try:
                for i,item in enumerate(case['history'],1):
                    await store.ingest(update_id=i,message_id=i,sender_id=item['sender'],sender_name='synthetic',text=item['text'],timestamp=datetime.now(timezone.utc),payload={})
                if args.mode=='index':
                    ids=[str(r['id']) for r in await store.recent()]
                    await store.memory_write('context/overview.md','Conversation evidence is available through history tools; check corrections and whose feedback applies.',expected_revision=0,reason='synthetic evaluation',source_ids=ids)
                await worker.start(maintenance=True)
                cap=TurnCapability(str(uuid4()),mode='reflection');worker.broker.begin(cap)
                payload='Evaluation only. Answer this synthetic user question using available history and memory. Return the actual answer as plain text; the harness will record internal text. Question: '+case['question']+'\n'+envelope([],notes=await store.context_notes([]))
                start=time.monotonic();result=await asyncio.wait_for(worker.brain.turn(authorized(payload,cap)),120)
                print(json.dumps({'case':case['id'],'mode':args.mode,'seconds':round(time.monotonic()-start,3),'answer':result.internal_text or result.reason,'status':result.status,'rubric':case['rubric']},ensure_ascii=False))
            finally:
                await worker.close()
                def cleanup():
                    with db.transaction(scope.audience) as c:
                        for table in ['tool_calls','memory_sources','memory_revisions','memory_documents','inbox','messages','sessions']:
                            c.execute(f'DELETE FROM brodar_v2.{table} WHERE audience=%s',(scope.audience,))
                        c.execute('DELETE FROM brodar_v2.poll_state WHERE bot_id=%s',(scope.bot_id,))
                        c.execute('DELETE FROM brodar_v2.audience_registry WHERE audience=%s',(scope.audience,))
                await db.run(cleanup)
    finally:await db.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=['search','index'],required=True)
    asyncio.run(main(parser.parse_args()))
