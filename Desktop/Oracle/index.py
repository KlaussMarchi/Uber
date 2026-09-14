import numpy as np
import pandas as pd
from Utils.functions import getClock
from Utils.variables import STEP


# tarifa publica da regiao; PER_KM calibrado para a mediana da rota de referencia em dia util, 9-16 h e sem chuva, dar R$ 54,85
BASE     = 2.50
PER_KM   = 1.2076
PER_MIN  = 0.22
FEE      = 1.00
MIN_FARE = 7.50

URBAN          = 0.18     # atraso relativo no pico fora do corredor
CORRIDOR       = 0.60     # atraso relativo no pico na Amaral Peixoto
INCIDENT_DELAY = 0.80
RAIN_DELAY     = 0.25
RAIN_MM        = 4.0      # chuva (mm/h) que produz 63% do efeito maximo
DEMAND_SURGE   = 0.20
RAIN_SURGE     = 0.32
INCIDENT_SURGE = 0.25
SURGE_MAX      = 2.5
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


# TARIFA DA PLATAFORMA; SEM DINAMICA E NA DURACAO LIVRE E A BASE QUE O MODELO USA PARA NORMALIZAR O PRECO
def getTariff(distance, duration, surge=1.0):
    return FEE + np.maximum(MIN_FARE, BASE + PER_KM * distance + PER_MIN * duration) * surge

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

# PRECO E TEMPO DE VIAGEM QUE O MERCADO MOSTRA NO INSTANTE: O MESMO ATRASO DE TRANSITO ALONGA A VIAGEM E ENTRA NA TARIFA
def getMarket(route, ts, rain):
    ts      = np.asarray(ts, dtype=np.int64)
    wet     = 1 - np.exp(-np.asarray(rain, dtype=float) / RAIN_MM)
    weekday, hour, days = getClock(ts, route['tz'])
    demand  = getDemand(weekday, hour)
    incident, noise, jitter = getRandom(ts, route['id'], days, route['tz'])
    delay   = (URBAN + CORRIDOR * route['corridor']) * demand + INCIDENT_DELAY * route['corridor'] * incident + RAIN_DELAY * wet
    surge   = np.minimum(1 + DEMAND_SURGE * demand + RAIN_SURGE * wet + INCIDENT_SURGE * route['corridor'] * incident, SURGE_MAX)
    minutes = route['duration'] * (1 + delay) * np.exp(jitter)
    return np.round(getTariff(route['distance'], minutes, surge * np.exp(noise)), 2), np.round(minutes, 1)
