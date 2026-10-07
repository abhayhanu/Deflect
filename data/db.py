import os
from urllib.parse import quote

from dotenv import load_dotenv

load_dotenv()

APP_ROLE = "deflect_app"


def database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if url:
        return url

    user = os.getenv("POSTGRES_USER") or "deflect"
    password = quote(os.getenv("POSTGRES_PASSWORD") or "deflect", safe="")
    host = os.getenv("POSTGRES_HOST") or "localhost"
    port = os.getenv("POSTGRES_PORT") or "5434"
    name = os.getenv("POSTGRES_DB") or "deflect"
    return f"postgresql://{user}:{password}@{host}:{port}/{name}"


def app_password() -> str:
    return os.getenv("DEFLECT_APP_DB_PASSWORD") or "deflect_app"


def app_database_url() -> str:
    """The same database as the owner, reached as the agent's own role.

    The owner creates tables and reseeds. The agent connects as deflect_app, which may only add
    rows to the audit log and read them, never change or remove them.
    """
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    url = os.getenv("DEFLECT_APP_DATABASE_URL")
    if url:
        return url
    params = conninfo_to_dict(database_url())
    params.update(user=APP_ROLE, password=app_password())
    return make_conninfo(**params)


def connect(**kwargs):
    import psycopg

    return psycopg.connect(database_url(), **kwargs)
