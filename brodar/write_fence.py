"""Carry a captured tool capability through queued database worker threads."""

from contextvars import ContextVar
from functools import wraps

WRITE_FENCE=ContextVar('brodar_write_fence',default=None)


def guarded_write(method):
    @wraps(method)
    async def guarded(self,*args,**kwargs):
        capability=self.require()
        def check():
            if self.capability is not capability:raise PermissionError('The writing turn has ended or been cancelled')
        token=WRITE_FENCE.set(check)
        try:return await method(self,*args,**kwargs)
        finally:WRITE_FENCE.reset(token)
    return guarded
