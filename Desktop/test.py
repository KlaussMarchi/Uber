import os, shutil, tempfile, logging
import bootstrap
import time as clock
import numpy as np
import pandas as pd
import Model.index
import Worker.index
from datetime import date, timedelta
from Api.index import api
from Database.index import database
from Engine.index import engine
from Model.index import model
from Oracle.index import getMarket, getRandom, getTariff
from Utils.functions import getClock, getDelay, getDistance, getDuration, getHolidays, getMoney, getSpan
from Utils.sound import getTone
from Utils.variables import ORIGIN, DESTINATION, TZ, STEP, HORIZON
from Worker.index import worker


FOLDER   = tempfile.mkdtemp(prefix='tarifa-')    # banco e modelo isolados dos dados do app
LEADS    = HORIZON // STEP + 1
failures = []

# (nome, instante da ancora, chuva mm/h, faixa plausivel do preco uma hora depois) na rota de referencia
SCENARIOS = [
    ('madrugada de quarta, seco',     '2026-09-09 02:00', 0.0, (50, 58)),
    ('meio da manhã de quarta, seco', '2026-09-09 09:30', 0.0, (50, 62)),
    ('rush de quarta, seco',          '2026-09-09 16:50', 0.0, (60, 80)),
    ('rush de sexta, seco',           '2026-09-11 17:00', 0.0, (62, 84)),
    ('rush de sexta, chuva forte',    '2026-09-11 17:00', 8.0, (80, 104)),
    ('domingo à tarde, seco',         '2026-09-13 16:00', 0.0, (54, 74)),
    ('feriado 7 de setembro, manhã',  '2026-09-07 06:00', 0.0, (50, 62)),
    ('segunda comum, manhã',          '2026-09-14 06:00', 0.0, (54, 76)),
]

# rotas reais de varias cidades e fusos para conferir distancia, tempo e preco em qualquer lugar do pais
MATRIX = [
    ('Copacabana → Ipanema (RJ)',   {'label': 'Copacabana', 'lat': -22.9711, 'lon': -43.1822}, {'label': 'Ipanema', 'lat': -22.9838, 'lon': -43.2096}, 'America/Sao_Paulo'),
    ('Paulista → Ibirapuera (SP)',  {'label': 'Avenida Paulista', 'lat': -23.5614, 'lon': -46.6559}, {'label': 'Ibirapuera', 'lat': -23.5874, 'lon': -46.6576}, 'America/Sao_Paulo'),
    ('Rio → Niterói (ponte)',       {'label': 'Centro do Rio', 'lat': -22.9035, 'lon': -43.2096}, {'label': 'Centro de Niterói', 'lat': -22.8940, 'lon': -43.1150}, 'America/Sao_Paulo'),
    ('Rio → São Paulo (430 km)',    {'label': 'Centro do Rio', 'lat': -22.9068, 'lon': -43.1729}, {'label': 'Centro de São Paulo', 'lat': -23.5505, 'lon': -46.6333}, 'America/Sao_Paulo'),
    ('Manaus centro → aeroporto',   {'label': 'Centro de Manaus', 'lat': -3.1316, 'lon': -60.0233}, {'label': 'Aeroporto de Manaus', 'lat': -3.0386, 'lon': -60.0497}, 'America/Manaus'),
]


def check(name, ok, detail=''):
    print(f"[{'ok' if ok else 'FALHOU'}] {name} {detail}")

    if not ok:
        failures.append(name)

# TIMESTAMP UNIX DE UMA DATA NO FUSO INFORMADO
def getTs(text, tz=TZ):
    return int(pd.Timestamp(text, tz=tz).timestamp())

# PREVISAO FORCADA COM CHUVA CONSTANTE, PARA TESTAR CENARIOS SEM DEPENDER DO TEMPO REAL
def getWeather(start, mm):
    ts = np.arange(start - 86400, start + 2 * 86400, STEP)
    return pd.DataFrame({'ts': ts, 'rain': np.full(len(ts), mm), 'probability': np.full(len(ts), 90.0 if mm else 0.0)})

# SERIE DO ENGINE PARA UMA ROTA EM UM INSTANTE E CHUVA FORCADOS
def getSeries(route, ts, mm=0.0):
    engine.getWeather = lambda lat, lon: getWeather(ts, mm)

    try:
        return engine.get(route, ts)
    finally:
        del engine.getWeather

def wait(app, condition, timeout):
    start = clock.time()

    while clock.time() - start < timeout:
        app.update()

        if condition():
            return True

        clock.sleep(0.05)

    return False

