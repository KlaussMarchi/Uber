import os, json, math, shutil, tempfile, logging
import time as clock
import numpy as np
import pandas as pd
import Worker.index
from datetime import date, timedelta
from Api.index import api
from Database.index import database
from Engine.index import engine
from Market.index import WEEK, getFrame, getTable, getWeek
import Oracle.index as oracle
from Oracle.index import TARIFFS, getCalibration, getFree, getOrigin, getState, getSurge, getTariff
from Model.index import getNormal, getQuantile
from Utils.functions import getClock, getDelay, getDistance, getDuration, getHolidays, getMoney, getSpan
from Utils.sound import getTone
from Utils.variables import COMPANIES, ORIGIN, DESTINATION, TZ, STEP, HORIZON, MARKET_PATH, ROUTES_PATH
from Worker.index import worker


FOLDER   = tempfile.mkdtemp(prefix='tarifa-')    # banco isolado dos dados do app
LEADS    = HORIZON // STEP + 1
Z90      = 1.2815515655446004                     # quantil 90% da normal padrao
failures = []

# rota de referencia do enunciado (macae -> rio das ostras) com o municipio de origem que o nominatim devolve
REFERENCE = {'id': 1, 'o_lat': -22.34137, 'o_lon': -41.75567, 'distance': 34.0809, 'duration': 42.7283, 'tz': TZ, 'city': 'Macaé', 'uf': 'RJ'}

# rotas reais de varias cidades e fusos, com o municipio de origem esperado
MATRIX = [
    ('Copacabana → Ipanema (RJ)',   {'label': 'Copacabana', 'lat': -22.9711, 'lon': -43.1822}, {'label': 'Ipanema', 'lat': -22.9838, 'lon': -43.2096}, 'America/Sao_Paulo', 'Rio de Janeiro/RJ'),
    ('Paulista → Ibirapuera (SP)',  {'label': 'Avenida Paulista', 'lat': -23.5614, 'lon': -46.6559}, {'label': 'Ibirapuera', 'lat': -23.5874, 'lon': -46.6576}, 'America/Sao_Paulo', 'São Paulo/SP'),
    ('Ilha do Governador → Xerém',  {'label': 'Moneró, Rio de Janeiro', 'lat': -22.79659, 'lon': -43.19412}, {'label': 'Xerém, Duque de Caxias', 'lat': -22.6557, 'lon': -43.30262}, 'America/Sao_Paulo', 'Rio de Janeiro/RJ'),
    ('Rio → Niterói (ponte)',       {'label': 'Centro do Rio', 'lat': -22.9035, 'lon': -43.2096}, {'label': 'Centro de Niterói', 'lat': -22.8940, 'lon': -43.1150}, 'America/Sao_Paulo', 'Rio de Janeiro/RJ'),
    ('Rio → São Paulo (430 km)',    {'label': 'Centro do Rio', 'lat': -22.9068, 'lon': -43.1729}, {'label': 'Centro de São Paulo', 'lat': -23.5505, 'lon': -46.6333}, 'America/Sao_Paulo', 'Rio de Janeiro/RJ'),
    ('Manaus centro → aeroporto',   {'label': 'Centro de Manaus', 'lat': -3.1316, 'lon': -60.0233}, {'label': 'Aeroporto de Manaus', 'lat': -3.0386, 'lon': -60.0497}, 'America/Manaus', 'média nacional'),    # a amostra nao tem trecho do amazonas
]


def check(name, ok, detail=''):
    print(f"[{'ok' if ok else 'FALHOU'}] {name} {detail}")

    if not ok:
        failures.append(name)

# TIMESTAMP UNIX DE UMA DATA NO FUSO INFORMADO
def getTs(text, tz=TZ):
    return int(pd.Timestamp(text, tz=tz).timestamp())

# PREVISAO FORCADA COM CHUVA E CHANCE CONSTANTES, PARA TESTAR CENARIOS SEM DEPENDER DO TEMPO REAL
def getWeather(start, mm, chance=None):
    ts = np.arange(start - 86400, start + 2 * 86400, 3600)
    return pd.DataFrame({'ts': ts, 'rain': np.full(len(ts), mm), 'probability': np.full(len(ts), (90.0 if mm else 0.0) if chance is None else chance)})

# SERIE DO ENGINE PARA UMA ROTA EM UM INSTANTE, CHUVA E CHANCE FORCADOS
def getSeries(route, ts, mm=0.0, company='uber', chance=None):
    engine.getWeather = lambda lat, lon: getWeather(ts, mm, chance)

    try:
        return engine.get(route, company, ts)
    finally:
        del engine.getWeather

