import logging
import numpy as np
import pandas as pd
from datetime import date, timedelta
from itertools import repeat
from time import time
from Api.index import api
from Database.index import database
from Engine.index import engine
from Oracle.index import getMarket
from Utils.variables import ORIGIN, DESTINATION, STEP


DAYS        = 365
REGION      = (-22.43, -41.85)    # ponto medio Macae - Rio das Ostras; a grade do ERA5 tem ~25 km
STORMS      = 6                   # tempestades sinteticas por semana; a chuva real quase nunca traz temporal no rush
STORM_MM    = (3, 15)             # mm/h no auge de cada tempestade
STORM_SLOTS = (6, 19)             # duracao de 1 a 3 h em slots de 10 min
SEED        = 3

# rotas reais da regiao, de 4 a 186 km e de 0 a 97% do percurso na Amaral Peixoto; longas sem corredor e curtas dentro dele impedem o modelo de confundir distancia com corredor
ROUTES = [
    (ORIGIN, DESTINATION),
    ({'label': 'Praia dos Cavaleiros, Macaé', 'lat': -22.4071231, 'lon': -41.8005748}, {'label': 'Terminal Rodoviário Álvaro Brum de Azevedo, Macaé', 'lat': -22.3785776, 'lon': -41.779899}),
    ({'label': 'Praia de Costazul, Rio das Ostras', 'lat': -22.5071036, 'lon': -41.9097663}, {'label': 'Residencial Praia Âncora, Rio das Ostras', 'lat': -22.4814971, 'lon': -41.9130247}),
    ({'label': 'Aeroporto de Macaé', 'lat': -22.3417817, 'lon': -41.7655371}, {'label': 'Barra de São João, Casimiro de Abreu', 'lat': -22.5881, 'lon': -41.9924}),
    ({'label': 'Praia do Pecado, Macaé', 'lat': -22.411424, 'lon': -41.808443}, {'label': 'Praia de Costazul, Rio das Ostras', 'lat': -22.5071036, 'lon': -41.9097663}),
    ({'label': 'Unamar, Cabo Frio', 'lat': -22.6210, 'lon': -42.0036}, {'label': 'Centro, Rio das Ostras', 'lat': -22.5269, 'lon': -41.9450}),
    ({'label': 'Praça José Pereira Câmara, Rio das Ostras', 'lat': -22.5273, 'lon': -41.9445}, {'label': 'Praia da Família, Rio das Ostras', 'lat': -22.4550, 'lon': -41.8556}),
    ({'label': 'Cabiúnas, Macaé', 'lat': -22.3122, 'lon': -41.7315}, {'label': 'Rua Doutor Bueno, Centro, Macaé', 'lat': -22.3797, 'lon': -41.7734}),
    ({'label': 'Praça José Pereira Câmara, Rio das Ostras', 'lat': -22.5273, 'lon': -41.9445}, {'label': 'Casimiro de Abreu', 'lat': -22.4850, 'lon': -42.2026}),
    ({'label': 'Glicério, Macaé', 'lat': -22.2382, 'lon': -42.0554}, {'label': 'Rua Doutor Bueno, Centro, Macaé', 'lat': -22.3797, 'lon': -41.7734}),
    ({'label': 'Centro, Rio das Ostras', 'lat': -22.5269, 'lon': -41.9450}, {'label': 'São Pedro da Aldeia', 'lat': -22.8385, 'lon': -42.1032}),
    ({'label': 'Rua Doutor Bueno, Centro, Macaé', 'lat': -22.3797, 'lon': -41.7734}, {'label': 'Conceição de Macabu', 'lat': -22.0832, 'lon': -41.8736}),
    ({'label': 'Rua Doutor Bueno, Centro, Macaé', 'lat': -22.3797, 'lon': -41.7734}, {'label': 'Campos dos Goytacazes', 'lat': -21.7739, 'lon': -41.3139}),
    ({'label': 'Rodoviária de Macaé', 'lat': -22.3785, 'lon': -41.7797}, {'label': 'Rodoviária Novo Rio, Rio de Janeiro', 'lat': -22.8976, 'lon': -43.2060}),
]


# PRIMEIRO BOOT: UM ANO DE CHUVA REAL DA REGIAO COM TEMPESTADES INJETADAS E, SOBRE ELA, O PRECO DO ORACULO A CADA 10 MIN EM CADA ROTA
def bootstrap():
    routes = [engine.getRoute(src, dst) for src, dst in ROUTES]
    today  = date.today()
    past   = api.getWeather(*REGION, start_date=str(today - timedelta(days=DAYS + 1)), end_date=str(today))
    recent = api.getWeather(*REGION, past_days=7, forecast_days=1)

    if None in routes or past is None or recent is None:
        return False

    # rotas do bootstrap nao sao lugares que o usuario confirmou: saem dos lugares ja usados, e o worker so as observa enquanto faltarem rotas do usuario
    database.set('UPDATE Routes SET used_at = 0 WHERE id = ?', [(route['id'],) for route in routes])

    weather = pd.concat([past, recent]).drop_duplicates('ts', keep='last').sort_values('ts')
    now     = int(time()) // STEP * STEP
    ts      = np.arange(now - DAYS * 86400, now + 1, STEP)
    storm   = np.zeros(len(ts))
    rng     = np.random.default_rng(SEED)
    n       = rng.poisson(DAYS / 7 * STORMS)

    for start, size, peak in zip(rng.integers(0, len(ts), n), rng.integers(*STORM_SLOTS, n), rng.uniform(*STORM_MM, n)):
        span = storm[start:start + size]
        span[:] = np.maximum(span, peak * np.sin(np.linspace(0, np.pi, size + 2)[1:-1])[:len(span)])

    rain = np.round(np.interp(ts, weather['ts'], weather['rain']) + storm, 3)

    for route in routes:
        price, minutes = getMarket(route, ts, rain)
        database.set('INSERT OR IGNORE INTO Prices (route_id, ts, rain, price, minutes) VALUES (?, ?, ?, ?, ?)', zip(repeat(route['id']), ts.tolist(), rain.tolist(), price.tolist(), minutes.tolist()))

    logging.info(f'bootstrap: {len(ts) * len(routes)} precos sinteticos de {len(routes)} rotas em {DAYS} dias, {n} tempestades injetadas')
    return True


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    database.setup()
    print('bootstrap concluido:', bootstrap())
    print(database.get('SELECT r.origin, ROUND(r.distance, 1) AS km, ROUND(r.duration, 1) AS livre, COUNT(*) AS n, ROUND(AVG(p.price), 2) AS preco, ROUND(AVG(p.minutes), 1) AS tempo, ROUND(MAX(p.minutes), 1) AS pior FROM Prices p JOIN Routes r ON r.id = p.route_id GROUP BY r.id').to_string())