# RELOGIO, FERIADOS E ALERTAS SONOROS
def testUtils():
    expected = {date(2026, 1, 1), date(2026, 2, 16), date(2026, 2, 17), date(2026, 4, 3), date(2026, 4, 21), date(2026, 5, 1), date(2026, 6, 4), date(2026, 9, 7), date(2026, 10, 12), date(2026, 11, 2), date(2026, 11, 15), date(2026, 11, 20), date(2026, 12, 25)}
    check('feriados nacionais de 2026 iguais ao calendário ANBIMA', getHolidays(2026) == expected, sorted(getHolidays(2026) ^ expected))
    check('páscoa correta de 2024 a 2038', all(easter - timedelta(days=2) in getHolidays(year) for year, easter in [(2024, date(2024, 3, 31)), (2025, date(2025, 4, 20)), (2026, date(2026, 4, 5)), (2027, date(2027, 3, 28)), (2030, date(2030, 4, 21)), (2038, date(2038, 4, 25))]))

    weekday, hour, day = getClock([getTs('2026-09-07 07:30'), getTs('2026-09-08 07:30'), getTs('2026-09-13 23:50')], TZ)
    check('feriado vale como domingo, dia e hora locais corretos', weekday.tolist() == [6, 1, 6] and hour.round(2).tolist() == [7.5, 7.5, 23.83] and str(day[0]) == '2026-09-07')

    instant = getTs('2026-09-08 07:00', 'America/Manaus')
    check('mesma hora local em fusos diferentes', getClock([instant], 'America/Manaus')[1][0] == 7.0 and getClock([instant], TZ)[1][0] == 8.0)
    check('valor em reais no padrão brasileiro', getMoney(1234.5) == 'R$ 1.234,50')
    check('tempo de viagem em minutos até 1 h e em horas depois, sem repetir a unidade na faixa', [getDuration(47.5), getDuration(59.6), getDuration(125.2), getDelay(-2.6), getDelay(65), getSpan(40.2, 45.4), getSpan(55, 65), getSpan(65, 80)] == ['48 min', '1h00', '2h05', '-3 min', '+1h05', '40 a 45 min', '55 min a 1h05', '1h05 a 1h20'])
    check('alertas sonoros gerados nos dois tons', all(len(getTone(kind)) > 20000 for kind in ('good', 'bad')))

# SERVICOS ABERTOS: AUTOCOMPLETE, GEOCODIFICACAO, ROTA, FUSO, LOCALIZACAO E FALHA DE REDE SEM EXCECAO
def testApi():
    places = api.search('Rua Professor Antônio Ava')
    check('autocomplete casa o prefixo da rua', bool(places) and 'Avarez Parada' in places[0]['label'], places[0]['label'] if places else places)
    check('autocomplete só devolve lugares no Brasil', all(-34 < p['lat'] < 6 and -74 < p['lon'] < -34 for p in api.search('Teatro Mun') or [{'lat': 99, 'lon': 0}]))
    check('texto livre é geocodificado', api.geocode('Praia de Costazul, Rio das Ostras') is not None)
    check('fusos por coordenada', (api.getZone(-22.34, -41.75), api.getZone(-3.13, -60.02), api.getZone(-9.97, -67.81)) == ('America/Sao_Paulo', 'America/Manaus', 'America/Rio_Branco'))

    place = api.locate()
    check('localização atual com precisão declarada', place is not None and -34 < place['lat'] < 6 and place['accuracy'] > 0, f"{place['label']} ±{place['accuracy']:.0f} m" if place else None)

    weather = api.getWeather(-22.43, -41.85, past_days=1, forecast_days=2)
    check('previsão horária do Open-Meteo cobre o horizonte com chance de chuva', weather is not None and weather['ts'].max() - clock.time() > HORIZON and weather['probability'].between(0, 100).all(), None if weather is None else f'{len(weather)} horas')
    check('servidor inexistente devolve None', api.get('https://servidor.invalido/', {}) is None)

# DISTANCIA E TEMPO: OSRM CONFERIDO CONTRA LINHA RETA, CONTRA OUTRO SERVIDOR E CONTRA PONTOS SEM ACESSO DE CARRO
def testDistance():
    route = api.getRoute(ORIGIN, DESTINATION)
    straight = getDistance(ORIGIN, DESTINATION)
    check('rota de referência: 34 km, 43 min e 62% na RJ-106', route is not None and abs(route['distance'] - 34.08) < 1 and abs(route['duration'] - 42.7) < 3 and 0.55 < route['corridor'] < 0.70, route)
    check('distância rodoviária maior que a linha reta e no máximo o triplo', straight < route['distance'] < 3 * straight, f"reta {straight:.1f} km, estrada {route['distance']:.1f} km")

    other = api.get('https://routing.openstreetmap.de/routed-car/route/v1/driving/' + f"{ORIGIN['lon']},{ORIGIN['lat']};{DESTINATION['lon']},{DESTINATION['lat']}", {'overview': 'false'})
    check('segundo servidor OSRM confirma distância e tempo', other is not None and abs(other['routes'][0]['distance'] / 1000 - route['distance']) < 0.5 and abs(other['routes'][0]['duration'] / 60 - route['duration']) < 2)
    check('ponto no mar e ilha sem estrada não viram rota', api.getRoute({'lat': -3.8547, 'lon': -32.4247}, {'lat': -8.0476, 'lon': -34.8770}) is None and api.getRoute({'lat': -22.60, 'lon': -41.60}, {'lat': -22.37, 'lon': -41.78}) is None)
    check('avenida homônima em Niterói não conta como corredor', api.getRoute({'lat': -22.8935, 'lon': -43.1234}, {'lat': -22.9026, 'lon': -43.1076})['corridor'] == 0)

    primary  = api.OSRM
    api.OSRM = ('https://servidor.invalido/route/v1/driving/', primary[1])
    fallback = api.getRoute(ORIGIN, DESTINATION)
    api.OSRM = primary
    check('OSRM principal fora do ar cai para o espelho da FOSSGIS com a mesma rota', fallback is not None and abs(fallback['distance'] - route['distance']) < 0.5, fallback)

