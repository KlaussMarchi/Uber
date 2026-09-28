import json, unicodedata
import numpy as np
import pandas as pd
from Utils.functions import getClock
from Utils.variables import MARKET_PATH


# aplicativo -> razao sobre a uberx, sensibilidade a dinamica e teto dela; a 99 nao publica precos e fica na razao tipica das comparacoes publicas
TARIFFS = {
    'uber': {'ratio': 1.00, 'surge': 1.00, 'cap': 2.50},
    '99':   {'ratio': 0.90, 'surge': 1.00, 'cap': 2.50},
}

DEMAND_SURGE = 0.35     # dinamica no pico de demanda mais forte da semana
RAIN_SURGE   = 0.40     # dinamica somada pela chuva forte
RAIN_DELAY   = 0.25     # tempo de viagem a mais com chuva forte
RAIN_MM      = 4.0      # chuva (mm/h) que produz 63% do efeito maximo
FREE         = 0.25     # parte do atraso medio que continua de madrugada (semaforo, conversao, via lenta)
VOLUME       = 0.30     # viagens fora dos picos em relacao a um pico, para ponderar a media do mes
BANDWIDTH    = 20.0     # km em que o nivel de uma cidade vizinha ainda pesa
PRIOR        = 0.05     # peso do estado frente as cidades proximas; longe de todas vale o estado
PACE         = (1.0, 3.5)    # ritmo real sobre o osrm; fora disso a regressao extrapola
KM_REF       = 20.0
LONG         = 40.0     # km a partir dos quais a uber cobra mais por km: a viagem intermunicipal volta vazia
SPEED_REF    = 50.0     # km/h
SAMPLE       = 20       # precos informados mais recentes de cada aplicativo
RADIUS       = 50.0     # km em que um preco informado ainda diz algo sobre outra origem
TAU          = 2700     # s ate a dinamica vista num preco informado cair a 37%
HOLD         = 120      # s em que o preco mostrado pelo app ainda vale inteiro
DYNAMIC      = 0.10     # desvio log do preco real em torno do esperado fora do pico; separa o nivel da dinamica
LIMITS       = (0.5, 2.0)    # preco informado fora disso em relacao ao esperado e erro de digitacao, nao mercado

# congestionamento tipico da semana nas metropoles brasileiras (tomtom traffic index 2025, media de rio, sao paulo, belo horizonte, recife, porto alegre, fortaleza, curitiba, salvador e brasilia): fracao de tempo a mais sobre o transito livre em cada hora local
TRAFFIC = np.array([
    [0.045, 0.033, 0.028, 0.015, 0.010, 0.093, 0.345, 0.609, 0.580, 0.489, 0.418, 0.389, 0.404, 0.416, 0.399, 0.410, 0.476, 0.650, 0.667, 0.418, 0.255, 0.190, 0.142, 0.088],    # segunda
    [0.044, 0.033, 0.030, 0.018, 0.014, 0.100, 0.356, 0.642, 0.647, 0.575, 0.490, 0.441, 0.456, 0.467, 0.447, 0.455, 0.532, 0.724, 0.753, 0.487, 0.292, 0.211, 0.158, 0.099],    # terca
    [0.058, 0.045, 0.040, 0.025, 0.018, 0.099, 0.347, 0.620, 0.632, 0.567, 0.486, 0.441, 0.462, 0.474, 0.452, 0.456, 0.534, 0.730, 0.765, 0.505, 0.304, 0.224, 0.169, 0.105],    # quarta
    [0.061, 0.044, 0.035, 0.018, 0.013, 0.094, 0.335, 0.601, 0.612, 0.552, 0.475, 0.437, 0.459, 0.469, 0.452, 0.464, 0.542, 0.733, 0.766, 0.517, 0.315, 0.227, 0.175, 0.110],    # quinta
    [0.058, 0.037, 0.029, 0.017, 0.014, 0.094, 0.321, 0.555, 0.546, 0.499, 0.461, 0.452, 0.506, 0.505, 0.482, 0.503, 0.605, 0.746, 0.719, 0.517, 0.353, 0.266, 0.215, 0.152],    # sexta
    [0.092, 0.060, 0.042, 0.027, 0.019, 0.050, 0.120, 0.196, 0.260, 0.314, 0.366, 0.393, 0.400, 0.350, 0.322, 0.322, 0.324, 0.334, 0.363, 0.355, 0.293, 0.244, 0.207, 0.160],    # sabado
    [0.105, 0.073, 0.052, 0.036, 0.027, 0.038, 0.077, 0.117, 0.142, 0.172, 0.203, 0.238, 0.263, 0.226, 0.210, 0.219, 0.238, 0.270, 0.308, 0.279, 0.235, 0.192, 0.133, 0.086],    # domingo
]).ravel()

