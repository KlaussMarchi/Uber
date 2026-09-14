import os, logging, sqlite3
import numpy as np
import pandas as pd
from contextlib import closing
from Utils.variables import DB_PATH


# o sqlite so aceita int nativo; float64 ja herda de float
sqlite3.register_adapter(np.int64, int)


# SQLITE LOCAL (WAL) COM UMA CONEXAO POR OPERACAO; BANCO DE OUTRA VERSAO E RECRIADO PARA O WORKER REGERAR HISTORICO E MODELO
class Database:
    VERSION = 5   # sobe quando o esquema ou o oraculo mudam; o historico e sintetico, entao banco antigo e recriado

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
            corridor    REAL    NOT NULL,
            tz          TEXT    NOT NULL,
            used_at     INTEGER NOT NULL,
            UNIQUE (o_lat, o_lon, d_lat, d_lon)
        );

        CREATE TABLE IF NOT EXISTS Prices (
            route_id INTEGER NOT NULL REFERENCES Routes (id),
            ts       INTEGER NOT NULL,
            rain     REAL    NOT NULL,
            price    REAL    NOT NULL,
            minutes  REAL    NOT NULL,
            PRIMARY KEY (route_id, ts)
        );

        CREATE TABLE IF NOT EXISTS Forecasts (
            route_id INTEGER NOT NULL REFERENCES Routes (id),
            made_at  INTEGER NOT NULL,
            ts       INTEGER NOT NULL,
            p10      REAL    NOT NULL,
            p50      REAL    NOT NULL,
            p90      REAL    NOT NULL,
            m10      REAL    NOT NULL,
            m50      REAL    NOT NULL,
            m90      REAL    NOT NULL,
            price    REAL,
            minutes  REAL,
            PRIMARY KEY (route_id, made_at, ts)
        );

        CREATE TABLE IF NOT EXISTS Metrics (
            ts             INTEGER PRIMARY KEY,
            version        INTEGER NOT NULL,
            n              INTEGER NOT NULL,
            price_mae      REAL    NOT NULL,
            price_coverage REAL    NOT NULL,
            time_mae       REAL    NOT NULL,
            time_coverage  REAL    NOT NULL
        );

        CREATE INDEX IF NOT EXISTS ix_prices_ts ON Prices (ts);
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
                logging.warning(f'banco na versao {version}, esperado {self.VERSION}: historico sintetico sera recriado')
                conn.executescript('DROP TABLE IF EXISTS Routes; DROP TABLE IF EXISTS Prices; DROP TABLE IF EXISTS Forecasts; DROP TABLE IF EXISTS Metrics;')

            conn.execute('PRAGMA journal_mode = WAL')
            conn.executescript(self.SCHEMA)
            conn.execute(f'PRAGMA user_version = {self.VERSION}')

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def get(self, sql, params=()):
        with closing(self.connect()) as conn:
            return pd.read_sql_query(sql, conn, params=params)

    def set(self, sql, rows):
        with closing(self.connect()) as conn, conn:
            conn.executemany(sql, rows)


database = Database()
