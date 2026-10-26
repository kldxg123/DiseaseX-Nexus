import psycopg

DSN = "postgresql://REDACTED@/epidemic_openclaw?host=USER_HOME/projects/openclaw/runtime/postgres-run&port=55432"


def connect():
    return psycopg.connect(DSN, autocommit=True)