# PRECO INFORMADO NUM INSTANTE, COM O CLIMA FORCADO
def setFare(route, company, observed, ts, mm=0.0):
    engine.getWeather = lambda lat, lon: getWeather(ts, mm)

    try:
        return engine.setFare(route, company, observed, ts)
    finally:
        del engine.getWeather

# PRECO MEDIO DO MES QUE O APP DA A UM TRECHO PUBLICADO PELA UBER: A SEMANA INTEIRA EM SLOTS DE 10 MIN, PONDERADA PELO VOLUME DE VIAGENS, NA ROTA QUE O OSRM TRACARIA
def getMonthly(row):
    route  = {'o_lat': row.lat, 'o_lon': row.lon, 'city': row.src, 'uf': row.uf, 'distance': row.km, 'duration': row.o_min * row.km / row.o_km, 'tz': TZ}
    ts     = getTs('2026-09-14') + WEEK
    excess, _, minutes = getState(route, ts, np.zeros(len(ts)))
    return float(getTariff(getOrigin(route)['level'], route['distance'], minutes, excess) @ getWeek()[0]), float(minutes @ getWeek()[0])

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

# SERVICOS ABERTOS: AUTOCOMPLETE, GEOCODIFICACAO, MUNICIPIO, FUSO, LOCALIZACAO E FALHA DE REDE SEM EXCECAO
def testApi():
    places = api.search('Rua Professor Antônio Ava')
    check('autocomplete casa o prefixo da rua', bool(places) and 'Avarez Parada' in places[0]['label'], places[0]['label'] if places else places)
    check('autocomplete só devolve lugares no Brasil', all(-34 < p['lat'] < 6 and -74 < p['lon'] < -34 for p in api.search('Teatro Mun') or [{'lat': 99, 'lon': 0}]))
    check('texto livre é geocodificado', api.geocode('Praia de Costazul, Rio das Ostras') is not None)
    check('fusos por coordenada', (api.getZone(-22.34, -41.75), api.getZone(-3.13, -60.02), api.getZone(-9.97, -67.81)) == ('America/Sao_Paulo', 'America/Manaus', 'America/Rio_Branco'))

    cities = [api.getCity(lat, lon) for lat, lon in ((-22.79659, -43.19412), (-22.6557, -43.30262), (-22.40, -42.10), (-23.5614, -46.6559))]
    check('município de origem pela fronteira do OSM: ilha no Rio, distrito e zona rural no município certo', [f"{c['city']}/{c['uf']}" if c else None for c in cities] == ['Rio de Janeiro/RJ', 'Duque de Caxias/RJ', 'Macaé/RJ', 'São Paulo/SP'], cities)
    check('ponto no mar não tem município', api.getCity(-23.5, -40.0) is None)

    address, beacon = api.getAddress(), api.getBeacon()
    place = api.locate()
    check('posição por IP: mediana dos provedores, dentro do Brasil e com a dispersão entre eles declarada', address is not None and -34 < address['lat'] < 6 and -74 < address['lon'] < -34 and address['accuracy'] >= api.IP_ERROR, address)
    check('localização atual escolhe a fonte mais precisa entre Wi-Fi e IP', place is not None and place['accuracy'] == min(item['accuracy'] for item in (address, beacon) if item is not None), f"{place['label']} ±{place['accuracy'] / 1000:.1f} km" if place else None)
    api.ADDRESSES['https://servidor.invalido/'] = lambda res: (res.get('latitude'), res.get('longitude'))
    broken = api.getAddress()
    del api.ADDRESSES['https://servidor.invalido/']
    check('provedor fora do ar é descartado sem mudar a posição por IP', broken is not None and abs(broken['lat'] - address['lat']) < 1e-9, broken)

    weather = api.getWeather(-22.43, -41.85, past_days=1, forecast_days=2)
    check('previsão horária do Open-Meteo cobre o horizonte com chance de chuva', weather is not None and weather['ts'].max() - clock.time() > HORIZON and weather['probability'].between(0, 100).all(), None if weather is None else f'{len(weather)} horas')
    check('servidor inexistente devolve None', api.get('https://servidor.invalido/', {}) is None)