# dia (0 = segunda, feriado vale 6), hora do centro, largura (h), demanda; picos gaussianos da procura por corrida na semana local, que movem a dinamica
PEAKS = np.array([
    *[(day, 13.00, 3.5, 0.10) for day in range(5)],
    *[(day, 7.75, 1.0, 0.55) for day in range(5)],
    *[(day, 18.00, 1.5, 0.70) for day in range(4)],
    (4, 18.25, 1.7, 0.85),
    (4, 23.50, 1.5, 0.45),
    (5, 12.00, 3.0, 0.15),
    (5, 22.00, 2.5, 0.50),
    (6, 1.50, 1.5, 0.35),
    (6, 12.00, 3.0, 0.10),
    (6, 18.00, 2.0, 0.35),
])

MARKET   = None    # tabela ajustada pelo market.py: tarifa sem dinamica, ritmo e nivel de cada municipio de origem
CITIES   = None
KEYS     = {}
FARES    = pd.DataFrame(columns=['company', 'ts', 'route_id', 'lat', 'lon', 'city', 'uf', 'level', 'expected', 'observed'])    # precos reais informados e o preco central que o app mostrava sem calibracao


def load(path=MARKET_PATH):
    global MARKET, CITIES, KEYS

    with open(path, encoding='utf-8') as file:
        MARKET = json.load(file)

    CITIES = np.array([city[2:] for city in MARKET['cities']], dtype=float)
    KEYS   = {getKey(*city[:2]): index for index, city in enumerate(MARKET['cities'])}

# CHAVE DO MUNICIPIO SEM ACENTO NEM CAIXA, COMO O NOMINATIM E O IBGE ESCREVEM DIFERENTE
def getKey(city, uf):
    return ''.join(ch for ch in unicodedata.normalize('NFD', f'{city}/{uf}') if not unicodedata.combining(ch)).lower()

# PRECOS REAIS INFORMADOS: OS MAIS RECENTES DE CADA APLICATIVO FICAM EM MEMORIA PARA A CALIBRACAO DE CADA CONSULTA
def update(rows):
    global FARES
    FARES = rows.sort_values('ts', kind='stable').groupby('company').tail(SAMPLE).reset_index(drop=True)

# CONGESTIONAMENTO REAL DA HORA LOCAL (INTERPOLADO ENTRE OS CENTROS DAS HORAS, CIRCULAR NA SEMANA) E DEMANDA POR PICOS GAUSSIANOS COM DISTANCIA CIRCULAR DE 168 H
def getProfile(weekday, hour):
    week   = (np.asarray(weekday) * 24 + np.asarray(hour) - 0.5) % 168    # o valor de cada hora vale no meio dela
    index  = np.floor(week).astype(int)
    share  = week - index
    demand = np.zeros(len(week))

    for day, center, width, rush in PEAKS:
        delta  = (np.asarray(weekday) * 24 + np.asarray(hour) - day * 24 - center + 84) % 168 - 84
        demand = demand + np.exp(-0.5 * (delta / width) ** 2) * rush

    return TRAFFIC[index] * (1 - share) + TRAFFIC[(index + 1) % 168] * share, demand

