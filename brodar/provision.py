"""Operator-only least-privilege database role provisioning."""

from urllib.parse import quote,urlsplit,urlunsplit
import secrets

from .postgres import Postgres

TABLES=('messages','inbox','memory_documents','memory_revisions','memory_sources',
        'embeddings','sessions','deliveries','jobs','poll_state','reflection_state','maintenance_runs','approvals','group_access','bot_exchanges','bot_aliases','bot_reservations','research_tasks','forgotten_sources','tool_calls','artifacts','audience_registry','storage_usage')


async def provision_runtime(db:Postgres,admin_uri:str,*,role:str,password:str|None=None):
    if not role.replace('_','').isalnum() or len(role)>50:
        raise ValueError('Invalid role')
    supplied_password=password is not None
    password=password or secrets.token_urlsafe(40)
    if any(x in password for x in ["'",'\\','\n']):
        raise ValueError('Invalid generated password')
    def provision():
        with db.transaction() as c:
            c.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(role,))
            if c.fetchone() is None:
                c.execute(f'CREATE ROLE "{role}" LOGIN PASSWORD \'{password}\' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT')
            else:
                if not supplied_password:
                    raise RuntimeError('Reload the existing private runtime credential before reprovisioning')
                # Reusing a role requires the same private password record;
                # never rotate it silently and break an existing process.
                c.execute('SELECT rolsuper,rolbypassrls,rolcreaterole,rolcreatedb FROM pg_roles WHERE rolname=%s',(role,))
                if any(c.fetchone()):
                    raise RuntimeError('Existing runtime role has excessive privileges')
            c.execute(f'GRANT USAGE ON SCHEMA brodar_v2 TO "{role}"')
            for table in TABLES:
                c.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON brodar_v2.{table} TO "{role}"')
    await db.run(provision)
    return runtime_uri(admin_uri,role,password)


def runtime_uri(admin_uri,role,password):
    u=urlsplit(admin_uri)
    return urlunsplit((u.scheme,quote(role,safe='')+':'+quote(password,safe='')+'@'+u.netloc.split('@',1)[1],u.path,u.query,u.fragment))