# DISTANCIA E TEMPO: OSRM CONFERIDO CONTRA LINHA RETA, CONTRA OUTRO SERVIDOR E CONTRA PONTOS SEM ACESSO DE CARRO
def testDistance():
    route    = api.getRoute(ORIGIN, DESTINATION)
    straight = getDistance(ORIGIN, DESTINATION)
    check('rota de referência: 34 km e 43 min sem trânsito', route is not None and abs(route['distance'] - 34.08) < 1 and abs(route['duration'] - 42.7) < 3, route)
    check('distância rodoviária maior que a linha reta e no máximo o triplo', straight < route['distance'] < 3 * straight, f"reta {straight:.1f} km, estrada {route['distance']:.1f} km")

    other = api.get('https://routing.openstreetmap.de/routed-car/route/v1/driving/' + f"{ORIGIN['lon']},{ORIGIN['lat']};{DESTINATION['lon']},{DESTINATION['lat']}", {'overview': 'false'})
    check('segundo servidor OSRM confirma distância e tempo', other is not None and abs(other['routes'][0]['distance'] / 1000 - route['distance']) < 0.5 and abs(other['routes'][0]['duration'] / 60 - route['duration']) < 2)
    check('ponto no mar e ilha sem estrada não viram rota', api.getRoute({'lat': -3.8547, 'lon': -32.4247}, {'lat': -8.0476, 'lon': -34.8770}) is None and api.getRoute({'lat': -22.60, 'lon': -41.60}, {'lat': -22.37, 'lon': -41.78}) is None)

    primary  = api.OSRM
    api.OSRM = ('https://servidor.invalido/route/v1/driving/', primary[1])
    fallback = api.getRoute(ORIGIN, DESTINATION)
    api.OSRM = primary
    check('OSRM principal fora do ar cai para o espelho da FOSSGIS com a mesma rota', fallback is not None and abs(fallback['distance'] - route['distance']) < 0.5, fallback)

# TABELA CONTRA A REALIDADE: VALIDACAO CRUZADA NAS MEDIAS REAIS DA UBERX, TABELA REPRODUZIVEL E A CADEIA INTEIRA DO APP REPRODUZINDO AS MEDIAS
def testMarket():
    df                 = getFrame(ROUTES_PATH)
    market, validation = getTable(df)
    saved              = json.load(open(MARKET_PATH, encoding='utf-8'))
    same               = {key: value for key, value in market.items() if key != 'updated'} == {key: value for key, value in saved.items() if key != 'updated'}
    check(f"tabela do app reproduzida a partir dos {len(df)} trechos reais guardados", same, f"{df['src_key'].nunique()} municípios em {df['uf'].nunique()} estados")

    old     = (1 + np.maximum(7.5, 2.5 + 1.2076 * df['km'] + 0.22 * df['min'])) / df['uberx'] - 1
    route   = np.exp(validation['rota']['price']) - 1
    city    = np.exp(validation['cidade']['price']) - 1
    pace    = np.exp(np.abs(validation['rota']['pace'][df['match']])) - 1
    inside  = np.abs(validation['rota']['price']) <= Z90 * market['sigma']['city']
    check('tabela antiga do app ficava muito abaixo da Uber real', old.median() < -0.15, f'viés mediano {old.median():+.1%}, erro mediano {old.abs().median():.1%}')
    check('rota nova num município conhecido: erro mediano abaixo de 8% e sem viés', route.abs().median() < 0.08 and abs(route.median()) < 0.02, f'erro {route.abs().median():.1%}, viés {route.median():+.1%}, p90 {route.abs().quantile(0.9):.1%}')
    check('município sem trecho publicado (só o estado): erro mediano abaixo de 12%', city.abs().median() < 0.12 and abs(city.median()) < 0.04, f'erro {city.abs().median():.1%}, viés {city.median():+.1%}')
    check('tempo médio real de viagem previsto pelo OSRM com erro mediano abaixo de 10%', pace.median() < 0.10, f'erro {pace.median():.1%} em {int(df["match"].sum())} trechos')
    check('faixa do nível regional cobre 80% das médias reais fora do ajuste', 0.72 <= inside.mean() <= 0.88, f'{inside.mean():.0%}')

    oracle.load()
    rows    = df[df['o_km'].notna()]
    monthly = np.array([getMonthly(row) for row in rows.itertuples()])
    error   = monthly[:, 0] / rows['uberx'].to_numpy() - 1
    minutes = monthly[:, 1] / rows['min'].to_numpy() - 1
    check('cadeia do app (município, ritmo, perfil horário e tarifa) reproduz a média real do mês', np.median(np.abs(error)) < 0.07 and abs(np.median(error)) < 0.03, f'erro {np.median(np.abs(error)):.1%}, viés {np.median(error):+.1%} no preço; erro {np.median(np.abs(minutes)):.1%} no tempo')
    return {'antiga': old, 'rota': route, 'cidade': city, 'app': error, 'tempo': minutes, 'uf': df['uf']}

