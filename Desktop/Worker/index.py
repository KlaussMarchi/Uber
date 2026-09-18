import logging, threading
import numpy as np
from itertools import repeat
from time import time
from bootstrap import bootstrap
from Database.index import database
from Engine.index import engine
from Model.index import model
from Oracle.index import getPrice
from Utils.functions import getMoney
from Utils.variables import COMPANIES, STEP


# SERVICO EM SEGUNDO PLANO: PRIMEIRO BOOT, ORACULO A CADA 10 MIN, CONSOLIDACAO DAS PREVISOES E RETREINO
class Worker:
    RETRAIN = 3600           # s entre retreinos; cada um ja inclui os precos consolidados desde o anterior
    RETRY   = 60             # s ate tentar de novo um primeiro boot que falhou
    KEEP    = 7 * 86400      # previsoes guardadas para consolidacao
    TRACKED = 5              # rotas usadas mais recentes observadas pelo oraculo; as do bootstrap (used_at = 0) ja tem historico
    SAMPLE  = 50             # previsoes consolidadas antes de mostrar a taxa de acerto; com menos ela oscila dezenas de pontos a cada ciclo

    def __init__(self):
        self.thread  = None
        self.event   = threading.Event()
        self.wakeup  = threading.Event()
        self.status  = 'Iniciando serviços em segundo plano…'
        self.metrics = None

    def setup(self):
        if model.ready():
            return True

        database.setup()
        engine.setup()

        if database.get('SELECT COUNT(*) AS n FROM Prices')['n'][0] == 0:
            self.status = 'Primeiro boot: gerando um ano de histórico sintético sobre a chuva real da região…'

            if not bootstrap():
                self.status = 'Sem conexão para o primeiro boot, nova tentativa em 1 min'
                return False

            self.status = 'Treinando o modelo de quantis (p10, p50, p90)…'
            self.update()
        elif not model.setup():
            self.status = 'Treinando o modelo de quantis (p10, p50, p90)…'
            self.update()

        return model.ready()

    def handle(self):
        slot   = int(time()) // STEP * STEP
        routes = database.get('SELECT * FROM Routes WHERE used_at > 0 ORDER BY used_at DESC LIMIT ?', (self.TRACKED,)).to_dict('records')

        for route in routes:
            frames = {company: engine.get(route, company, now=slot) for company in COMPANIES}

            if any(df is None for df in frames.values()):
                continue

            state = next(iter(frames.values())).iloc[0]
            database.set('INSERT OR IGNORE INTO Prices (route_id, ts, rain, excess, noise, minutes) VALUES (?, ?, ?, ?, ?, ?)', [(route['id'], slot, float(state['rain']), float(state['excess']), float(state['noise']), float(state['minutes']))])

            for company, df in frames.items():
                future = df.iloc[1:]
                database.set('INSERT OR IGNORE INTO Forecasts (route_id, company, made_at, ts, p10, p50, p90, m10, m50, m90) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', zip(repeat(route['id']), repeat(company), repeat(slot), future['ts'].tolist(), future['p10'].tolist(), future['p50'].tolist(), future['p90'].tolist(), future['m10'].tolist(), future['m50'].tolist(), future['m90'].tolist()))

        self.check(slot)

        if time() - model.metrics.get('trained_at', 0) > self.RETRAIN:
            self.update()

        n      = self.metrics['n'] if self.metrics else 0
        online = f" · últimas 24 h: faixa acerta {self.metrics['p']['coverage']:.0%} no preço e {self.metrics['m']['coverage']:.0%} no tempo, erro de {getMoney(self.metrics['p']['mae'])} e {self.metrics['m']['mae']:.1f} min".replace('.', ',') if n >= self.SAMPLE else f' · consolidando previsões ({n} de {self.SAMPLE})'
        self.status = f"Modelo v{model.version} · {len(routes)} {'rota monitorada' if len(routes) == 1 else 'rotas monitoradas'}{online}"
        logging.info(self.status)

    # PREVISOES CUJO HORARIO JA PASSOU CONTRA O ESTADO OBSERVADO NAQUELE SLOT; O PRECO DE CADA UMA SAI DA TARIFA DO SEU APLICATIVO
    def check(self, now):
        database.set('DELETE FROM Forecasts WHERE ts < ?', [(now - self.KEEP,)])
        df = database.get('SELECT f.company, f.p10, f.p50, f.p90, f.m10, f.m50, f.m90, p.excess, p.noise, p.minutes, r.distance FROM Forecasts f JOIN Prices p ON p.route_id = f.route_id AND p.ts = f.ts JOIN Routes r ON r.id = f.route_id WHERE f.ts > ?', (now - 86400,))

        if df.empty:
            return

        for company, part in df.groupby('company'):
            df.loc[part.index, 'price'] = getPrice(part['distance'], part['minutes'], part['excess'], part['noise'], company)

        alpha = np.array(list(model.QUANTILES.values()))
        self.metrics = {'n': len(df)}

        for column, prefix in model.TARGETS.items():
            error = df[column].to_numpy()[:, None] - df[[f'{prefix}{key}' for key in model.QUANTILES]].to_numpy()
            self.metrics[prefix] = {
                'mae':      float(np.abs(error[:, 1]).mean()),
                'coverage': float(((df[column] >= df[f'{prefix}10']) & (df[column] <= df[f'{prefix}90'])).mean()),
                'loss':     float(np.maximum(alpha * error, (alpha - 1) * error).mean()),
            }

        database.set('INSERT OR REPLACE INTO Metrics (ts, version, n, price_mae, price_coverage, time_mae, time_coverage) VALUES (?, ?, ?, ?, ?, ?, ?)', [(now, model.version, len(df), self.metrics['p']['mae'], self.metrics['p']['coverage'], self.metrics['m']['mae'], self.metrics['m']['coverage'])])

    def update(self):
        df = database.get('SELECT p.route_id, p.ts, p.rain, p.excess, p.noise, p.minutes, r.distance, r.duration, r.corridor, r.tz FROM Prices p JOIN Routes r ON r.id = p.route_id')

        if model.update(df):
            model.export()

    # ROTA NOVA NA TELA: O CICLO RODA JA, SEM ESPERAR O PROXIMO SLOT, PARA ELA ENTRAR NAS ROTAS MONITORADAS
    def wake(self):
        self.wakeup.set()

    def start(self):
        if self.thread is not None and self.thread.is_alive():
            return

        self.event.clear()
        self.wakeup.clear()
        self.thread = threading.Thread(target=self.handleThread, name='worker', daemon=True)
        self.thread.start()

    # LACO DO SERVICO, UM CICLO POR SLOT DE 10 MIN; UM ERRO INESPERADO VAI PARA O LOG E O SLOT SEGUINTE TENTA DE NOVO
    def handleThread(self):
        while not self.event.is_set():
            try:
                if self.setup():
                    self.handle()
            except Exception:
                logging.exception('ciclo do worker falhou')
                self.status = 'Erro no ciclo em segundo plano (detalhes em data/app.log)'

            self.wakeup.wait(STEP - time() % STEP + 5 if model.ready() else self.RETRY)
            self.wakeup.clear()

    def stop(self):
        self.event.set()
        self.wakeup.set()

        if self.thread is not None:
            self.thread.join(timeout=30)


worker = Worker()