# DISTANCIA EM KM ENTRE UM PONTO E VARIOS (HAVERSINE)
def getDistances(lat, lon, lats, lons):
    p1, p2 = np.radians(lat), np.radians(lats)
    dp, dl = p2 - p1, np.radians(lons) - np.radians(lon)
    return 2 * 6371.0088 * np.arcsin(np.sqrt(np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2))

# NIVEL DE PRECO, RITMO E INCERTEZA NA ORIGEM: O DO MUNICIPIO QUANDO A UBER PUBLICA TRECHOS DELE; SENAO A MEDIA DOS VIZINHOS DO MESMO ESTADO, PUXADA PARA O ESTADO QUANDO NAO HA NENHUM PERTO
def getMarket(lat, lon, city='', uf=''):
    sigma = MARKET['sigma']
    index = KEYS.get(getKey(city, uf))

    if index is not None:
        return {'market': '/'.join(MARKET['cities'][index][:2]), 'level': float(CITIES[index, 2]), 'pace': float(CITIES[index, 3]), 'sigma': sigma['city']}

    distance = getDistances(lat, lon, CITIES[:, 0], CITIES[:, 1])
    uf       = uf or MARKET['cities'][int(np.argmin(distance))][1]
    same     = np.array([item[1] == uf for item in MARKET['cities']])
    state    = MARKET['states'].get(uf, MARKET['national'])
    weight   = np.exp(-(distance / BANDWIDTH) ** 2) * same
    total    = weight.sum() + PRIOR
    near     = int(np.argmin(np.where(same, distance, np.inf)))
    name     = f'{MARKET["cities"][near][0]}/{uf} e arredores' if same.any() and distance[near] <= 2 * BANDWIDTH else f'{uf} (média do estado)' if uf in MARKET['states'] else 'média nacional'
    return {
        'market': name,
        'level': float((weight @ CITIES[:, 2] + PRIOR * state[0]) / total),
        'pace': float((weight @ CITIES[:, 3] + PRIOR * state[1]) / total),
        'sigma': float(np.sqrt(sigma['city'] ** 2 + (sigma['state'] ** 2 - sigma['city'] ** 2) * PRIOR / total)),
    }

# MERCADO NA ORIGEM DE UMA ROTA
def getOrigin(route):
    return getMarket(route['o_lat'], route['o_lon'], route.get('city', ''), route.get('uf', ''))

# RITMO MEDIO DO MES SOBRE O TEMPO DO OSRM: O OSRM ERRA MAIS NA CIDADE (CURTA E LENTA) E NA RODOVIA RAPIDA, E CADA REGIAO TEM O SEU TRANSITO
def getPace(route, pace):
    speed = route['distance'] / route['duration'] * 60
    slope = MARKET['pace']
    return float(np.clip(np.exp(pace + slope['km'] * np.log(route['distance'] / KM_REF) + slope['speed'] * np.log(speed / SPEED_REF)), *PACE))

# ESTADO ESPERADO NO INSTANTE, O MESMO PARA OS DOIS APLICATIVOS: EXCESSO DE DEMANDA, CONGESTIONAMENTO RELATIVO E MINUTOS DE VIAGEM
def getState(route, ts, rain):
    weekday, hour, _ = getClock(np.asarray(ts, dtype=np.int64), route['tz'])
    traffic, demand = getProfile(weekday, hour)
    wet     = 1 - np.exp(-np.asarray(rain, dtype=float) / RAIN_MM)
    pace    = getPace(route, getOrigin(route)['pace'])
    free    = 1 + (pace - 1) * FREE
    minutes = route['duration'] * (free + (pace - free) * traffic / MARKET['traffic']) * (1 + RAIN_DELAY * wet)
    return DEMAND_SURGE * demand + RAIN_SURGE * wet, traffic / MARKET['traffic'], minutes