# MERCADO ESPERADO: PICO MAIS CARO E LENTO QUE A MADRUGADA, CHUVA NUNCA BARATEIA, FERIADO COMO DOMINGO, 99 ABAIXO DA UBER E O MESMO TEMPO NOS DOIS
def testOracle():
    route = REFERENCE
    level = getOrigin(route)['level']
    price = lambda ts, mm=0.0, company='uber': float(getTariff(level, route['distance'], getState(route, [ts], [mm])[2], getState(route, [ts], [mm])[0], company)[0])
    time  = lambda ts, mm=0.0: float(getState(route, [ts], [mm])[2][0])

    check('município de origem conhecido usa o nível dele', getOrigin(route)['market'] == 'Macaé/RJ' and getOrigin({**route, 'city': '', 'uf': ''})['market'].startswith('Macaé/RJ e arredores'), getOrigin({**route, 'city': 'Carapebus', 'uf': 'RJ'})['market'])
    check('sexta 18 h mais cara e mais lenta que a madrugada', price(getTs('2026-09-11 18:00')) > 1.25 * price(getTs('2026-09-10 03:00')) and time(getTs('2026-09-11 18:00')) > 1.2 * time(getTs('2026-09-10 03:00')), f"{getMoney(price(getTs('2026-09-11 18:00')))} x {getMoney(price(getTs('2026-09-10 03:00')))}")
    check('feriado de manhã custa menos que segunda comum', price(getTs('2026-09-07 07:30')) < 0.9 * price(getTs('2026-09-14 07:30')), f"{getMoney(price(getTs('2026-09-07 07:30')))} x {getMoney(price(getTs('2026-09-14 07:30')))}")

    rains = [(price(getTs('2026-09-09 10:30'), mm), time(getTs('2026-09-09 10:30'), mm)) for mm in (0, 1, 3, 6, 10, 30)]
    check('mais chuva nunca barateia nem acelera', bool(np.all(np.diff(rains, axis=0) >= 0)), [round(p, 2) for p, _ in rains])
    check('sem chuva a madrugada fica no tempo sem trânsito', abs(time(getTs('2026-09-10 03:30')) / getFree(route) - 1) < 0.02, f"{time(getTs('2026-09-10 03:30')):.1f} x {getFree(route):.1f} min")

    ts     = np.arange(getTs('2026-09-14'), getTs('2026-09-21'), STEP)
    excess, _, minutes = getState(route, ts, np.zeros(len(ts)))
    prices = {company: getTariff(level, route['distance'], minutes, excess, company) for company in COMPANIES}
    ratio  = prices['99'] / prices['uber']
    check('99 abaixo da Uber em todo instante, na razão típica das comparações públicas', bool((ratio < 1).all()) and abs(ratio.mean() - TARIFFS['99']['ratio']) < 0.01, f'{ratio.min():.3f} a {ratio.max():.3f}')
    check('dinâmica entre 1 e o teto do aplicativo', bool((getSurge(excess) >= 1).all() and (getSurge(np.full(3, 9.0)) == TARIFFS['uber']['cap']).all()))
    check('mesmo instante, mesmo preço e mesmo tempo', np.array_equal(getState(route, ts[:300], np.zeros(300))[2], getState(route, ts[:300], np.zeros(300))[2]))

    gap = np.arange(getTs('2018-11-03 12:00'), getTs('2018-11-05 12:00'), STEP)
    check('dia sem meia-noite (horário de verão de 2018) não quebra o mercado', len(getState(route, gap, np.zeros(len(gap)))[2]) == len(gap))