# PRIMEIRO BOOT ISOLADO: HISTORICO SINTETICO COM FUSO, TREINO, CALIBRACAO E RECARGA DO MODELO SALVO
def testBootstrap():
    database.path = os.path.join(FOLDER, 'surge.db')
    model.path    = os.path.join(FOLDER, 'model.json')
    start = clock.time()
    check('primeiro boot: bootstrap + treino', worker.setup() and model.ready(), f'{clock.time() - start:.0f} s')

    routes = database.get('SELECT * FROM Routes ORDER BY id').to_dict('records')
    counts = database.get('SELECT route_id, COUNT(*) AS n FROM Prices GROUP BY route_id')
    check(f'{len(bootstrap.ROUTES)} rotas reais com fuso e um ano de preços e tempos a cada 10 min', len(routes) == len(bootstrap.ROUTES) and all(r['tz'] == TZ for r in routes) and (counts['n'] >= 365 * 144).all() and database.get('SELECT MIN(minutes) AS m FROM Prices')['m'][0] > 0, counts['n'].tolist())
    check('rotas de 4 a 186 km e de 0 a 97% no corredor, com rota longa fora dele e curta dentro dele', min(r['distance'] for r in routes) < 5 and max(r['distance'] for r in routes) > 150 and any(r['distance'] > 30 and r['corridor'] < 0.1 for r in routes) and any(r['distance'] < 15 and r['corridor'] > 0.9 for r in routes), [(round(r['distance']), round(r['corridor'], 2)) for r in routes])
    check('erro na calibração abaixo de 2,5% no preço e 4,5% no tempo', max(model.metrics['price']['mape']) < 0.025 and max(model.metrics['minutes']['mape']) < 0.045, f"preço até {max(model.metrics['price']['mape']):.2%}, tempo até {max(model.metrics['minutes']['mape']):.2%}")
    check('setup repetido não retreina', worker.setup() and model.version == 1)
    check('rotas do bootstrap não aparecem como lugares já usados', engine.getPlaces() == [], engine.getPlaces())

    copy = type(model)()
    copy.path = os.path.join(FOLDER, 'corrompido.json')

    with open(copy.path, 'w') as file:
        file.write('{"boosters": ')

    check('modelo salvo corrompido é recusado sem exceção', copy.setup() is False)
    return routes

# ORACULO: PRECO NORMAL NA FAIXA PEDIDA, PICO COM CHUVA, FERIADO MAIS BARATO, TEMPO DE VIAGEM COERENTE E DETERMINISMO
def testOracle(route):
    ts = np.arange(getTs('2025-09-15'), getTs('2025-09-15') + 364 * 86400, STEP)
    weekday, hour, _ = getClock(ts, TZ)
    normal = (weekday < 5) & (hour >= 9) & (hour < 16) & ((hour < 12) | (hour >= 13))
    price, minutes = getMarket(route, ts[normal], np.zeros(normal.sum()))
    median = float(np.median(price))
    check('preço normal (dia útil 9-16 h, seco) entre R$ 54,67 e R$ 55,00', 54.67 <= median <= 55.00, getMoney(median))
    check('tempo fora do pico fica perto do tempo sem trânsito', abs(np.median(minutes) / route['duration'] - 1) < 0.08, f"{np.median(minutes):.1f} min x {route['duration']:.1f} min livre")

    storm, slow = getMarket(route, [getTs('2026-09-11 18:00')], [8.0])
    check('sexta 18 h com chuva forte chega a R$ 90 e alonga muito a viagem', storm[0] >= 88 and slow[0] >= 1.4 * route['duration'], f'{getMoney(storm[0])} e {slow[0]:.0f} min')

    holiday, common = getMarket(route, [getTs('2026-09-07 07:00'), getTs('2026-09-14 07:00')], [0, 0])[0]
    check('feriado de manhã custa bem menos que segunda comum', common - holiday > 5, f'{getMoney(holiday)} x {getMoney(common)}')

    rush, calm = getMarket(route, [getTs('2026-09-09 17:50'), getTs('2026-09-09 10:30')], [0, 0])[1]
    check('rush leva bem mais tempo que o meio da manhã', rush > calm * 1.3, f'{rush:.0f} min x {calm:.0f} min')

    rains = getMarket(route, [getTs('2026-09-09 10:30')] * 5, [0, 1, 3, 6, 10])
    check('no oráculo mais chuva nunca barateia nem acelera', bool(np.all(np.diff(rains[0]) >= 0) and np.all(np.diff(rains[1]) >= 0)), rains[1])
    check('mesmo instante, mesmo preço e mesmo tempo', all(np.array_equal(a, b) for a, b in zip(getMarket(route, ts[:300], np.zeros(300)), getMarket(route, ts[:300], np.zeros(300)))))

    gap = np.arange(getTs('2018-11-03 12:00'), getTs('2018-11-05 12:00'), STEP)
    check('dia sem meia-noite (horário de verão de 2018) não quebra o oráculo', len(getMarket(route, gap, np.zeros(len(gap)))[0]) == len(gap))

