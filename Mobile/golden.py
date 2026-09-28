import json, os, shutil, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'Desktop'))

import Oracle.index as oracle
from Engine.index import engine
from Interface.Locator.index import Locator
from Model.index import get, getNormal, getQuantile
from Oracle.index import getAnchor, getCalibration, getFree, getKey, getMarket, getProfile, getShare, getState, getSurge, getTariff
from Utils.functions import getClock, getDelay, getDuration, getHolidays, getSpan
from Utils.variables import COMPANIES, MARKET_PATH, STEP, HORIZON


# SAIDAS DO DESKTOP QUE O APP TEM DE REPRODUZIR (FERIADOS, RELOGIO, TABELA, MERCADO, CALIBRACAO, FAIXAS E SERIE), CONFERIDAS PELO TESTE EM KOTLIN
ASSET  = os.path.join(HERE, 'app', 'src', 'main', 'assets', 'markets.json')
GOLDEN = os.path.join(HERE, 'app', 'src', 'test', 'resources', 'golden.json')
ZONES  = ['America/Sao_Paulo', 'America/Manaus', 'America/Noronha', 'America/Rio_Branco']
DST    = ('2018-11-03 12:00', '2018-11-05 12:00')    # ultimo inicio de horario de verao no brasil: a meia-noite de 04/11 nao existiu

# rotas com municipio conhecido, com municipio sem trecho publicado, sem municipio e num estado fora da amostra; curtas, longas e em outro fuso
ROUTES = [
    {'id': 1, 'o_lat': -22.34137, 'o_lon': -41.75567, 'distance': 34.0809, 'duration': 42.7283, 'tz': 'America/Sao_Paulo', 'city': 'Macaé', 'uf': 'RJ'},
    {'id': 2, 'o_lat': -22.9711, 'o_lon': -43.1822, 'distance': 1.2, 'duration': 3.1, 'tz': 'America/Sao_Paulo', 'city': 'Rio de Janeiro', 'uf': 'RJ'},
    {'id': 3, 'o_lat': -22.2667, 'o_lon': -41.6667, 'distance': 185.7375, 'duration': 159.0017, 'tz': 'America/Sao_Paulo', 'city': 'Carapebus', 'uf': 'RJ'},
    {'id': 4, 'o_lat': -23.5614, 'o_lon': -46.6559, 'distance': 5.25, 'duration': 11.4, 'tz': 'America/Sao_Paulo', 'city': '', 'uf': ''},
    {'id': 5, 'o_lat': -3.1316, 'o_lon': -60.0233, 'distance': 15.16, 'duration': 18.05, 'tz': 'America/Manaus', 'city': 'Manaus', 'uf': 'AM'},
]
MOMENTS = ['2026-10-05 07:40', '2026-10-09 17:55', '2026-10-11 15:03', '2026-10-12 09:00', '2026-10-07 02:13', '2026-10-06 23:47']
COLUMNS = ['company', 'ts', 'route_id', 'lat', 'lon', 'city', 'uf', 'level', 'expected', 'observed']


def getTs(text, tz='America/Sao_Paulo'):
    return int(pd.Timestamp(text, tz=tz).timestamp())

def getSample(rng, n):
    return np.concatenate([rng.integers(getTs('2017-01-01'), getTs('2040-12-31'), n), np.arange(getTs(DST[0]), getTs(DST[1]), STEP)])

def getRain(rng, n):
    return np.where(rng.random(n) < 0.35, rng.exponential(4.0, n), 0.0).round(3)

def getChance(rng, n):
    return np.where(rng.random(n) < 0.1, np.nan, rng.integers(0, 101, n).astype(float))

# PRECOS INFORMADOS DE UM CASO DE CALIBRACAO: EM TORNO DA ORIGEM DA ROTA, EM OUTRAS CIDADES, SOB O PISO, ABSURDOS E MAIS QUE A AMOSTRA
def getRides(rng, case, now):
    kinds = {'nenhum': 0, 'um': 1, 'varios': 6, 'absurdo': 2, 'muitos': 26}
    rows  = []

    for i in range(kinds[case]):
        route    = ROUTES[int(rng.integers(0, len(ROUTES)))]
        company  = list(COMPANIES)[int(rng.integers(0, 2))] if case != 'um' else 'uber'
        expected = float(rng.uniform(8, 300))
        off      = 30.0 if case == 'absurdo' and i == 1 else float(rng.uniform(0.8, 1.3))
        rows.append({'company': company, 'ts': int(now - rng.integers(60, 20000)), 'route_id': route['id'], 'lat': route['o_lat'] + rng.normal(0, 0.05), 'lon': route['o_lon'] + rng.normal(0, 0.05), 'city': route['city'], 'uf': route['uf'], 'level': float(rng.normal(0, 0.1)), 'expected': expected, 'observed': round(expected * off, 2)})

    return pd.DataFrame(rows, columns=COLUMNS)