# FAIXAS: ORDENADAS, MISTURA DE CHUVA CORRETA E MONOTONA, PRECO INFORMADO EXATO NA HORA, DINAMICA QUE SE DISSIPA, NIVEL QUE FICA E LIMITES CONTRA ERRO DE DIGITACAO
def testModel():
    z = np.linspace(-6, 6, 241)
    check('normal acumulada com erro abaixo de 2e-7', float(np.max(np.abs(getNormal(z) - [0.5 * math.erfc(-v / math.sqrt(2)) for v in z]))) < 2e-7)
    q = getQuantile(0.9, np.array([0.0, 0.4]), [np.array([0.0, 0.0]), np.array([0.0, 3.0])], [np.array([1.0, 1.0]), np.array([1.0, 1.0])])
    check('quantil da mistura: sem chuva é o da normal, com chuva sobe', abs(q[0] - Z90) < 1e-5 and q[1] > Z90 + 1, q.round(4))

    start = getTs('2026-09-11 16:00')
    dry   = getSeries(REFERENCE, start, 0.0)
    wet   = getSeries(REFERENCE, start, 8.0)
    check('série de 73 pontos com p10 < p50 < p90 e m10 < m50 < m90', len(dry) == LEADS and bool(((dry['p10'] < dry['p50']) & (dry['p50'] < dry['p90']) & (dry['m10'] < dry['m50']) & (dry['m50'] < dry['m90'])).all()))
    check('chuva forte encarece o pico e alonga a viagem', wet['p50'].max() > 1.15 * dry['p50'].max() and wet['m50'].max() > 1.1 * dry['m50'].max(), f"{getMoney(dry['p50'].max())} x {getMoney(wet['p50'].max())}")
    check('sem chuva nenhum salto de 10 min passa de 6%', float((np.abs(np.diff(dry['p50'].to_numpy())) / dry['p50'].to_numpy()[:-1]).max()) <= 0.06)
    blank = getSeries(REFERENCE, start, 0.3, chance=np.nan)
    check('sem chance de chuva publicada as faixas continuam válidas e ordenadas', bool(np.isfinite(blank[['p10', 'p50', 'p90', 'm10', 'm50', 'm90']].to_numpy()).all() and (blank['p10'] < blank['p50']).all() and (blank['p50'] < blank['p90']).all()))

    for company in COMPANIES:
        levels = np.array([getSeries(REFERENCE, getTs('2026-09-10 18:00'), mm, company, chance)[['p10', 'p50', 'p90', 'm10', 'm50', 'm90']].to_numpy() for mm, chance in ((0, 0), (0, 30), (0.5, 60), (2, 80), (6, 95), (12, 100))])
        check(f'mais chuva ou mais chance de chuva nunca reduz nenhum quantil ({COMPANIES[company]})', bool((np.diff(levels, axis=0) >= -0.005).all()))

    database.set('DELETE FROM Fares', [()])
    engine.update()
    now     = getTs('2026-09-10 17:00')
    base    = getSeries(REFERENCE, now)
    other   = getSeries(REFERENCE, now + 30, company='99')
    target  = round(float(base['p50'][0]) * 1.25, 2)
    setFare(REFERENCE, 'uber', target, now)
    tuned   = getSeries(REFERENCE, now + 30)
    later   = getSeries(REFERENCE, now + 3 * 3600)
    cal     = getCalibration(REFERENCE, 'uber', now + 3 * 3600)
    check('preço informado aparece exato na hora, com a faixa estreita', tuned['p50'][0] == target and tuned['p90'][0] - tuned['p10'][0] < 0.25 * (base['p90'][0] - base['p10'][0]), f"pedido {getMoney(target)}, mostra {getMoney(tuned['p50'][0])}")
    check('a dinâmica do preço informado se dissipa e o nível aprendido fica', 1.05 < later['p50'][0] / base['p50'][18] < 1.2 and later['p90'][0] - later['p10'][0] < base['p90'][18] - base['p10'][18], f"{later['p50'][0] / base['p50'][18]:.3f} três horas depois, nível {math.exp(cal['mean']):.3f}")
    check('o preço informado de um aplicativo não mexe no outro', getSeries(REFERENCE, now + 30, company='99')['p50'].equals(other['p50']) and getCalibration(REFERENCE, '99', now)['count'] == 0)

    far = {**REFERENCE, 'o_lat': -23.55, 'o_lon': -46.63, 'city': 'São Paulo', 'uf': 'SP'}
    check('preço informado a mais de 300 km quase não mexe no nível de outra origem', abs(getCalibration(far, 'uber', now)['mean']) < 0.005, getCalibration(far, 'uber', now)['mean'])

    setFare(REFERENCE, 'uber', target * 10, now + 60)
    check('preço absurdo fica preso no dobro do esperado', getSeries(REFERENCE, now + 90)['p50'][0] <= 2.01 * base['p50'][0], getMoney(getSeries(REFERENCE, now + 90)['p50'][0]))

    for i in range(25):
        setFare(REFERENCE, 'uber', round(float(base['p50'][0]) * 1.10, 2), now + 120 + i)

    check(f'só os {oracle.SAMPLE} preços mais recentes contam e muitos preços convergem o nível', len(oracle.FARES) == oracle.SAMPLE and abs(math.exp(getCalibration(REFERENCE, 'uber', now + 200)['mean']) - 1.10) < 0.02, f"nível {math.exp(getCalibration(REFERENCE, 'uber', now + 200)['mean']):.3f}")
    check('zero apaga os preços do aplicativo e volta à média real', setFare(REFERENCE, 'uber', 0, now) == 0 and getSeries(REFERENCE, now)['p50'][0] == base['p50'][0])

