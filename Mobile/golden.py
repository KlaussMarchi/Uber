import json, os, shutil, sys, tempfile
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'Desktop'))

import Oracle.index as oracle
from Database.index import database
from Engine.index import engine
from Interface.Locator.index import Locator
from Model.index import model
from Oracle.index import FARES, TARIFFS, getMarket, getPrice, getRandom, getState, getSurge, getTariff
from Utils.functions import getClock, getDelay, getDuration, getHolidays, getSpan
from Utils.variables import COMPANIES, STEP, HORIZON


# SAIDAS DO DESKTOP QUE O APP TEM DE REPRODUZIR (SORTEIO, FERIADOS, RELOGIO, TARIFA, ORACULO E MODELO), CONFERIDAS PELO TESTE EM KOTLIN
ASSET  = os.path.join(HERE, 'app', 'src', 'main', 'assets', 'model.json')
GOLDEN = os.path.join(HERE, 'app', 'src', 'test', 'resources', 'golden.json')
ZONES  = ['America/Sao_Paulo', 'America/Manaus', 'America/Noronha', 'America/Rio_Branco']
DST    = ('2018-11-03 12:00', '2018-11-05 12:00')    # ultimo inicio de horario de verao no brasil: a meia-noite de 04/11 nao existiu
ROUTES = [
    {'id': 1, 'distance': 34.0809, 'duration': 42.7283, 'corridor': 0.6183, 'tz': 'America/Sao_Paulo'},
    {'id': 2, 'distance': 1.2, 'duration': 3.1, 'corridor': 0.0, 'tz': 'America/Sao_Paulo'},
    {'id': 14, 'distance': 185.7375, 'duration': 159.0017, 'corridor': 0.0, 'tz': 'America/Sao_Paulo'},
    {'id': 31, 'distance': 15.16, 'duration': 18.05, 'corridor': 0.97, 'tz': 'America/Manaus'},
]
MOMENTS = ['2026-10-05 07:40', '2026-10-09 17:55', '2026-10-11 15:03', '2026-10-12 09:00', '2026-10-07 02:13', '2026-10-06 23:47']

# (distancia km, minutos, dinamica) das corridas informadas em cada caso de calibracao: nenhuma, longa, curta sob o piso, varias rotas, absurda e mais de uma amostra
RIDES = [
    [],
    [('uber', 34.08, 45.0, 1.0)],
    [('uber', 1.0, 3.0, 1.0), ('uber', 1.4, 4.2, 1.2)],
    [('uber', 34.08, 45.0, 1.0), ('uber', 12.0, 20.0, 1.1), ('uber', 5.0, 12.0, 1.0), ('uber', 60.0, 70.0, 1.0), ('uber', 2.0, 6.0, 1.2), ('uber', 1.0, 3.0, 1.0)],
    [('uber', 34.08, 45.0, 1.0), ('99', 34.08, 45.0, 1.0), ('99', 8.0, 15.0, 1.4)],
    [('uber', 34.08, 45.0, 2.4)],
    [('99', 20.0 + i, 25.0 + i, 1.0 + i / 100) for i in range(25)],
]
OFFSETS = [1.0, 1.12, 0.88, 1.12, 0.94, 12.0, 1.07]    # quanto o preco informado se afasta da tabela publicada em cada caso


def getTs(text, tz='America/Sao_Paulo'):
    return int(pd.Timestamp(text, tz=tz).timestamp())

def getSample(rng, n):
    return np.concatenate([rng.integers(getTs('2017-01-01'), getTs('2040-12-31'), n), np.arange(getTs(DST[0]), getTs(DST[1]), STEP)])

def getRain(rng, n):
    return np.where(rng.random(n) < 0.35, rng.exponential(4.0, n), 0.0).round(3)