# CENARIOS DE HORARIO, CHUVA E FERIADO: A PREVISAO DE UMA HORA A FRENTE, DE PRECO E DE TEMPO, CONTRA O QUE O ORACULO COBRA
def testScenarios(route):
    rows = []

    for name, text, mm, (lo, hi) in SCENARIOS:
        anchor = getTs(text)
        df     = getSeries(route, anchor, mm)
        row    = df[df['ts'] == anchor + 3600].iloc[0]
        price, minutes = [value[0] for value in getMarket(route, [anchor + 3600], [mm])]
        rows.append({'cenário': name, 'previsto': row['p50'], 'real': price, 'erro': abs(row['p50'] / price - 1), 'dentro': lo <= price <= hi, 'na faixa': row['p10'] <= price <= row['p90'],
                     'tempo': row['m50'], 'tempo real': minutes, 'erro tempo': abs(row['m50'] / minutes - 1), 'tempo na faixa': row['m10'] <= minutes <= row['m90']})

    table = pd.DataFrame(rows)
    print(table.round(3).to_string(index=False))
    check('previsão de preço em 1 h com erro médio abaixo de 3% e nenhum acima de 8%', table['erro'].mean() < 0.03 and table['erro'].max() < 0.08, f"médio {table['erro'].mean():.2%}, máximo {table['erro'].max():.2%}")
    check('preço real dentro da faixa esperada em todos os cenários', table['dentro'].all(), table.loc[~table['dentro'], 'cenário'].tolist())
    check('faixa p10-p90 cobre o preço real em pelo menos 6 dos 8 cenários', table['na faixa'].sum() >= 6, f"{table['na faixa'].sum()} de {len(table)}")
    check('previsão de tempo em 1 h com erro médio abaixo de 5% e nenhum acima de 12%', table['erro tempo'].mean() < 0.05 and table['erro tempo'].max() < 0.12, f"médio {table['erro tempo'].mean():.2%}, máximo {table['erro tempo'].max():.2%}")
    check('faixa m10-m90 cobre o tempo real em pelo menos 5 dos 8 cenários', table['tempo na faixa'].sum() >= 5, f"{table['tempo na faixa'].sum()} de {len(table)}")

    dry  = getSeries(route, getTs('2026-09-11 16:00'), 0.0)
    wet  = getSeries(route, getTs('2026-09-11 16:00'), 8.0)
    check('chuva forte encarece o pico da janela em pelo menos R$ 10', wet['p50'].max() - dry['p50'].max() >= 10, f"{getMoney(dry['p50'].max())} x {getMoney(wet['p50'].max())}")
    check('chuva forte também alonga a viagem prevista', wet['m50'].max() > dry['m50'].max() * 1.05, f"{dry['m50'].max():.0f} min x {wet['m50'].max():.0f} min")
    check('sem chuva nenhum salto de 10 min passa de 6%', (np.abs(np.diff(dry['p50'].to_numpy())) / dry['p50'].to_numpy()[:-1]).max() <= 0.06)

    levels = np.array([getSeries(route, getTs('2026-09-10 18:00'), mm)[['p10', 'p50', 'p90', 'm10', 'm50', 'm90']].to_numpy() for mm in (0, 1, 3, 6, 10)])
    check('mais chuva prevista nunca reduz nenhum quantil de preço ou tempo', bool((np.diff(levels, axis=0) >= 0).all()))

