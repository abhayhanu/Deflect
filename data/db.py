import os
from urllib.parse import quote

from dotenv import load_dotenv

load_dotenv()


def database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if url:
        return url

    user = os.getenv("POSTGRES_USER") or "deflect"
    password = quote(os.getenv("POSTGRES_PASSWORD") or "deflect", safe="")
    host = os.getenv("POSTGRES_HOST") or "localhost"
    port = os.getenv("POSTGRES_PORT") or "5432"
    name = os.getenv("POSTGRES_DB") or "deflect"
    return f"postgresql://{user}:{password}@{host}:{port}/{name}"


def connect(**kwargs):
    import psycopg

    return psycopg.connect(database_url(), **kwargs)
