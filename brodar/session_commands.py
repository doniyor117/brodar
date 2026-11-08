"""Host session controls: no inference for status, serialized provider compaction."""
from __future__ import annotations
import asyncio
import time


def telegram_command(message,username):
    if message.get('edit_date') or message.get('sender_chat') or any(message.get(k) for k in ('forward_origin','forward_from','forward_sender_name')):
        return None
    if (message.get('from') or {}).get('is_bot'):return None
    text=(message.get('text') or '').strip().lower()
    for name in ('status','compact'):
        if text=='/'+name or (username and text=='/'+name+'@'+username.lower()):return name
    return None


def status_snapshot(brain,*,target,provider,model,threshold=None):
    usage=getattr(brain,'token_usage',None) or {}
    last=usage.get('last') or {}
    used=last.get('totalTokens')
    window=usage.get('modelContextWindow')
    return {'target':target,'provider':provider,'model':model,'session_started':brain is not None,
        'context_tokens_estimate':used,'context_window':window,
        'remaining_tokens_estimate':max(0,window-used) if isinstance(window,int) and isinstance(used,int) else None,
        'used_percent_estimate':round(100*used/window,1) if isinstance(window,int) and window>0 and isinstance(used,int) else None,
        'usage_as_of':getattr(brain,'usage_updated_at',None),
        'auto_compact_configured_tokens':threshold if provider=='codex' else None,
        'compactions_since_start':getattr(brain,'compactions',0),
        'compaction_supported':callable(getattr(brain,'compact',None)),
        'busy':bool(getattr(brain,'active_turn',None) or getattr(brain,'_starting',False) or getattr(brain,'_compact_waiter',None)),
        'note':'Last provider-reported request usage estimates context, not subscription quota. Unknown until a usage event arrives.'}


def format_status(value):
    lines=[f"{value['target']} · {value['provider']} / {value['model']}"]
    used,window=value['context_tokens_estimate'],value['context_window']
    if used is None:lines.append('context: unknown until the provider reports usage')
    elif window:
        lines.append(f"context ≈ {used:,} / {window:,} tokens ({value['used_percent_estimate']}%); ≈ {value['remaining_tokens_estimate']:,} remaining")
    else:lines.append(f'context ≈ {used:,} tokens; provider has not reported the window')
    if value.get('usage_as_of'):lines.append(f"last usage update: {max(0,int(time.time()-value['usage_as_of']))}s ago")
    if value['auto_compact_configured_tokens']:
        lines.append(f"configured auto-compact: {value['auto_compact_configured_tokens']:,} tokens")
    lines.append('compaction: '+value.get('compaction_status','idle'))
    if value.get('compaction_error'):lines.append('last compaction failed; retry or check service logs')
    lines.append('context estimate, not account quota')
    return '\n'.join(lines)


class SessionCommands:
    def __init__(self,resolve,cfg):
        self.resolve,self.cfg=resolve,cfg
        self.tasks={};self.results={}

    def status(self,target):
        conversation,_=self.resolve(target)
        result=status_snapshot(conversation.brain if conversation else None,target=target,
            provider=self.cfg.brain,model=self.cfg.model,threshold=self.cfg.auto_compact_token_limit)
        result.update(self.results.get(target,{}))
        if target in self.tasks:result['compaction_status']='queued / working'
        return result

    def queue(self,target,notify=None):
        if target in self.tasks:return {'queued':False,'message':'compaction already queued / working'}
        conversation,lock=self.resolve(target)
        if conversation is None:return {'queued':False,'message':'no active session to compact yet'}
        if not callable(getattr(conversation.brain,'compact',None)):
            return {'queued':False,'message':'manual compaction is currently supported for Codex only'}
        async def work():
            try:
                async with lock:await conversation.brain.compact()
                self.results[target]={'compaction_status':'completed','compaction_error':False}
                text='compaction finished.'
            except asyncio.CancelledError:raise
            except Exception:
                self.results[target]={'compaction_status':'failed','compaction_error':True}
                text='compaction failed; the provider session was recovered where possible. check /status or service logs'
            finally:self.tasks.pop(target,None)
            if not self.results[target]['compaction_error']:text+=' '+format_status(self.status(target))
            if notify:
                try:await notify(text)
                except Exception:pass  # uncertain Telegram delivery must not be retried
        self.results[target]={'compaction_status':'queued','compaction_error':False}
        self.tasks[target]=asyncio.create_task(work(),name='brodar-session-compact')
        return {'queued':True,'message':'compaction queued; waits for the current turn. /status shows completion. this uses provider quota'}

    async def close(self):
        tasks=list(self.tasks.values())
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