# TEMPO DE VIAGEM DE MADRUGADA, SEM TRANSITO: A REFERENCIA DO RESUMO E DO GRAFICO
def getFree(route):
    pace = getPace(route, getOrigin(route)['pace'])
    return route['duration'] * (1 + (pace - 1) * FREE)

# MULTIPLICADOR DA DINAMICA DO APLICATIVO SOBRE O EXCESSO DE DEMANDA, LIMITADO PELO TETO
def getSurge(excess, company='uber'):
    fare = TARIFFS[company]
    return np.minimum(1 + fare['surge'] * np.asarray(excess, dtype=float), fare['cap'])

# PRECO SEM CALIBRACAO: TARIFA SEM DINAMICA NO NIVEL DA CIDADE DE ORIGEM, COM PISO DA CORRIDA CURTA, VEZES A DINAMICA
def getTariff(level, distance, minutes, excess, company='uber'):
    card = MARKET['tariff']
    base = np.maximum(card['floor'], card['base'] + card['km'] * np.asarray(distance, dtype=float) + card['minute'] * np.asarray(minutes, dtype=float) + card['long'] * np.maximum(np.asarray(distance, dtype=float) - LONG, 0))
    return np.exp(level) * TARIFFS[company]['ratio'] * base * getSurge(excess, company)

# PARTE DO PRECO QUE VEM DOS MINUTOS; E O QUANTO O ERRO DO TEMPO DE VIAGEM PESA NO PRECO
def getShare(distance, minutes):
    card = MARKET['tariff']
    time = card['minute'] * np.asarray(minutes, dtype=float)
    base = card['base'] + card['km'] * np.asarray(distance, dtype=float) + time + card['long'] * np.maximum(np.asarray(distance, dtype=float) - LONG, 0)
    return np.where(base > card['floor'], time / base, 0.0)

# CALIBRACAO PELOS PRECOS INFORMADOS: NIVEL BAYESIANO (PRIORI DA REGIAO, PESO PELA DISTANCIA DA ORIGEM) E A DINAMICA DO PRECO MAIS RECENTE, QUE SE DISSIPA COM O TEMPO
def getCalibration(route, company, now):
    market = getOrigin(route)
    rows   = FARES[(FARES['company'] == company) & (FARES['ts'] <= now)]

    if rows.empty:
        return {'mean': 0.0, 'var': market['sigma'] ** 2, 'last': 0.0, 'ts': int(now), 'weight': 0.0, 'count': 0, 'market': market}

    level  = np.array([getMarket(*row)['level'] for row in rows[['lat', 'lon', 'city', 'uf']].itertuples(index=False)])
    y      = np.log(np.clip(rows['observed'].to_numpy(float) / (rows['expected'].to_numpy(float) * np.exp(level - rows['level'].to_numpy(float))), *LIMITS))
    weight = np.exp(-getDistances(route['o_lat'], route['o_lon'], rows['lat'].to_numpy(float), rows['lon'].to_numpy(float)) / RADIUS)
    old    = weight[:-1] / DYNAMIC ** 2
    v0     = 1 / (1 / market['sigma'] ** 2 + old.sum())
    m0     = v0 * (old @ y[:-1])
    noise  = DYNAMIC ** 2 / weight[-1]
    return {
        'mean': float(m0 + v0 / (v0 + noise) * (y[-1] - m0)),
        'var': float(v0 * noise / (v0 + noise)),
        'last': float(y[-1]),
        'ts': int(rows['ts'].iloc[-1]),
        'weight': float(weight[-1]),
        'count': int((weight > 0.5).sum()),
        'market': market,
    }

# CALIBRACAO NEUTRA QUE ANCORA EXATAMENTE NO INSTANTE: E COM ELA QUE O PRECO CENTRAL SEM CALIBRACAO E GUARDADO JUNTO DO PRECO INFORMADO
def getAnchor(route, now):
    return {'mean': 0.0, 'var': 0.0, 'last': 0.0, 'ts': int(now), 'weight': 1.0, 'count': 0, 'market': getOrigin(route)}