if __name__ == '__main__':
    rng    = np.random.default_rng(2026)
    golden = {}
    oracle.load()
    os.makedirs(os.path.dirname(ASSET), exist_ok=True)
    shutil.copyfile(MARKET_PATH, ASSET)

    golden['holidays']  = {str(year): sorted(day.isoformat() for day in getHolidays(year)) for year in range(2020, 2046)}
    golden['spans']     = [{'low': low, 'high': high, 'span': getSpan(low, high)} for low, high in ((40.2, 45.4), (55, 65), (59.4, 59.6), (65, 80), (0.4, 3.5), (59.5, 59.5), (119.5, 120.5), (2.5, 3.5))]
    golden['durations'] = [{'minutes': minutes, 'duration': getDuration(minutes), 'delay': getDelay(minutes)} for minutes in (0, 0.4, 0.5, 1.5, 2.5, 5, 47.5, 48.5, 59.4, 59.5, 59.6, 60, 61, 125.2, 159.0, 719.6, -0.3, -0.5, -2.6, 14.2, -65.4)]

    # ponto inicial do mapa do botao de localizacao: estimativa precisa, grosseira com e sem lugar usado dentro do erro, limite de precisao e sem estimativa
    near, far = {'label': 'Praia do Pecado, Macaé', 'lat': -22.4114, 'lon': -41.8084}, {'label': 'Centro de São Paulo', 'lat': -23.5505, 'lon': -46.6333}
    coarse    = {'label': 'Rio das Ostras, Rio de Janeiro', 'lat': -22.5269, 'lon': -41.9450, 'accuracy': 25000.0}
    starts    = [({**near, 'accuracy': 8.0}, [far]), (coarse, [far, near]), (coarse, [far]), ({**coarse, 'accuracy': 1000.0}, [near]), ({**coarse, 'accuracy': 18000.0}, [near]), ({**coarse, 'accuracy': 20000.0}, [near]), (None, [far, near]), (None, [])]
    golden['starts'] = [{'estimate': estimate, 'recent': recent, 'label': place['label'], 'lat': place['lat'], 'lon': place['lon'], 'zoom': zoom, 'mode': mode} for estimate, recent in starts for place, zoom, mode in [Locator.getStart(Locator, estimate, recent)]]

    ts = getSample(rng, 3000)
    golden['clock'] = [{'tz': tz, 'ts': ts.tolist(), 'weekday': w.tolist(), 'hour': h.tolist(), 'day': d.astype(np.int64).tolist()} for tz in ZONES for w, h, d in [getClock(ts, tz)]]

    # municipio pela chave sem acento; perfil da semana; mercado na origem em municipios da tabela, vizinhos, estados sem cidade perto e fora da amostra
    golden['keys'] = [{'city': city, 'uf': uf, 'key': getKey(city, uf)} for city, uf in (('São João de Meriti', 'RJ'), ('SAO JOAO DE MERITI', 'rj'), ('Macaé', 'RJ'), ('Niterói', 'RJ'), ('', ''), ('Mogi das Cruzes', 'SP'), ('Guarujá', 'SP'))]
    weekday, hour = rng.integers(0, 7, 500), rng.uniform(0, 24, 500)
    golden['profile'] = {'weekday': weekday.tolist(), 'hour': hour.tolist(), 'traffic': getProfile(weekday, hour)[0].tolist(), 'demand': getProfile(weekday, hour)[1].tolist()}
    cities = oracle.MARKET['cities']
    places = [(city[2], city[3], city[0], city[1]) for city in [cities[i] for i in rng.choice(len(cities), 25, replace=False)]]
    places += [(city[2] + rng.normal(0, 0.15), city[3] + rng.normal(0, 0.15), '', '') for city in [cities[i] for i in rng.choice(len(cities), 25, replace=False)]]
    places += [(city[2] + rng.normal(0, 0.3), city[3] + rng.normal(0, 0.3), 'Cidade Sem Trecho', city[1]) for city in [cities[i] for i in rng.choice(len(cities), 15, replace=False)]]
    places += [(-3.1316, -60.0233, 'Manaus', 'AM'), (-9.97, -67.81, '', 'AC'), (-15.0, -40.0, '', ''), (-22.2667, -41.6667, 'Carapebus', 'RJ')]
    golden['markets'] = [{'lat': lat, 'lon': lon, 'city': city, 'uf': uf, **getMarket(lat, lon, city, uf)} for lat, lon, city, uf in places]

    golden['state'] = []

    for route in ROUTES:
        ts   = getSample(rng, 400)
        rain = getRain(rng, len(ts))
        excess, traffic, minutes = getState(route, ts, rain)
        golden['state'].append({'route': route, 'ts': ts.tolist(), 'rain': rain.tolist(), 'excess': excess.tolist(), 'traffic': traffic.tolist(), 'minutes': minutes.tolist(), 'free': getFree(route)})

    golden['tariff'] = [{'company': company, 'level': float(level), 'distance': float(d), 'minutes': float(m), 'excess': float(e), 'tariff': float(getTariff(level, d, m, e, company)), 'share': float(getShare(d, m)), 'surge': float(getSurge(e, company))}
                        for company in COMPANIES for level, d, m, e in zip(rng.normal(0, 0.15, 40), rng.uniform(0.1, 400, 40), rng.uniform(0.5, 300, 40), rng.uniform(0, 4, 40))]

    z = np.concatenate([np.linspace(-9, 9, 721), rng.normal(0, 3, 200)])
    golden['normal'] = {'z': z.tolist(), 'cdf': getNormal(z).tolist()}
    mixtures = [(float(rng.choice([0.0, rng.uniform(0, 1)])), rng.normal(0, 2, 2), rng.uniform(1e-4, 1, 2)) for _ in range(120)]
    golden['quantiles'] = [{'q': q, 'wet': wet, 'means': means.tolist(), 'sigmas': sigmas.tolist(), 'value': float(getQuantile(q, np.array([wet]), [np.array([means[0]]), np.array([means[1]])], [np.array([sigmas[0]]), np.array([sigmas[1]])])[0])} for wet, means, sigmas in mixtures for q in (0.1, 0.5, 0.9)]

    # calibracao, faixas do modelo e a serie completa do engine com clima fixo e precos informados, nos casos que a tela e o worker produzem
    golden['calibration'] = []
    golden['model']       = []
    golden['engine']      = []

    for case in ('nenhum', 'um', 'varios', 'absurdo', 'muitos'):
        now   = getTs(MOMENTS[len(golden['calibration']) % len(MOMENTS)])
        rides = getRides(rng, case, now)
        oracle.update(rides)
        stored = oracle.FARES

        for route in ROUTES:
            for company in COMPANIES:
                cal = getCalibration(route, company, now)
                golden['calibration'].append({'case': case, 'rides': rides.to_dict('records'), 'route': route, 'company': company, 'now': now, 'kept': len(stored), **{key: value for key, value in cal.items() if key != 'market'}, 'market': cal['market']})

    for route in ROUTES:
        for company in COMPANIES:
            for text in MOMENTS:
                now   = getTs(text, route['tz']) + int(rng.integers(0, STEP))
                rides = getRides(rng, ['nenhum', 'um', 'varios'][int(rng.integers(0, 3))], now)
                ts    = np.append(now, np.arange((now // STEP + 1) * STEP, now + HORIZON + 1, STEP))
                rain  = getRain(rng, len(ts))
                odds  = getChance(rng, len(ts))
                oracle.update(rides)

                for label, cal in (('calibrada', getCalibration(route, company, now)), ('ancora', getAnchor(route, now))):
                    out = get(route, ts, rain, odds, company, cal)
                    golden['model'].append({'route': route, 'company': company, 'label': label, 'rides': rides.to_dict('records'), 'now': now, 'ts': ts.tolist(), 'rain': rain.tolist(), 'probability': [None if np.isnan(v) else v for v in odds], 'out': {key: out[key].tolist() for key in out}})

    for route in ROUTES:
        for text in MOMENTS[:4]:
            now     = getTs(text, route['tz']) + int(rng.integers(0, STEP))
            hours   = np.arange((now // 3600 - 30) * 3600, (now // 3600 + 40) * 3600, 3600)
            weather = pd.DataFrame({'ts': hours - 1800, 'rain': getRain(rng, len(hours)).round(1), 'probability': rng.integers(0, 101, len(hours)).astype(float)})
            rides   = getRides(rng, 'varios', now)
            oracle.update(rides)

            for company in COMPANIES:
                engine.getWeather = lambda lat, lon: weather
                df = engine.get(route, company, now)
                del engine.getWeather
                golden['engine'].append({'route': route, 'company': company, 'now': now, 'rides': rides.to_dict('records'), 'weather': {key: weather[key].tolist() for key in weather}, 'out': {key: df[key].tolist() for key in df}})

    oracle.update(pd.DataFrame(columns=COLUMNS))
    os.makedirs(os.path.dirname(GOLDEN), exist_ok=True)

    with open(GOLDEN, 'w', encoding='utf-8') as file:
        json.dump(golden, file)

    print(f"tabela de {oracle.MARKET['updated']} copiada para os assets; {len(golden['model'])} casos do modelo, {len(golden['calibration'])} de calibração e {len(golden['engine'])} do engine")
    print(f'{GOLDEN}: {os.path.getsize(GOLDEN) / 1e6:.1f} MB')
