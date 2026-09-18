import numpy as np
import pandas as pd
from Utils.functions import getClock
from Utils.variables import STEP


# tarifa de cada aplicativo na regiao; a da uber reproduz os R$ 54,85 medidos na rota de referencia e a da 99 sai cerca de 10% abaixo, como o mercado
# tabela publicada da UberX na capital do Rio em 2026: bandeirada 2,30, R$ 1,55/km, R$ 0,32/min, minima 10,50 e reserva 0,75; a 99 nao publica tabela fixa
TARIFFS = {
    'uber': {'base': 2.50, 'km': 1.2076, 'minute': 0.2200, 'fee': 1.00, 'floor': 7.50, 'surge': 1.00, 'cap': 2.50},
    '99':   {'base': 2.10, 'km': 1.1147, 'minute': 0.2031, 'fee': 0.00, 'floor': 6.50, 'surge': 0.78, 'cap': 2.20},
}

FARES  = {company: dict(fare) for company, fare in TARIFFS.items()}    # tabela em uso; cada preco real informado pelo usuario desloca ela
TERMS  = ('base', 'km', 'minute')
RIDGE  = 6.0            # observacoes equivalentes que a forma da tabela publicada vale no ajuste; o nivel ja se move com a primeira
LIMITS = (0.5, 2.0)     # correcao maxima de cada termo, para um preco digitado errado nao destruir a tarifa
SAMPLE = 20             # precos reais mais recentes de cada aplicativo que entram no ajuste
DIGITS = 6              # casas da tarifa ajustada; arredondar aqui faz o celular e o computador chegarem na mesma tabela apesar do solver diferente

URBAN          = 0.18     # atraso relativo no pico fora do corredor
CORRIDOR       = 0.60     # atraso relativo no pico na Amaral Peixoto
INCIDENT_DELAY = 0.80
RAIN_DELAY     = 0.25
RAIN_MM        = 4.0      # chuva (mm/h) que produz 63% do efeito maximo
DEMAND_SURGE   = 0.20
RAIN_SURGE     = 0.32
INCIDENT_SURGE = 0.25
INCIDENTS      = 0.7      # acidentes por dia no corredor
INCIDENT_TAU   = 2700     # s ate o congestionamento de um acidente cair a 37%
NOISE          = 0.025    # desvio log do preco entre slots (oferta de motoristas)
DAY_NOISE      = 0.015    # desvio log do nivel de cada dia local
TIME_NOISE     = 0.04     # desvio log do tempo de viagem entre slots (semaforos, motorista, fila)
DAY_TIME_NOISE = 0.02     # desvio log do transito de cada dia local (obra, ferias escolares, evento)
SLOTS          = 150      # slots de ruido por dia; 25 h cobrem o dia de fim de horario de verao
SEED           = 7

# dia da semana (0 = segunda, feriado entra como 6), hora do pico, amplitude da demanda, largura (h)
PEAKS = np.array([
    *[(day, 7.00, 0.70, 0.9) for day in range(5)],
    *[(day, 12.25, 0.15, 0.7) for day in range(5)],
    *[(day, 17.75, 0.85, 1.1) for day in range(4)],
    (4, 18.00, 1.00, 1.3),
    (4, 23.00, 0.30, 1.5),
    (5, 11.00, 0.35, 2.0),
    (5, 21.00, 0.30, 2.5),
    (6, 17.00, 0.55, 2.0),
])


# TARIFA DO APLICATIVO; SEM DINAMICA E NA DURACAO LIVRE E A BASE QUE O MODELO USA PARA NORMALIZAR O PRECO
def getTariff(distance, duration, surge=1.0, company='uber'):
    fare = FARES[company]
    return fare['fee'] + np.maximum(fare['floor'], fare['base'] + fare['km'] * distance + fare['minute'] * duration) * surge

# AJUSTE DA TARIFA AOS PRECOS REAIS INFORMADOS: O NIVEL SEGUE A PRIMEIRA OBSERVACAO E A FORMA SO SE MOVE QUANDO VARIAS ROTAS DISCORDAM DA TABELA PUBLICADA
def update(rows):
    for company, fare in TARIFFS.items():
        found  = rows[(rows['company'] == company) & (rows['observed'] > fare['fee'])].tail(SAMPLE)
        fitted = dict(fare)
        terms  = np.column_stack([np.full(len(found), fare['base']), fare['km'] * found['distance'], fare['minute'] * found['minutes']])
        surge  = found['surge'].to_numpy()
        above  = terms.sum(axis=1) > fare['floor']    # abaixo da tarifa minima o preco nao depende dos coeficientes, so do piso
        seen   = (found['observed'].to_numpy() - fare['fee']) / np.maximum(surge, 1e-9)

        if (~above).any():
            fitted['floor'] = round(fare['floor'] * getBounded(np.median(seen[~above]) / fare['floor']), DIGITS)

        if not above.any():
            FARES[company] = fitted
            continue

        terms  = terms[above] * surge[above, None]
        target = found['observed'].to_numpy()[above] - fare['fee']
        whole  = terms.sum(axis=1)
        level  = float(whole @ target / max(whole @ whole, 1e-9))
        scale  = np.sqrt((terms ** 2).mean(axis=0)) + 1e-9
        normal = terms / scale
        shape  = np.linalg.solve(normal.T @ normal + RIDGE * np.eye(len(TERMS)), normal.T @ target + RIDGE * level * scale) / scale
        FARES[company] = {**fitted, **{term: round(fare[term] * getBounded(value), DIGITS) for term, value in zip(TERMS, shape)}}