# QUALQUER ROTA DO PAIS, NOS DOIS APLICATIVOS: MUNICIPIO, FUSO, SERIE COMPLETA E PRECO COERENTE COM A MEDIA REAL DA REGIAO
def testRoutes():
    rows = []

    for name, src, dst, zone, market in MATRIX:
        quotes = {company: engine.getQuote(src, dst, company) for company in COMPANIES}

        if any('error' in res for res in quotes.values()):
            check(f'{name}: consulta completa', False, [res['error'] for res in quotes.values() if 'error' in res][0])
            continue

        route = quotes['uber']['route']
        speed = route['distance'] / route['duration'] * 60
        steps = np.diff(quotes['uber']['df']['ts'].to_numpy())
        found = getOrigin(route)['market']
        rows.append({'rota': name, 'km': route['distance'], 'sem trânsito': getFree(route), 'agora': quotes['uber']['df']['minutes'][0], 'município': found, **{COMPANIES[company]: res['df']['price'][0] for company, res in quotes.items()}})
        check(f'{name}: município, fuso, velocidade e série', found == market and route['tz'] == zone and 10 < speed < 130 and len(quotes['uber']['df']) == LEADS and (steps[1:] == STEP).all(), f"{found}, {route['distance']:.1f} km, {speed:.0f} km/h, {route['tz']}")

        for company, res in quotes.items():
            df = res['df']
            check(f'{name}, {COMPANIES[company]}: faixas abertas e preço por km plausível', 1.2 <= df['price'][0] / route['distance'] <= 6 or route['distance'] < 3, f"{getMoney(df['price'][0])} ({df['price'][0] / route['distance']:.2f}/km), {df['minutes'][0]:.0f} min")
            check(f'{name}, {COMPANIES[company]}: p10 < p50 < p90 e m10 < m50 < m90 na série inteira', bool(((df['p10'] < df['p50']) & (df['p50'] < df['p90']) & (df['m10'] < df['m50']) & (df['m50'] < df['m90'])).all()))

        check(f'{name}: 99 mais barata que a Uber em toda a série e com o mesmo tempo de viagem', bool((quotes['99']['df']['p50'] < quotes['uber']['df']['p50']).all()) and quotes['99']['df']['m50'].equals(quotes['uber']['df']['m50']))

    print(pd.DataFrame(rows).round(2).to_string(index=False))
    check('origem igual ao destino vira mensagem', 'error' in engine.getQuote(ORIGIN, ORIGIN))
    check('endereço inexistente vira mensagem', 'error' in engine.getQuote({'label': 'zzzzqqqqxxxxwwww'}, DESTINATION))
    check('ilha sem ligação rodoviária vira mensagem', 'error' in engine.getQuote({'label': 'Fernando de Noronha', 'lat': -3.8547, 'lon': -32.4247}, {'label': 'Recife', 'lat': -8.0476, 'lon': -34.8770}))