# PRECISAO FORA DA AMOSTRA POR HORIZONTE, EM PRECO E TEMPO, COM A ROTA RASTREADA E COM ROTA NOVA DE UMA OBSERVACAO SO
def testPrecision(routes):
    rng   = np.random.default_rng(21)
    start = getTs('2026-10-05')
    slots = np.arange(start, start + 8 * 86400, STEP)
    rain  = np.repeat(np.where(rng.random(len(slots) // 6 + 1) < 0.12, rng.exponential(3.0, len(slots) // 6 + 1), 0.0), 6)[:len(slots)]
    rows  = []

    for route in routes:
        prices, times = getMarket(route, slots, rain)
        day    = getClock(slots, route['tz'])[2]
        frame  = pd.DataFrame({'ts': slots, 'rain': rain, 'distance': route['distance'], 'duration': route['duration'], 'corridor': route['corridor'], 'tz': route['tz']})
        X      = model.process(frame, *model.getClock(frame)[:2])
        base   = {column: model.getQuantiles(model.boosters[column], X)[1] * model.SCALES[column](frame) for column in model.TARGETS}

        for a in range(204, len(slots) - LEADS, 18):
            anchor  = slots[a] + 180
            price, minutes = [value[0] for value in getMarket(route, [anchor], [rain[a]])]
            current = pd.DataFrame({'ts': [anchor], 'rain': [rain[a]], 'price': [price], 'minutes': [minutes]})
            same    = np.flatnonzero(day[:a] == day[a])
            tracked = pd.concat([pd.DataFrame({'ts': slots[same], 'rain': rain[same], 'price': prices[same], 'minutes': times[same]}), current], ignore_index=True)
            grid    = pd.DataFrame({'ts': np.append(anchor, slots[a + 1:a + LEADS]), 'rain': rain[a:a + LEADS]})
            truth   = {'price': np.append(price, prices[a + 1:a + LEADS]), 'minutes': np.append(minutes, times[a + 1:a + LEADS])}

            for column, prefix in model.TARGETS.items():
                rows.append(pd.DataFrame({'alvo': prefix, 'regime': '0 sem âncora', 'lead': np.arange(LEADS), 'erro': np.abs(base[column][a:a + LEADS] - truth[column]) / truth[column], 'cobertura': np.nan}))

            for regime, obs in (('1 rota rastreada', tracked), ('2 rota nova', current)):
                out = model.get(route, grid, obs)

                for column, prefix in model.TARGETS.items():
                    rows.append(pd.DataFrame({'alvo': prefix, 'regime': regime, 'lead': np.arange(LEADS), 'erro': np.abs(out[f'{prefix}50'] - truth[column]) / truth[column],
                                              'cobertura': ((truth[column] >= out[f'{prefix}10']) & (truth[column] <= out[f'{prefix}90'])).astype(float)}))

    res = pd.concat(rows)
    res['faixa'] = pd.cut(res['lead'], [-1, 0, 3, 6, 18, 36, 72], labels=['agora', '10-30min', '40-60min', '1-3h', '3-6h', '6-12h'])
    table = res.groupby(['alvo', 'regime', 'faixa'], observed=True)[['erro', 'cobertura']].mean().unstack('faixa')
    pd.set_option('display.width', 220)
    print(table.round(4).to_string())

    for prefix, name, limit in (('p', 'preço', 0.025), ('m', 'tempo', 0.045)):
        erro = table['erro'].loc[prefix]
        cobertura = table['cobertura'].loc[prefix].drop(columns='agora')
        check(f'{name}: observação de agora exata nos dois regimes', erro.loc['1 rota rastreada', 'agora'] == 0 and erro.loc['2 rota nova', 'agora'] == 0)
        check(f'{name}: rota rastreada nunca erra mais que sem âncora e ganha no curto prazo', (erro.loc['1 rota rastreada'].drop('agora') <= erro.loc['0 sem âncora'].drop('agora') + 1e-4).all() and erro.loc['1 rota rastreada', '10-30min'] < erro.loc['0 sem âncora', '10-30min'], f"{erro.loc['1 rota rastreada'].drop('agora').max():.2%} x {erro.loc['0 sem âncora'].drop('agora').max():.2%}")
        check(f'{name}: erro abaixo de {limit:.1%} em todos os horizontes e regimes', erro.drop(index='0 sem âncora').max().max() < limit, f"{erro.drop(index='0 sem âncora').max().max():.2%}")
        check(f'{name}: cobertura entre 76% e 86% em todos os horizontes', cobertura.loc[['1 rota rastreada', '2 rota nova']].min().min() > 0.76 and cobertura.loc[['1 rota rastreada', '2 rota nova']].max().max() < 0.86, f"{cobertura.min().min():.0%} a {cobertura.max().max():.0%}")

# ROTAS FORA DO TREINO, DE 2 A 300 KM, COM E SEM CORREDOR: SEM VIES DE DISTANCIA E COM FAIXA NA COBERTURA NOMINAL; COM AS 5 ROTAS ANTIGAS O TEMPO ERRAVA +2% E A FAIXA COBRIA 60%
def testGeneralization():
    rng   = np.random.default_rng(8)
    start = getTs('2026-10-19')
    slots = np.arange(start, start + 6 * 86400, STEP)
    rain  = np.repeat(np.where(rng.random(len(slots) // 6 + 1) < 0.12, rng.exponential(3.0, len(slots) // 6 + 1), 0.0), 6)[:len(slots)]
    day   = getClock(slots, TZ)[2]
    rows  = []

    for i, (km, speed, corridor) in enumerate([(2, 25, 0.0), (9, 40, 0.3), (30, 45, 0.0), (30, 45, 0.9), (100, 60, 0.0), (300, 70, 0.3)]):
        route = {'id': 900 + i, 'distance': km, 'duration': km / speed * 60, 'corridor': corridor, 'tz': TZ}
        prices, times = getMarket(route, slots, rain)

        for a in range(204, len(slots) - LEADS, 18):
            same  = np.flatnonzero(day[:a + 1] == day[a])
            out   = model.get(route, pd.DataFrame({'ts': slots[a:a + LEADS], 'rain': rain[a:a + LEADS]}), pd.DataFrame({'ts': slots[same], 'rain': rain[same], 'price': prices[same], 'minutes': times[same]}))
            truth = {'price': prices[a + 1:a + LEADS], 'minutes': times[a + 1:a + LEADS]}

            for column, prefix in model.TARGETS.items():
                rows.append(pd.DataFrame({'rota': f'{km} km, {corridor:.0%} no corredor', 'alvo': prefix, 'erro': out[f'{prefix}50'][1:].to_numpy() / truth[column] - 1, 'cobertura': ((truth[column] >= out[f'{prefix}10'][1:].to_numpy()) & (truth[column] <= out[f'{prefix}90'][1:].to_numpy())).astype(float)}))

    table = pd.concat(rows).groupby(['rota', 'alvo']).agg(erro=('erro', lambda e: e.abs().mean()), vies=('erro', 'mean'), cobertura=('cobertura', 'mean')).unstack('alvo')
    print(table.round(4).to_string())
    check('rotas fora do treino: erro abaixo de 3% no preço e de 5% no tempo', (table['erro']['p'] < 0.03).all() and (table['erro']['m'] < 0.05).all(), f"até {table['erro']['p'].max():.2%} e {table['erro']['m'].max():.2%}")
    check('rotas fora do treino: viés abaixo de 1,5% e faixa cobrindo entre 68% e 90%', (table['vies'].abs() < 0.015).all().all() and table['cobertura'].min().min() > 0.68 and table['cobertura'].max().max() < 0.90, f"viés até {table['vies'].abs().max().max():.2%}, cobertura de {table['cobertura'].min().min():.0%} a {table['cobertura'].max().max():.0%}")

# ACIDENTE NO CORREDOR: A ANCORA SEGUE O CHOQUE NA PRIMEIRA HORA E NAO CONTAMINA O NIVEL DO RESTO DO DIA (ANTES: VIES DE -6% NA PRIMEIRA HORA E +1,7% DEPOIS)
def testShock(routes):
    start = getTs('2026-11-02')
    slots = np.arange(start, start + 60 * 86400, STEP)
    rain  = np.zeros(len(slots))
    rows  = []

    for route in [r for r in routes if r['corridor'] > 0.55][:3]:
        prices, times = getMarket(route, slots, rain)
        day      = getClock(slots, route['tz'])[2]
        incident = getRandom(slots, route['id'], day, route['tz'])[0]

        for a in np.flatnonzero(incident > 0.2)[::2]:
            if a < 144 or a >= len(slots) - LEADS:
                continue

            same  = np.flatnonzero(day[:a + 1] == day[a])
            out   = model.get(route, pd.DataFrame({'ts': slots[a:a + LEADS], 'rain': rain[a:a + LEADS]}), pd.DataFrame({'ts': slots[same], 'rain': rain[same], 'price': prices[same], 'minutes': times[same]}))
            truth = {'price': prices[a + 1:a + LEADS], 'minutes': times[a + 1:a + LEADS]}

            for column, prefix in model.TARGETS.items():
                rows.append(pd.DataFrame({'alvo': prefix, 'lead': np.arange(1, LEADS), 'erro': out[f'{prefix}50'][1:].to_numpy() / truth[column] - 1, 'cobertura': ((truth[column] >= out[f'{prefix}10'][1:].to_numpy()) & (truth[column] <= out[f'{prefix}90'][1:].to_numpy())).astype(float)}))

    res   = pd.concat(rows)
    res['faixa'] = pd.cut(res['lead'], [0, 6, 18, 72], labels=['primeira hora', '1-3h', '3-12h'])
    table = res.groupby(['alvo', 'faixa'], observed=True).agg(erro=('erro', lambda e: e.abs().mean()), vies=('erro', 'mean'), cobertura=('cobertura', 'mean'), n=('erro', 'size'))
    print(table.round(4).to_string())
    check('acidente ativo: na primeira hora viés abaixo de 5% e faixa cobrindo ao menos 50%', (table.xs('primeira hora', level='faixa')['vies'].abs() < 0.05).all() and (table.xs('primeira hora', level='faixa')['cobertura'] >= 0.50).all(), table.xs('primeira hora', level='faixa')[['vies', 'cobertura']].round(3).to_dict('index'))
    check('acidente ativo: de 3 a 12 h o nível do dia não fica contaminado (viés abaixo de 1,2%)', (table.xs('3-12h', level='faixa')['vies'].abs() < 0.012).all(), table.xs('3-12h', level='faixa')['vies'].round(4).to_dict())

# QUALQUER ROTA DO PAIS: DISTANCIA, TEMPO, FUSO, SERIE COMPLETA E PRECO COERENTE COM A TARIFA
def testRoutes():
    rows = []

    for name, src, dst, zone in MATRIX:
        res = engine.getQuote(src, dst)

        if 'error' in res:
            check(f'{name}: consulta completa', False, res['error'])
            continue

        route, df = res['route'], res['df']
        tariff    = float(getTariff(route['distance'], route['duration']))
        speed     = route['distance'] / route['duration'] * 60
        straight  = getDistance(src, dst)
        steps     = np.diff(df['ts'].to_numpy())
        rows.append({'rota': name, 'km': route['distance'], 'livre': route['duration'], 'com trânsito': df['minutes'][0], 'km/h': speed, 'fuso': route['tz'], 'agora': df['price'][0]})
        check(f'{name}: fuso, velocidade, distância e série', route['tz'] == zone and 10 < speed < 110 and straight <= route['distance'] < 3 * straight and len(df) == LEADS and (steps[1:] == STEP).all(), f"{route['distance']:.1f} km, {speed:.0f} km/h, {route['tz']}")
        check(f'{name}: preço e tempo coerentes, com as duas faixas abertas', 0.9 * tariff <= df['price'][0] < 2.6 * tariff and 0.85 * route['duration'] <= df['minutes'][0] < 3 * route['duration'] and bool(((df['p10'] < df['p50']) & (df['p50'] < df['p90']) & (df['m10'] < df['m50']) & (df['m50'] < df['m90'])).all()), f"{getMoney(df['price'][0])} e {df['minutes'][0]:.0f} min (livre {route['duration']:.0f} min)")

    print(pd.DataFrame(rows).round(2).to_string(index=False))
    check('origem igual ao destino vira mensagem', 'error' in engine.getQuote(ORIGIN, ORIGIN))
    check('endereço inexistente vira mensagem', 'error' in engine.getQuote({'label': 'zzzzqqqqxxxxwwww'}, DESTINATION))
    check('ilha sem ligação rodoviária vira mensagem', 'error' in engine.getQuote({'label': 'Fernando de Noronha', 'lat': -3.8547, 'lon': -32.4247}, {'label': 'Recife', 'lat': -8.0476, 'lon': -34.8770}))

# CICLOS DO WORKER EM RELOGIO SIMULADO: OBSERVA, PREVE, CONSOLIDA, MEDE E RETREINA SEM EXCECAO
def testWorker():
    start    = (int(clock.time()) // STEP + 1) * STEP
    now      = [start]
    versions = []
    statuses = []
    Worker.index.time = Model.index.time = lambda: now[0]

    try:
        for i in range(13):
            now[0] = start + i * STEP + 5
            worker.handle()
            versions.append(model.version)
            statuses.append(worker.status)

        forecasts = database.get('SELECT COUNT(*) AS n FROM Forecasts')['n'][0]
        worker.handle()
        check('13 ciclos guardam 5 rotas x 72 previsões, sem duplicar no ciclo repetido', forecasts == 13 * 5 * (LEADS - 1) and database.get('SELECT COUNT(*) AS n FROM Forecasts')['n'][0] == forecasts, forecasts)
    finally:
        Worker.index.time = Model.index.time = clock.time

    check('previsões consolidadas contra preço e tempo observados', worker.metrics['n'] >= 300 and 0.4 <= worker.metrics['p']['coverage'] <= 1.0 and 0.4 <= worker.metrics['m']['coverage'] <= 1.0, {key: value for key, value in worker.metrics.items()})
    check('status só mostra a taxa de acerto da faixa com amostra suficiente', statuses[0].endswith(f'5 rotas monitoradas · consolidando previsões (0 de {worker.SAMPLE})') and '· últimas 24 h: faixa acerta' in statuses[-1], [statuses[0], statuses[-1]])
    changes = np.flatnonzero(np.diff(versions))
    check('retreino acontece e respeita o intervalo de 1 h', len(changes) >= 1 and bool(np.all(np.diff(changes) * STEP > worker.RETRAIN)), versions)
    check('uma linha de métricas por ciclo com previsão a consolidar', len(database.get('SELECT ts FROM Metrics')) == 12)

    worker.stop()
    worker.start()
    worker.wake()
    worker.stop()
    worker.stop()
    check('start/stop idempotentes e thread encerrada', not worker.thread.is_alive())

# JANELA REAL: CAMPOS VAZIOS, CARREGANDO, LOCALIZACAO, CONSULTA, JANELA DE 1 A 12 H, TEMPO REAL, ALERTAS E CRUZETA
def testInterface():
    from Interface import index as screen
    sounds = []
    screen.play = lambda kind: sounds.append(kind) or True
    app = screen.Interface()

    try:
        check('abre com origem e destino vazios e sem gráfico', app.origin.entry.get() == '' and app.destination.entry.get() == '' and app.chart.df is None and app.price.cget('text') == '—')

        search = api.search
        api.search = lambda text, limit=6: clock.sleep(1.2) or search(text, limit)
        app.origin.entry.insert(0, 'Praia do Pec')
        app.origin.handleSearch()
        app.update()
        check('lista mostra aviso de carregando antes das sugestões', app.origin.rows[0].cget('text').startswith('Buscando') and bool(app.origin.menu.winfo_ismapped()))
        found = wait(app, lambda: bool(app.origin.results), 25)
        api.search = search
        check('sugestões chegam e substituem o aviso', found and app.origin.rows[0].cget('text').startswith('Praia do Pecado'), [p['label'] for p in app.origin.results[:2]])
        app.origin.choose(0)

        app.getLocation(app.destination)
        opened  = wait(app, lambda: app.locator is not None and app.locator.winfo_exists(), 40)
        locator = app.locator
        check('botão de localização abre o mapa no ponto inicial, já com o endereço dele', opened and locator.marker == (locator.place['lat'], locator.place['lon']) and locator.address.cget('text') == locator.place['label'], locator.address.cget('text')[:60])
        check('projeção do mapa ida e volta sem perda', all(abs(value - back) < 1e-9 for zoom in (11, 15, 18) for value, back in zip((-22.3414, -41.7557), locator.getCoords(*locator.getPixel(-22.3414, -41.7557, zoom), zoom))))

        near, far = {'label': 'Praia do Pecado, Macaé', 'lat': -22.4114, 'lon': -41.8084}, {'label': 'Centro de São Paulo', 'lat': -23.5505, 'lon': -46.6333}
        coarse    = {'label': 'Rio das Ostras, Rio de Janeiro', 'lat': -22.5269, 'lon': -41.9450, 'accuracy': 25000}
        starts    = [(place['label'], zoom, mode) for place, zoom, mode in (locator.getStart({**near, 'accuracy': 8}, [far]), locator.getStart(coarse, [far, near]), locator.getStart(coarse, [far]), locator.getStart(None, [far, near]), locator.getStart(None, []))]
        check('mapa abre na estimativa precisa, no lugar usado dentro do erro da grosseira, no último lugar ou na região', starts == [('Praia do Pecado, Macaé', 16, 'estimativa'), ('Praia do Pecado, Macaé', 15, 'perto'), ('Rio das Ostras, Rio de Janeiro', 12, 'estimativa'), ('Centro de São Paulo', 15, 'lugar'), ('Macaé e Rio das Ostras', 12, 'regiao')], starts)

        Event = lambda x, y: type('Event', (), {'x': x, 'y': y})()
        center, marker = locator.center, locator.marker
        locator.handlePress(Event(330, 200))
        locator.handleDrag(Event(250, 150))
        locator.handleRelease(Event(250, 150))
        check('arrastar move o mapa sem mexer no ponto marcado', locator.center != center and locator.marker == marker)

        locator.handlePress(Event(120, 90))
        locator.handleRelease(Event(121, 91))
        check('clique marca o ponto na hora, com a coordenada até o endereço chegar', locator.marker != marker and (locator.place['lat'], locator.place['lon']) == locator.marker and locator.place['label'].startswith('-'), locator.place['label'])
        moved = wait(app, lambda: locator.address.cget('text') != 'buscando endereço…', 25)
        check('endereço do ponto marcado chega e continua valendo para o mesmo ponto', moved and (locator.place['lat'], locator.place['lon']) == locator.marker, locator.address.cget('text')[:60])

        locator.choose(locator.place)
        check('ponto escolhido preenche o campo e fecha o mapa', app.destination.selected is not None and not locator.winfo_exists(), app.destination.entry.get()[:50])

        app.showLocation(app.origin, None)
        fallback = app.locator
        check('sem estimativa o mapa abre mesmo assim, no último lugar usado ou na região', fallback.winfo_exists() and fallback.zoom in (12, 15) and fallback.address.cget('text') != '', fallback.address.cget('text')[:60])
        fallback.stop()

        app.destination.set({'label': 'Teatro Popular de Rio das Ostras', **DESTINATION})
        app.getForecast()
        ready = wait(app, lambda: app.chart.df is not None and not app.busy, 60)
        check('consulta renderiza preço, tempo com trânsito, resumo e as linhas de preço, chuva e trânsito', ready and app.price.cget('text').startswith('R$') and app.change.cget('text').startswith('•') and len(app.chart.figure.axes) == 4 and app.stats['distance'].cget('text').endswith('km'), f"{app.price.cget('text')} · {app.stats['distance'].cget('text')} · {app.message.cget('text')}")
        app.chart.canvas.draw()
        ticks   = [label.get_text() for label in app.chart.traffic.get_yticklabels() if label.get_text()]
        minutes = app.stats['minutes'].cget('text')
        check('tempo de viagem em minutos ou horas no eixo, no resumo e nas linhas de referência, sem porcentagem', bool(ticks) and all(text.endswith('min') or 'h' in text for text in ticks) and minutes.split(' · ')[1][0] in '+-' and '%' not in minutes + app.stats['worst'].cget('text') + app.stats['duration'].cget('text') and any(text.get_text().startswith('sem trânsito · ') for text in app.chart.traffic.texts), f"{ticks} · {minutes} · {app.stats['worst'].cget('text')}")
        check('gráfico sem caixas de legenda e com a chance de chuva hora a hora', all(ax.get_legend() is None for ax in app.chart.figure.axes) and 4 <= len(app.chart.chance.texts) <= 6 and app.stats['rain'].cget('text').endswith('%'), [text.get_text() for text in app.chart.chance.texts])

        app.after_cancel(app.jobs['sync'])
        app.handleTick()
        synced = app.syncing and wait(app, lambda: not app.syncing, 30)
        check('tempo real sempre ativo e sem botão: cada ciclo observa o preço de novo', synced and not hasattr(app, 'toggle') and app.change.cget('text').startswith(('•', '▼', '▲')), app.change.cget('text'))

        app.chart.handleWindow(1)
        app.update()
        short = len(app.chart.df)
        app.chart.handleWindow(12)
        app.update()
        check('janela de 1 h a 12 h recorta a série sem recalcular', 6 <= short <= 7 and len(app.chart.df) == LEADS and app.route is not None, f'{short} e {len(app.chart.df)} pontos')

        route = {'id': -1}
        app.baselines[-1], app.levels[-1] = (100.0, '10:00'), 0

        for price in (95, 89, 85, 79, 88, 105, 111, 125):
            app.check(route, price)

        check('alertas a cada 10% de afastamento, sem repetir no mesmo patamar', sounds == ['good', 'good', 'bad', 'bad'], sounds)

        app.chart.handleMotion(type('Event', (), {'inaxes': app.chart.price, 'xdata': app.chart.x[12]})())
        check('cruzeta e resumo aparecem no hover, com o tempo de viagem e a hora de chegada', app.chart.tip.get_visible() and 'chegada às' in app.chart.tip.get_text() and '%   trânsito' not in app.chart.tip.get_text(), app.chart.tip.get_text().replace(chr(10), ' | '))
    finally:
        app.stop()


if __name__ == '__main__':
    logging.basicConfig(level=logging.WARNING, format='%(asctime)s [%(threadName)s] %(levelname)s %(message)s')

    try:
        testUtils()
        testApi()
        testDistance()
        routes = testBootstrap()
        testOracle(routes[0])
        testScenarios(routes[0])
        testPrecision(routes)
        testGeneralization()
        testShock(routes)
        testRoutes()
        testWorker()
        testInterface()
    finally:
        shutil.rmtree(FOLDER, ignore_errors=True)

    print(f'\n{len(failures)} falha(s): {failures}' if failures else '\ntodas as verificações passaram')
    raise SystemExit(1 if failures else 0)