# CORRECAO ACEITA PARA UM TERMO DA TARIFA; UM PRECO DIGITADO ERRADO NAO TIRA A TABELA DA FAIXA PLAUSIVEL
def getBounded(value):
    return float(np.clip(value, *LIMITS)) if np.isfinite(value) else 1.0

# DEMANDA RELATIVA NA SEMANA LOCAL: PICOS GAUSSIANOS COM DISTANCIA CIRCULAR DE 168 H, QUE ATRAVESSA MEIA-NOITE E DOMINGO
def getDemand(weekday, hour):
    delta = (weekday[:, None] * 24 + hour[:, None] - PEAKS[:, 0] * 24 - PEAKS[:, 1] + 84) % 168 - 84
    return (PEAKS[:, 2] * np.exp(-0.5 * (delta / PEAKS[:, 3]) ** 2)).sum(axis=1)

# SORTEIOS DO MUNDO SIMULADO COM SEMENTE POR DIA LOCAL: ACIDENTES NO CORREDOR (COMUNS AS ROTAS), RUIDO DE OFERTA E DE TEMPO DE VIAGEM
def getRandom(ts, routeId, days, tz):
    incident = np.zeros(len(ts))
    noise    = np.zeros(len(ts))
    jitter   = np.zeros(len(ts))
    numbers  = days.astype(np.int64)

    for day in range(numbers.min() - 1, numbers.max() + 1):
        midnight = pd.Timestamp(day, unit='D').tz_localize(tz, nonexistent='shift_forward').timestamp()
        rng      = np.random.default_rng((SEED, day))
        starts   = midnight + rng.uniform(0, 86400, rng.poisson(INCIDENTS))
        age      = ts[:, None] - starts
        incident += (rng.uniform(0.3, 1.0, len(starts)) * np.exp(-np.maximum(age, 0) / INCIDENT_TAU) * (age >= 0)).sum(axis=1)

        today = numbers == day
        slot  = ((ts[today] - midnight) // STEP).astype(np.int64)
        rng   = np.random.default_rng((SEED, routeId, day))
        noise[today]  = rng.normal(0, DAY_NOISE) + rng.normal(0, NOISE, SLOTS)[slot]
        jitter[today] = rng.normal(0, DAY_TIME_NOISE) + rng.normal(0, TIME_NOISE, SLOTS)[slot]

    return incident, noise, jitter

# ESTADO DO MERCADO NO INSTANTE, O MESMO PARA OS DOIS APLICATIVOS: EXCESSO DE DEMANDA, RUIDO DA OFERTA E MINUTOS DE VIAGEM
def getState(route, ts, rain):
    ts      = np.asarray(ts, dtype=np.int64)
    wet     = 1 - np.exp(-np.asarray(rain, dtype=float) / RAIN_MM)
    weekday, hour, days = getClock(ts, route['tz'])
    demand  = getDemand(weekday, hour)
    incident, noise, jitter = getRandom(ts, route['id'], days, route['tz'])
    delay   = (URBAN + CORRIDOR * route['corridor']) * demand + INCIDENT_DELAY * route['corridor'] * incident + RAIN_DELAY * wet
    excess  = DEMAND_SURGE * demand + RAIN_SURGE * wet + INCIDENT_SURGE * route['corridor'] * incident
    return excess, noise, np.round(route['duration'] * (1 + delay) * np.exp(jitter), 1)

# MULTIPLICADOR EFETIVO DO APLICATIVO: A DINAMICA DELE SOBRE O EXCESSO DE DEMANDA, LIMITADA PELO TETO, MAIS O RUIDO DA OFERTA, QUE E DO MERCADO
def getSurge(excess, noise, company='uber'):
    fare = FARES[company]
    return np.minimum(1 + fare['surge'] * np.asarray(excess, dtype=float), fare['cap']) * np.exp(np.asarray(noise, dtype=float))

# PRECO QUE O APLICATIVO MOSTRA A PARTIR DO ESTADO GUARDADO
def getPrice(distance, minutes, excess, noise, company='uber'):
    return np.round(getTariff(np.asarray(distance, dtype=float), np.asarray(minutes, dtype=float), getSurge(excess, noise, company), company), 2)

# PRECO E TEMPO DE VIAGEM QUE O APLICATIVO MOSTRA NO INSTANTE: O MESMO ATRASO DE TRANSITO ALONGA A VIAGEM E ENTRA NA TARIFA
def getMarket(route, ts, rain, company='uber'):
    excess, noise, minutes = getState(route, ts, rain)
    return getPrice(route['distance'], minutes, excess, noise, company), minutes