# CICLOS DO WORKER EM RELOGIO SIMULADO: GUARDA A PREVISAO DAS ROTAS MONITORADAS E MEDE O ACERTO CONTRA PRECOS INFORMADOS DEPOIS
def testWorker():
    start  = (int(clock.time()) // STEP + 1) * STEP
    now    = [start]
    routes = database.get('SELECT * FROM Routes WHERE used_at > 0 ORDER BY used_at DESC LIMIT ?', (worker.TRACKED,)).to_dict('records')
    Worker.index.time = lambda: now[0]

    try:
        for i in range(7):
            now[0] = start + i * STEP + 5
            worker.handle()

        forecasts = database.get('SELECT COUNT(*) AS n FROM Forecasts')['n'][0]
        worker.handle()
        check(f'7 ciclos guardam {len(routes)} rotas x 2 aplicativos x 72 previsões, sem duplicar no ciclo repetido', forecasts == 7 * len(routes) * len(COMPANIES) * (LEADS - 1) and database.get('SELECT COUNT(*) AS n FROM Forecasts')['n'][0] == forecasts, forecasts)
        before = worker.status

        for route in routes[:3]:
            shown = engine.get(route, 'uber', now[0] + 60)
            engine.setFare(route, 'uber', round(float(shown['p50'][0]) * 1.05, 2), now[0] + 60)

        now[0] += STEP
        worker.handle()
    finally:
        Worker.index.time = clock.time

    check('status pede o preço real antes de ter com o que comparar e mede o acerto depois', 'informe o preço' in before and 'as previsões feitas antes erraram' in worker.status and worker.metrics['matched'] == 3, [before, worker.status])
    check('acerto medido com erro de ~5% e a faixa cobrindo o preço informado', 0.02 < worker.metrics['mape'] < 0.08 and worker.metrics['coverage'] > 0.8, {key: round(value, 3) for key, value in worker.metrics.items()})
    engine.setFare(routes[0], 'uber', 0)

    worker.stop()
    worker.start()
    worker.wake()
    worker.stop()
    worker.stop()
    check('start/stop idempotentes e thread encerrada', not worker.thread.is_alive())

# JANELA REAL: CAMPOS VAZIOS, CARREGANDO, LOCALIZACAO, CONSULTA, JANELA DE 1 A 12 H, TEMPO REAL, PRECO INFORMADO, ALERTAS E CRUZETA
def testInterface():
    from Interface import index as screen
    sounds = []
    screen.play = lambda kind: sounds.append(kind) or True
    app = screen.Interface()

    try:
        check('abre com origem e destino vazios, na Uber e sem gráfico', app.origin.entry.get() == '' and app.destination.entry.get() == '' and app.chart.df is None and app.price.cget('text') == '—' and app.selector.get() == 'Uber' and 'médias reais' in app.hint.cget('text'), app.hint.cget('text'))

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

        app.origin.set({'label': 'Rua Professor Antônio Álvares 92, Macaé', **{key: ORIGIN[key] for key in ('lat', 'lon')}})
        app.destination.set({'label': 'Teatro Popular de Rio das Ostras', **DESTINATION})
        app.getForecast()
        ready = wait(app, lambda: app.chart.df is not None and not app.busy, 60)
        check('consulta renderiza preço, dinâmica, tempo com trânsito, resumo e as linhas de preço, chuva e trânsito', ready and app.price.cget('text').startswith('R$') and app.change.cget('text').startswith('•') and len(app.chart.figure.axes) == 4 and app.stats['distance'].cget('text').endswith('km') and app.stats['surge'].cget('text').endswith('×'), f"{app.price.cget('text')} · {app.stats['surge'].cget('text')} · {app.message.cget('text')}")
        check('origem da tarifa diz o município e que ainda não há preço seu', 'Macaé/RJ' in app.hint.cget('text') and 'sem preço seu' in app.hint.cget('text'), app.hint.cget('text'))
        app.chart.canvas.draw()
        ticks   = [label.get_text() for label in app.chart.traffic.get_yticklabels() if label.get_text()]
        minutes = app.stats['minutes'].cget('text')
        check('tempo de viagem em minutos ou horas no eixo, no resumo e nas linhas de referência, sem porcentagem', bool(ticks) and all(text.endswith('min') or 'h' in text for text in ticks) and minutes.split(' · ')[1][0] in '+-' and '%' not in minutes + app.stats['worst'].cget('text') + app.stats['duration'].cget('text') and any(text.get_text().startswith('sem trânsito · ') for text in app.chart.traffic.texts), f"{ticks} · {minutes} · {app.stats['worst'].cget('text')}")
        check('gráfico sem caixas de legenda e com a chance de chuva hora a hora', all(ax.get_legend() is None for ax in app.chart.figure.axes) and 4 <= len(app.chart.chance.texts) <= 6 and app.stats['rain'].cget('text').endswith('%'), [text.get_text() for text in app.chart.chance.texts])

        uber = app.price.cget('text')
        app.selector.set('99')
        app.handleCompany('99')
        switched = wait(app, lambda: app.price.cget('text') != uber, 30)
        check('seletor troca o aplicativo e recalcula preço e gráfico com a tarifa dele', switched and app.company == '99' and app.chart.price.get_title('left').endswith('· 99') and '× 0,90' in app.hint.cget('text'), f"{uber} (Uber) -> {app.price.cget('text')} (99) · {app.hint.cget('text')}")

        shown  = lambda: float(app.price.cget('text').replace('R$ ', '').replace('.', '').replace(',', '.')) if app.price.cget('text').startswith('R$') else 0.0
        target = round(shown() * 1.12, 2)
        app.fare.insert(0, f'{target:.2f}'.replace('.', ','))
        app.handleFare()
        tuned = wait(app, lambda: 'registrado' in app.message.cget('text') and abs(shown() - target) < 0.005, 30)
        check('preço real informado aparece exato e a origem da tarifa conta ele', tuned and 'ajustada a 1 preço' in app.hint.cget('text'), f"{app.hint.cget('text')} · pedido {getMoney(target)}, mostra {app.price.cget('text')}")

        app.fare.insert(0, '0')
        app.handleFare()
        cleared = wait(app, lambda: 'apagados' in app.message.cget('text'), 30)
        check('zero apaga os preços informados e volta à média real da região', cleared and 'sem preço seu' in app.hint.cget('text'), app.hint.cget('text'))
        app.selector.set('Uber')
        app.handleCompany('Uber')
        wait(app, lambda: not app.syncing, 30)

        app.after_cancel(app.jobs['sync'])
        app.handleTick()
        synced = app.syncing and wait(app, lambda: not app.syncing, 30)
        check('tempo real sempre ativo e sem botão: cada ciclo recalcula o preço', synced and not hasattr(app, 'toggle') and app.change.cget('text').startswith(('•', '▼', '▲')), app.change.cget('text'))

        app.chart.handleWindow(1)
        app.update()
        short = len(app.chart.df)
        app.chart.handleWindow(12)
        app.update()
        check('janela de 1 h a 12 h recorta a série sem recalcular', 6 <= short <= 7 and len(app.chart.df) == LEADS and app.route is not None, f'{short} e {len(app.chart.df)} pontos')

        route = {'id': -1}
        sounds.clear()    # alertas legitimos da rota real durante o teste nao entram na sequencia conferida aqui
        app.baselines[(-1, app.company)], app.levels[(-1, app.company)] = (100.0, '10:00'), 0

        for price in (95, 89, 85, 79, 88, 105, 111, 125):
            app.check(route, app.company, price)

        check('alertas a cada 10% de afastamento, sem repetir no mesmo patamar', sounds == ['good', 'good', 'bad', 'bad'], sounds)

        shown = app.df
        app.showForecast(app.route, app.company, shown.assign(probability=np.nan))
        app.chart.canvas.draw()
        check('sem chance de chuva publicada o gráfico desenha e mostra traço no lugar da porcentagem', app.stats['rain'].cget('text').endswith('—') and not app.chart.chance.texts, app.stats['rain'].cget('text'))
        app.showForecast(app.route, app.company, shown)

        app.chart.handleMotion(type('Event', (), {'inaxes': app.chart.price, 'xdata': app.chart.x[0]})())
        now = app.chart.tip.get_text()
        app.chart.handleMotion(type('Event', (), {'inaxes': app.chart.price, 'xdata': app.chart.x[12]})())
        check('cruzeta e resumo no hover: dinâmica agora, tempo de viagem e hora de chegada', 'dinâmica estimada' in now and app.chart.tip.get_visible() and 'chegada às' in app.chart.tip.get_text(), app.chart.tip.get_text().replace(chr(10), ' | '))
    finally:
        app.stop()


if __name__ == '__main__':
    logging.basicConfig(level=logging.WARNING, format='%(asctime)s [%(threadName)s] %(levelname)s %(message)s')
    database.path = os.path.join(FOLDER, 'surge.db')

    try:
        worker.setup()
        testUtils()
        testApi()
        testDistance()
        measured = testMarket()
        testOracle()
        testModel()
        testRoutes()
        testWorker()
        testInterface()
    finally:
        shutil.rmtree(FOLDER, ignore_errors=True)

    table = pd.DataFrame({key: measured[key] for key in ('antiga', 'rota', 'cidade', 'app')}).assign(uf=measured['uf'])
    print(table.groupby('uf').agg(lambda e: np.abs(e).median()).round(3).assign(n=table.groupby('uf').size()).to_string())
    print(f'\n{len(failures)} falha(s): {failures}' if failures else '\ntodas as verificações passaram')
    raise SystemExit(1 if failures else 0)
