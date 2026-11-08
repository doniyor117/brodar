"""An attaching operator TUI; no polling consumer or database work on repaint."""
from __future__ import annotations
import asyncio
import json
import sys
from .control import request
from .redaction import scrub

VIEWS=['Overview','Chat','Connections','Groups','Memory','Jobs','Skills','Persona/Settings','Logs']
METHODS=['status','chat_read','connections','groups','memory','jobs','skills','settings','logs']

async def tui(cfg):
    if not sys.stdin.isatty() or not sys.stdout.isatty():raise ValueError('TUI needs a terminal; use doctor --json, configure --plain or service status')
    from prompt_toolkit import Application
    from prompt_toolkit.layout import Layout,HSplit,Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.widgets import TextArea,Frame
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.styles import Style
    selected=0;app=None;pending=set();exit_action=None;cached={}
    body=TextArea(text='Connecting to the existing worker…',read_only=True,scrollbar=True,wrap_lines=True)
    def title():return [('class:bar','  '.join(('['+name+']') if i==selected else name for i,name in enumerate(VIEWS)))]
    async def refresh():
        try:
            value=await request(cfg,METHODS[selected]);cached[selected]=value
            body.text=scrub(json.dumps(value,indent=2,ensure_ascii=False,default=str))
        except Exception as exc:
            body.text='Worker unavailable ('+type(exc).__name__+'). Use brodar service start or run.\nCached state:\n'+json.dumps(cached.get(selected,{}),indent=2,default=str)
        if app:app.invalidate()
    def spawn(coro):
        task=asyncio.create_task(coro);pending.add(task);task.add_done_callback(pending.discard)
    def accept(buffer):
        text=buffer.text.strip();buffer.text=''
        async def act():
            nonlocal exit_action
            try:
                if text.startswith('/configure'):
                    exit_action=('configure',text.partition(' ')[2] or None);app.exit();return
                if text=='/restart':
                    exit_action=('restart',None);app.exit();return
                if text=='/doctor':
                    from .diagnostics import diagnose
                    body.text=json.dumps(await diagnose(cfg),indent=2,default=str);return
                if selected==1 and text:
                    await request(cfg,'chat_send',{'text':text});body.text='queued in private terminal session';return
                if text in {'/quit','/q'}:app.exit();return
                await refresh()
            except Exception as exc:body.text='Action failed: '+type(exc).__name__
        spawn(act())
    command=TextArea(height=1,prompt='> ',multiline=False,accept_handler=accept)
    keys=KeyBindings()
    @keys.add('c-q')
    @keys.add('c-c')
    def quit_(event):event.app.exit()
    @keys.add('tab')
    def next_(event):
        nonlocal selected
        selected=(selected+1)%len(VIEWS);spawn(refresh())
    @keys.add('s-tab')
    def previous(event):
        nonlocal selected
        selected=(selected-1)%len(VIEWS);spawn(refresh())
    @keys.add('f5')
    def reload_(event):spawn(refresh())
    layout=HSplit([Window(FormattedTextControl(title),height=2),Frame(body,title='Brodar operator'),Window(FormattedTextControl('tab: view · f5: refresh · /configure [section] · /doctor · ctrl-q: detach (bot keeps running)'),height=2),command])
    app=Application(layout=Layout(layout,focused_element=command),key_bindings=keys,full_screen=True,style=Style.from_dict({'bar':'bg:#17324d #ffffff'}))
    async def updates():
        while True:
            await refresh();await asyncio.sleep(5 if selected in (0,1) else 60)
    updater=asyncio.create_task(updates())
    try:await app.run_async()
    finally:
        updater.cancel()
        for task in pending:task.cancel()
        await asyncio.gather(updater,*pending,return_exceptions=True)
    if exit_action:
        if exit_action[0]=='restart':
            from .service import service
            from .settings import Settings
            print(json.dumps(await asyncio.to_thread(service,Settings.load(profile=cfg.profile_path),'restart'),indent=2))
        else:
            from .onboarding import wizard
            await wizard(cfg.profile_path,section=exit_action[1]);print('Settings saved. Apply with brodar service restart or /restart from the TUI.')
