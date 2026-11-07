"""Fixed persona plus runtime instructions; learned notes stay in user context."""

import hashlib
import json
from pathlib import Path
from xml.sax.saxutils import escape


def compose(*,model:str,provider:str,tools:list[str],timezone:str='UTC'):
    root=Path(__file__).resolve().parent.parent/'prompts'
    if (root/'brodar.md').exists():
        persona=(root/'brodar.md').read_text()
        harness=(root/'brodar-harness.md').read_text()
    else:
        from importlib.resources import files
        root=files('brodar').joinpath('assets')
        persona=root.joinpath('persona.md').read_text()
        harness=root.joinpath('harness.md').read_text()
    fixed=persona+'\n\n'+harness
    revision=hashlib.sha256(fixed.encode()).hexdigest()
    runtime='\n\n# runtime\n\n'+f'provider: {provider}\nmodel: {model}\n'
    runtime+=f'configured local timezone: {timezone}\n'
    runtime+='\nuse the exact tool names supplied by the runtime. available brodar tools:\n'
    runtime+='\n'.join('- '+name for name in tools)
    return fixed+runtime,revision


def envelope(messages,*,context=None,notes=None,receipts=None):
    # Metadata is JSON inside an escaped XML attribute; untrusted text never
    # gets to close a message or masquerade as a trusted sender envelope.
    def xmlmessage(m):
        from xml.sax.saxutils import quoteattr
        meta={k:m.get(k) for k in ['id','platform_message_id','sender_id','sender_name','role','sent_at',
                                   'legacy_timestamp','reply_to_id','topic_id','source','chunk_start','chunk_end','content_length'] if m.get(k) is not None}
        return '<message metadata='+quoteattr(json.dumps(meta,default=str,ensure_ascii=False))+'>'+escape(m.get('content',''))+'</message>'
    blocks=[]
    if context:blocks.append('<historical_context>'+''.join(xmlmessage(m) for m in context)+'</historical_context>')
    if notes:blocks.append('<learned_context>'+escape(json.dumps(notes,default=str,ensure_ascii=False))+'</learned_context>')
    if receipts:blocks.append('<previous_delivery_receipts>'+escape(json.dumps(receipts,default=str))+'</previous_delivery_receipts>')
    blocks.append('<new_messages>'+''.join(xmlmessage(m) for m in messages)+'</new_messages>')
    return '\n'.join(blocks)


def authorized(payload,capability):
    from xml.sax.saxutils import quoteattr
    return '<turn turn_token='+quoteattr(capability.token)+'>'+payload+'</turn>'