# OBSERVACOES DE HOJE ANTES DE AGORA NUMA ROTA, NO REGIME PEDIDO; O CHOQUE MULTIPLICA AS TRES ULTIMAS COMO UM ACIDENTE
def getObs(rng, route, now, first, regime, company):
    day      = getClock([now], route['tz'])[2][0]
    midnight = int(pd.Timestamp(day).tz_localize(route['tz'], nonexistent='shift_forward').timestamp())
    slots    = np.arange(midnight, now // STEP * STEP, STEP)
    past     = {'nova': slots[:0], 'poucas': slots[-3:], 'rastreada': slots, 'choque': slots}[regime]
    ts       = np.append(past, now)
    rain     = np.append(getRain(rng, len(past)), first)
    price, minutes = getMarket(route, ts, rain, company)

    if regime == 'choque' and len(past) >= 3:
        price[-4:-1] *= 1.22
        minutes[-4:-1] *= 1.55

    return pd.DataFrame({'ts': ts, 'rain': rain, 'price': price, 'minutes': minutes})

# CASOS DA CALIBRACAO DA TARIFA: OS PRECOS REAIS INFORMADOS E A TABELA QUE O AJUSTE PRODUZ A PARTIR DELES
def getFares():
    cases = []

    for rides, off in zip(RIDES, OFFSETS):
        oracle.update(pd.DataFrame(columns=['company', 'distance', 'minutes', 'surge', 'observed']))
        rows = pd.DataFrame(rides, columns=['company', 'distance', 'minutes', 'surge'])
        rows['observed'] = [round(getTariff(d, m, s, c) * off, 2) for c, d, m, s in rides]
        oracle.update(rows)
        cases.append({'rides': rows.to_dict('records'), 'fares': {company: dict(fare) for company, fare in FARES.items()}})

    oracle.update(pd.DataFrame(columns=['company', 'distance', 'minutes', 'surge', 'observed']))
    return cases


if __name__ == '__main__':
    rng    = np.random.default_rng(2026)
    golden = {}

    golden['rng'] = [{'entropy': e, 'raw': [str(v) for v in np.random.PCG64(np.random.SeedSequence(e)).random_raw(8)], 'normal': np.random.default_rng(e).normal(0, 1, 400).tolist(), 'poisson': np.random.default_rng(e).poisson(0.7, 40).tolist(), 'double': np.random.default_rng(e).random(40).tolist()} for e in [[7, 20709], [7, 3, 20709], [7, 0], [7, 2**40 + 5, 20709], [7, 14, 17808]]]
    golden['holidays'] = {str(year): sorted(day.isoformat() for day in getHolidays(year)) for year in range(2020, 2046)}
    golden['spans'] = [{'low': low, 'high': high, 'span': getSpan(low, high)} for low, high in ((40.2, 45.4), (55, 65), (59.4, 59.6), (65, 80), (0.4, 3.5), (59.5, 59.5), (119.5, 120.5), (2.5, 3.5))]
    golden['durations'] = [{'minutes': minutes, 'duration': getDuration(minutes), 'delay': getDelay(minutes)} for minutes in (0, 0.4, 0.5, 1.5, 2.5, 5, 47.5, 48.5, 59.4, 59.5, 59.6, 60, 61, 125.2, 159.0, 719.6, -0.3, -0.5, -2.6, 14.2, -65.4)]
    golden['fares'] = getFares()
    golden['tariff'] = [{'company': company, 'distance': float(d), 'duration': float(m), 'surge': float(s), 'excess': float(e), 'noise': float(n), 'tariff': float(getTariff(d, m, s, company)), 'surged': float(getSurge(e, n, company)), 'price': float(getPrice(d, m, e, n, company))}
                        for company in COMPANIES for d, m, s, e, n in zip(rng.uniform(0.1, 400, 40), rng.uniform(0.5, 300, 40), rng.uniform(1, 3, 40), rng.uniform(0, 4, 40), rng.normal(0, 0.03, 40))]

    # ponto inicial do mapa do botao de localizacao: estimativa precisa, grosseira com e sem lugar usado dentro do erro, limite de precisao e sem estimativa
    near, far = {'label': 'Praia do Pecado, Macaé', 'lat': -22.4114, 'lon': -41.8084}, {'label': 'Centro de São Paulo', 'lat': -23.5505, 'lon': -46.6333}
    coarse    = {'label': 'Rio das Ostras, Rio de Janeiro', 'lat': -22.5269, 'lon': -41.9450, 'accuracy': 25000.0}
    starts    = [({**near, 'accuracy': 8.0}, [far]), (coarse, [far, near]), (coarse, [far]), ({**coarse, 'accuracy': 1000.0}, [near]), ({**coarse, 'accuracy': 18000.0}, [near]), ({**coarse, 'accuracy': 20000.0}, [near]), (None, [far, near]), (None, [])]
    golden['starts'] = [{'estimate': estimate, 'recent': recent, 'label': place['label'], 'lat': place['lat'], 'lon': place['lon'], 'zoom': zoom, 'mode': mode} for estimate, recent in starts for place, zoom, mode in [Locator.getStart(Locator, estimate, recent)]]

    ts = getSample(rng, 3000)
    golden['clock'] = [{'tz': tz, 'ts': ts.tolist(), 'weekday': w.tolist(), 'hour': h.tolist(), 'day': d.astype(np.int64).tolist()} for tz in ZONES for w, h, d in [getClock(ts, tz)]]

    golden['random'] = []
    golden['market'] = []

    for route in ROUTES:
        ts   = getSample(rng, 1500)
        rain = getRain(rng, len(ts))
        days = getClock(ts, route['tz'])[2]
        incident, noise, jitter = getRandom(ts, route['id'], days, route['tz'])
        excess, supply, minutes = getState(route, ts, rain)
        golden['random'].append({'route': route, 'ts': ts.tolist(), 'incident': incident.tolist(), 'noise': noise.tolist(), 'jitter': jitter.tolist()})

        for company in COMPANIES:
            price, times = getMarket(route, ts, rain, company)
            golden['market'].append({'route': route, 'company': company, 'ts': ts.tolist(), 'rain': rain.tolist(), 'excess': excess.tolist(), 'noise': supply.tolist(), 'price': price.tolist(), 'minutes': times.tolist()})

    model.path = sys.argv[1] if len(sys.argv) > 1 else model.path

    if model.setup():
        os.makedirs(os.path.dirname(ASSET), exist_ok=True)
        shutil.copyfile(model.path, ASSET)
        frame = pd.DataFrame({'ts': rng.integers(getTs('2026-01-01'), getTs('2027-12-31'), 60), 'rain': np.append(getRain(rng, 56), [0.0, 12.0, 15.5, 0.25]), 'distance': rng.uniform(0.5, 300, 60), 'duration': rng.uniform(1, 240, 60), 'corridor': rng.uniform(0, 1, 60), 'company': rng.integers(0, len(COMPANIES), 60), 'tz': 'America/Sao_Paulo'})
        X = model.process(frame, *model.getClock(frame)[:2])
        golden['quantiles'] = {'frame': {key: frame[key].tolist() for key in ('ts', 'rain', 'distance', 'duration', 'corridor', 'company')}, 'raw': {column: {key: booster.predict(X).tolist() for key, booster in model.boosters[column].items()} for column in model.TARGETS}, 'sorted': {column: [q.tolist() for q in model.getQuantiles(model.boosters[column], X)] for column in model.TARGETS}}
        golden['model'] = []

        for route in ROUTES:
            for company in COMPANIES:
                for text in MOMENTS:
                    now  = getTs(text, route['tz']) + int(rng.integers(0, STEP))
                    ts   = np.append(now, np.arange((now // STEP + 1) * STEP, now + HORIZON + 1, STEP))
                    rain = getRain(rng, len(ts))
                    grid = pd.DataFrame({'ts': ts, 'rain': rain})

                    for regime in ('nova', 'poucas', 'rastreada', 'choque'):
                        obs = getObs(rng, route, now, rain[0], regime, company)
                        out = model.get(route, grid, obs, company)
                        golden['model'].append({'route': route, 'company': company, 'regime': regime, 'ts': ts.tolist(), 'rain': rain.tolist(), 'obs': {key: obs[key].tolist() for key in obs}, 'out': {key: out[key].tolist() for key in out}})

        # serie completa do engine com clima fixo e observacoes esparsas de hoje num banco temporario, como o worker deixaria
        database.path = os.path.join(tempfile.mkdtemp(prefix='golden-'), 'surge.db')
        database.setup()
        golden['engine'] = []

        for route in ROUTES:
            row = {**route, 'o_lat': -22.34, 'o_lon': -41.75}
            database.set('INSERT INTO Routes (id, origin, destination, o_lat, o_lon, d_lat, d_lon, distance, duration, corridor, tz, used_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)', [(route['id'], 'origem', 'destino', -22.34, -41.75, -22.5, -41.9 - route['id'] / 1000, route['distance'], route['duration'], route['corridor'], route['tz'])])

            for text in MOMENTS[:4]:
                now      = getTs(text, route['tz']) + int(rng.integers(0, STEP))
                hours    = np.arange((now // 3600 - 30) * 3600, (now // 3600 + 40) * 3600, 3600)
                weather  = pd.DataFrame({'ts': hours - 1800, 'rain': getRain(rng, len(hours)).round(1), 'probability': rng.integers(0, 101, len(hours)).astype(float)})
                day      = getClock([now], route['tz'])[2][0]
                midnight = int(pd.Timestamp(day).tz_localize(route['tz'], nonexistent='shift_forward').timestamp())
                past     = np.arange(midnight, now // STEP * STEP, STEP)[::2]
                rain     = getRain(rng, len(past))
                excess, noise, minutes = getState(route, past, rain)
                database.set('INSERT OR IGNORE INTO Prices (route_id, ts, rain, excess, noise, minutes) VALUES (?, ?, ?, ?, ?, ?)', zip([route['id']] * len(past), past.tolist(), rain.tolist(), excess.tolist(), noise.tolist(), minutes.tolist()))

                for company in COMPANIES:
                    engine.getWeather = lambda lat, lon: weather
                    df = engine.get(row, company, now)
                    del engine.getWeather
                    golden['engine'].append({'route': route, 'company': company, 'now': now, 'weather': {key: weather[key].tolist() for key in weather}, 'today': {'ts': past.tolist(), 'rain': rain.tolist(), 'excess': excess.tolist(), 'noise': noise.tolist(), 'minutes': minutes.tolist()}, 'out': {key: df[key].tolist() for key in df}})

        print(f"modelo v{model.version} copiado para os assets, {len(golden['model'])} casos do modelo e {len(golden['engine'])} do engine")
    else:
        print('sem modelo legivel: golden sem a secao do modelo')

    os.makedirs(os.path.dirname(GOLDEN), exist_ok=True)

    with open(GOLDEN, 'w') as file:
        json.dump(golden, file)

    print(f'{GOLDEN}: {os.path.getsize(GOLDEN) / 1e6:.1f} MB')
