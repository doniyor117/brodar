"""Editable prompt assets. Deployment edits take effect after a service restart."""
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from string import Template
import re

SOURCE=Path(__file__).resolve().parent.parent/'prompts'

@lru_cache(maxsize=128)
def load(name):
    if Path(name).name!=name or not name.endswith('.md'):raise ValueError('Invalid prompt asset name')
    if (SOURCE/name).is_file():return (SOURCE/name).read_text(encoding='utf-8').rstrip('\n')
    alias={'brodar.md':'persona.md','brodar-harness.md':'harness.md'}.get(name,name)
    return files('brodar').joinpath('assets',alias).read_text(encoding='utf-8').rstrip('\n')

def render(name,**values):return Template(load(name)).substitute(**values)

@lru_cache(maxsize=128)
def section(name,heading):
    blocks=re.split(r'^## ([a-zA-Z0-9_-]+)\s*$',load(name),flags=re.M)
    for i in range(1,len(blocks),2):
        if blocks[i]==heading:return blocks[i+1].strip()
    raise ValueError(f'Missing prompt section {heading} in {name}')
