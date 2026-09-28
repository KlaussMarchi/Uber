import logging, threading
import numpy as np
from datetime import date
from itertools import repeat
from time import time
from Database.index import database
from Engine.index import engine
import Oracle.index as oracle
from Utils.functions import getMoney
from Utils.variables import COMPANIES, STEP


# SERVICO EM SEGUNDO PLANO: A CADA 10 MIN GUARDA A PREVISAO DAS ROTAS MONITORADAS E MEDE O ACERTO DELA CONTRA OS PRECOS REAIS INFORMADOS DEPOIS
class Worker:
    KEEP    = 7 * 86400      # previsoes guardadas para comparar com precos informados
    TRACKED = 5              # rotas usadas mais recentes que sao monitoradas
    SAMPLE  = 3              # precos informados com previsao anterior antes de mostrar a taxa de acerto; com menos ela diz pouco

    def __init__(self):
        self.thread  = None
        self.event   = threading.Event()
        self.wakeup  = threading.Event()
        self.status  = 'Iniciando serviços em segundo plano…'
        self.metrics = None

    def setup(self):
        database.setup()
        engine.setup()

    def handle(self):
        slot   = int(time()) // STEP * STEP
        routes = database.get('SELECT * FROM Routes WHERE used_at > 0 ORDER BY used_at DESC LIMIT ?', (self.TRACKED,)).to_dict('records')

        for route in routes:
            for company in COMPANIES:
                df = engine.get(route, company, now=slot)

                if df is None:
                    continue

                future = df.iloc[1:]
                database.set('INSERT OR IGNORE INTO Forecasts (route_id, company, made_at, ts, p10, p50, p90, m10, m50, m90) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', zip(repeat(route['id']), repeat(company), repeat(slot), future['ts'].tolist(), future['p10'].tolist(), future['p50'].tolist(), future['p90'].tolist(), future['m10'].tolist(), future['m50'].tolist(), future['m90'].tolist()))

        self.check(slot)
        found   = self.metrics
        updated = date.fromisoformat(oracle.MARKET['updated']).strftime('%d/%m/%Y')
        places  = f"{len(routes)} {'rota monitorada' if len(routes) == 1 else 'rotas monitoradas'}"
        error   = f"{found.get('mape', 0) * 100:.1f}".replace('.', ',')
        prices  = f"{found['observed']} {'preço real informado' if found['observed'] == 1 else 'preços reais informados'}"
        online  = (f" · {prices}: as previsões feitas antes erraram {error}% ({getMoney(found.get('mae', 0))}) e a faixa acertou {found.get('coverage', 0):.0%}" if found['matched'] >= self.SAMPLE else
                   f" · {prices}, comparando com as previsões anteriores ({found['matched']} de {self.SAMPLE})" if found['observed'] else ' · informe o preço que o app mostra para medir o acerto')
        self.status = f'Médias reais da Uber de {updated} · {places}{online}'
        logging.info(self.status)

    # PRECOS INFORMADOS CONTRA AS PREVISOES FEITAS ANTES DELES PARA O MESMO SLOT, ROTA E APLICATIVO
    def check(self, now):
        database.set('DELETE FROM Forecasts WHERE ts < ?', [(now - self.KEEP,)])
        df = database.get('SELECT fa.id, fa.observed, f.p10, f.p50, f.p90 FROM Fares fa JOIN Forecasts f ON f.route_id = fa.route_id AND f.company = fa.company AND f.ts = (fa.ts + ?) / ? * ? AND f.made_at < fa.ts', (STEP // 2, STEP, STEP))
        observed = int(database.get('SELECT COUNT(*) AS n FROM Fares')['n'][0])
        self.metrics = {'observed': observed, 'matched': int(df['id'].nunique()), 'n': len(df)}

        if df.empty:
            return

        error = df['p50'] - df['observed']
        self.metrics.update({'mae': float(np.abs(error).mean()), 'mape': float(np.abs(error / df['observed']).mean()), 'coverage': float(df['observed'].between(df['p10'], df['p90']).mean())})
        database.set('INSERT OR REPLACE INTO Metrics (ts, n, observed, mae, mape, coverage) VALUES (?, ?, ?, ?, ?, ?)', [(now, len(df), observed, self.metrics['mae'], self.metrics['mape'], self.metrics['coverage'])])

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
                self.handle()
            except Exception:
                logging.exception('ciclo do worker falhou')
                self.status = 'Erro no ciclo em segundo plano (detalhes em data/app.log)'

            self.wakeup.wait(STEP - time() % STEP + 5)
            self.wakeup.clear()

    def stop(self):
        self.event.set()
        self.wakeup.set()

        if self.thread is not None:
            self.thread.join(timeout=30)


worker = Worker()
