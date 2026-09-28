import os, logging, sqlite3
import numpy as np
import pandas as pd
from contextlib import closing
from Utils.variables import DB_PATH


# o sqlite so aceita int nativo; float64 ja herda de float
sqlite3.register_adapter(np.int64, int)


# SQLITE LOCAL (WAL) COM UMA CONEXAO POR OPERACAO; BANCO DE OUTRA VERSAO E MIGRADO SEM PERDER AS ROTAS E OS PRECOS QUE O USUARIO INFORMOU
class Database:
    VERSION = 7    # sobe quando o esquema muda; o que e derivado e recalculado, o que o usuario fez fica
    ROUTE   = ('id', 'origin', 'destination', 'o_lat', 'o_lon', 'd_lat', 'd_lon', 'distance', 'duration', 'tz', 'used_at')    # colunas que uma rota de versao antiga ja tinha

    SCHEMA = '''
        CREATE TABLE IF NOT EXISTS Routes (
            id          INTEGER PRIMARY KEY,
            origin      TEXT    NOT NULL,
            destination TEXT    NOT NULL,
            o_lat       REAL    NOT NULL,
            o_lon       REAL    NOT NULL,
            d_lat       REAL    NOT NULL,
            d_lon       REAL    NOT NULL,
            distance    REAL    NOT NULL,
            duration    REAL    NOT NULL,
            tz          TEXT    NOT NULL,
            used_at     INTEGER NOT NULL,
            city        TEXT    NOT NULL DEFAULT '',
            uf          TEXT    NOT NULL DEFAULT '',
            UNIQUE (o_lat, o_lon, d_lat, d_lon)
        );

        CREATE TABLE IF NOT EXISTS Forecasts (
            route_id INTEGER NOT NULL REFERENCES Routes (id),
            company  TEXT    NOT NULL,
            made_at  INTEGER NOT NULL,
            ts       INTEGER NOT NULL,
            p10      REAL    NOT NULL,
            p50      REAL    NOT NULL,
            p90      REAL    NOT NULL,
            m10      REAL    NOT NULL,
            m50      REAL    NOT NULL,
            m90      REAL    NOT NULL,
            PRIMARY KEY (route_id, company, made_at, ts)
        );

        CREATE TABLE IF NOT EXISTS Fares (
            id       INTEGER PRIMARY KEY,
            company  TEXT    NOT NULL,
            ts       INTEGER NOT NULL,
            route_id INTEGER NOT NULL,
            lat      REAL    NOT NULL,
            lon      REAL    NOT NULL,
            city     TEXT    NOT NULL,
            uf       TEXT    NOT NULL,
            level    REAL    NOT NULL,
            expected REAL    NOT NULL,
            observed REAL    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS Metrics (
            ts       INTEGER PRIMARY KEY,
            n        INTEGER NOT NULL,
            observed INTEGER NOT NULL,
            mae      REAL    NOT NULL,
            mape     REAL    NOT NULL,
            coverage REAL    NOT NULL
        );

        CREATE INDEX IF NOT EXISTS ix_forecasts_ts ON Forecasts (ts);
    '''

    def __init__(self):
        self.path = DB_PATH

    def setup(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

        with closing(self.connect()) as conn:
            version = conn.execute('PRAGMA user_version').fetchone()[0]
            tables  = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0]

            if version != self.VERSION and tables:
                logging.warning(f'banco na versao {version}, esperado {self.VERSION}: migrando')
                self.migrate(conn)

            conn.execute('PRAGMA journal_mode = WAL')
            conn.executescript(self.SCHEMA)
            conn.execute(f'PRAGMA user_version = {self.VERSION}')

    # PREVISOES, METRICAS E O HISTORICO SINTETICO ANTIGO SAO RECALCULAVEIS; AS ROTAS ESCOLHIDAS FICAM (SAO OS LUGARES JA USADOS) E PRECO INFORMADO NO FORMATO ANTIGO SAI, PORQUE ERA RELATIVO AO MERCADO SIMULADO
    def migrate(self, conn):
        columns = lambda table: {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}
        routes  = ', '.join(self.ROUTE)
        conn.executescript('DROP TABLE IF EXISTS Prices; DROP TABLE IF EXISTS Forecasts; DROP TABLE IF EXISTS Metrics; DROP TABLE IF EXISTS Legacy;')

        if not {'expected', 'level', 'city'} <= columns('Fares'):
            conn.execute('DROP TABLE IF EXISTS Fares')

        if not set(self.ROUTE) <= columns('Routes'):
            return conn.execute('DROP TABLE IF EXISTS Routes')

        conn.executescript(f'ALTER TABLE Routes RENAME TO Legacy; {self.SCHEMA} INSERT INTO Routes ({routes}) SELECT {routes} FROM Legacy WHERE used_at > 0; DROP TABLE Legacy;')

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def get(self, sql, params=()):
        with closing(self.connect()) as conn:
            return pd.read_sql_query(sql, conn, params=params)

    def set(self, sql, rows):
        with closing(self.connect()) as conn, conn:
            conn.executemany(sql, rows)


database = Database()
