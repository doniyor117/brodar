"""Fixed persona plus runtime instructions; learned notes stay in user context."""

import hashlib
import json
from .prompt_files import load
from xml.sax.saxutils import escape


def compose(*,model:str,provider:str,tools:list[str],timezone:str='UTC'):
    from .prompt_files import load,render
    fixed=load('brodar.md')+'\n\n'+load('brodar-harness.md')
    # Changes to fixed model/tool guidance invalidate old provider session references.
    revision=hashlib.sha256('\0'.join(load(name) for name in (
        'brodar.md','brodar-harness.md','runtime.md','codex-developer.md',
        'tool-descriptions.md','tool-authorization.md','control-action.md','control-reason.md',
        'claude-runtime.md','claude-tools.md','claude-external-tools.md','shared-group-turn.md','research-delegation.md')).encode()).hexdigest()
    runtime=render('runtime.md',model=model,provider=provider,timezone=timezone,tools='\n'.join('- '+name for name in tools))
    return fixed+'\n\n'+runtime,revision


def envelope(messages,*,context=None,notes=None,receipts=None):
    # Metadata is JSON inside an escaped XML attribute; untrusted text never
    # gets to close a message or masquerade as a trusted sender envelope.
    def xmlmessage(m):
        from xml.sax.saxutils import quoteattr
        meta={k:m.get(k) for k in ['id','platform_message_id','sender_id','sender_name','role','sent_at',
                                   'legacy_timestamp','reply_to_id','topic_id','source','chunk_start','chunk_end','content_length'] if m.get(k) is not None}
        if m.get('metadata'):meta.update({k:v for k,v in m['metadata'].items() if k in {'sender_is_bot','sender_username'}})
        return '<message metadata='+quoteattr(json.dumps(meta,default=str,ensure_ascii=False))+'>'+escape(m.get('content',''))+'</message>'
    blocks=[]
    if context:
        remaining=12000;selected=[]
        for item in reversed(context):
            if remaining<=0:break
            text=item.get('content','')
            part=text[:remaining]
            selected.append(dict(item,content=part+(load('context-truncated.md') if len(part)<len(text) else '')))
            remaining-=len(part)
        context=list(reversed(selected))
    if context:blocks.append('<historical_context>'+''.join(xmlmessage(m) for m in context)+'</historical_context>')
    if notes:blocks.append('<learned_context>'+escape(json.dumps(notes,default=str,ensure_ascii=False))+'</learned_context>')
    if receipts:blocks.append('<previous_delivery_receipts>'+escape(json.dumps(receipts,default=str))+'</previous_delivery_receipts>')
    blocks.append('<new_messages>'+''.join(xmlmessage(m) for m in messages)+'</new_messages>')
    return '\n'.join(blocks)


def authorized(payload,capability):
    from xml.sax.saxutils import quoteattr
    return '<turn turn_token='+quoteattr(capability.token)+'>'+payload+'</turn>'
