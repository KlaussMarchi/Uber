import numpy as np
import pandas as pd
import Oracle.index as oracle
from Oracle.index import DYNAMIC, HOLD, RAIN_MM, TAU, getShare, getState, getTariff


QUANTILES    = {'10': 0.10, '50': 0.50, '90': 0.90}
DIGITS       = {'p': 2, 'm': 2}          # centavos e centesimo de minuto, para a faixa nao empatar em viagem curta
MIN_BAND     = 0.01                      # largura minima da faixa, fracao da mediana
DYNAMIC_PEAK = 0.15                      # desvio a mais por unidade de excesso de demanda (pico, chuva)
TIME_BASE    = 0.06                      # desvio log do tempo de viagem de madrugada
TIME_PEAK    = 0.04                      # desvio a mais por unidade de congestionamento relativo
TIME_RAIN    = 0.10                      # desvio a mais com chuva forte
RAIN_SURE    = 0.1                       # mm/h previstos a partir dos quais a chuva conta como provavel mesmo com chance baixa
RAIN_LIGHT   = 0.5                       # mm/h minimos do cenario chuvoso
RAIN_HEAVY   = 30.0
SIGMA_MIN    = 1e-4
STEPS        = 60                        # bissecoes do quantil; 2^-60 da faixa inicial ja e menor que um centavo
ERFC         = (-1.26551223, 1.00002368, 0.37409196, 0.09678418, -0.18628806, 0.27886807, -1.13520398, 1.48851587, -0.82215223, 0.17087277)


# DISTRIBUICAO NORMAL ACUMULADA PELA ERFC DO NUMERICAL RECIPES (ERRO RELATIVO ABAIXO DE 1,2E-7), A MESMA CONTA NO DESKTOP E NO CELULAR
def getNormal(z):
    x = -np.asarray(z, dtype=float) / np.sqrt(2.0)
    t = 1 / (1 + 0.5 * np.abs(x))
    poly = ERFC[9]

    for coef in ERFC[8::-1]:
        poly = coef + t * poly

    r = t * np.exp(-x * x + poly)
    return 0.5 * np.where(x >= 0, r, 2 - r)

# QUANTIL DE UMA MISTURA DE DUAS NORMAIS (SECO E CHUVOSO) POR BISSECAO; SEM CHUVA POSSIVEL VIRA A NORMAL DO CENARIO SECO
def getQuantile(q, wet, means, sigmas):
    lo = np.minimum(means[0] - 8 * sigmas[0], means[1] - 8 * sigmas[1])
    hi = np.maximum(means[0] + 8 * sigmas[0], means[1] + 8 * sigmas[1])

    for _ in range(STEPS):
        mid   = (lo + hi) / 2
        below = (1 - wet) * getNormal((mid - means[0]) / sigmas[0]) + wet * getNormal((mid - means[1]) / sigmas[1]) < q
        lo    = np.where(below, mid, lo)
        hi    = np.where(below, hi, mid)

    return (lo + hi) / 2

# CENARIOS DE CHUVA DE CADA INSTANTE: A CHANCE DO OPEN-METEO E O PESO DO CHUVOSO, E A CHUVA DELE E A PREVISTA DIVIDIDA PELA CHANCE (A PREVISAO JA E A MEDIA)
def getScenarios(rain, probability):
    rain   = np.asarray(rain, dtype=float)
    chance = np.clip(np.nan_to_num(np.asarray(probability, dtype=float) / 100, nan=0.0), 0, 1)
    wet    = np.where(rain > RAIN_SURE, np.maximum(chance, 0.5), chance)
    heavy  = np.clip(rain / np.maximum(wet, 1e-9), RAIN_LIGHT, RAIN_HEAVY)
    return wet, heavy

# FAIXAS P10/P50/P90 DO PRECO E M10/M50/M90 DO TEMPO DE VIAGEM NOS INSTANTES PEDIDOS; O PRECO INFORMADO MAIS RECENTE PUXA O COMECO DA SERIE E SE DISSIPA
def getBands(route, ts, rain, probability, company, calibration):
    ts      = np.asarray(ts, dtype=np.int64)
    wet, heavy = getScenarios(rain, probability)
    near    = calibration['weight'] * np.exp(-np.maximum(ts - calibration['ts'] - HOLD, 0) / TAU) * (ts >= calibration['ts'])
    shift   = calibration['mean'] + near * (calibration['last'] - calibration['mean'])
    error   = oracle.MARKET['sigma']['pace']    # desvio log do ritmo medio da rota, que o preco informado nao corrige
    prices, times = [], []

    for scenario in (np.zeros(len(ts)), heavy):
        excess, traffic, minutes = getState(route, ts, scenario)
        drops   = 1 - np.exp(-scenario / RAIN_MM)
        dynamic = DYNAMIC + DYNAMIC_PEAK * excess
        travel  = TIME_BASE + TIME_PEAK * traffic + TIME_RAIN * drops
        spread  = (1 - near) ** 2 * calibration['var'] + (1 - near ** 2) * (dynamic ** 2 + (getShare(route['distance'], minutes) * travel) ** 2)    # o preco informado ja traz a dinamica e o transito daquele instante
        tariff  = getTariff(calibration['market']['level'], route['distance'], minutes, excess, company)
        prices.append((np.log(tariff) + shift, np.sqrt(np.maximum(spread, SIGMA_MIN ** 2))))
        times.append((np.log(minutes), np.sqrt(error ** 2 + travel ** 2)))

    out = {}

    for prefix, parts in (('p', prices), ('m', times)):
        means, sigmas = [part[0] for part in parts], [part[1] for part in parts]
        lower, middle, upper = [np.exp(getQuantile(q, wet, means, sigmas)) for q in QUANTILES.values()]
        lower = np.minimum(lower, middle * (1 - MIN_BAND / 2))
        upper = np.maximum(upper, middle * (1 + MIN_BAND / 2))
        out.update({f'{prefix}{key}': value for key, value in zip(QUANTILES, (lower, middle, upper))})

    return out

def get(route, ts, rain, probability, company, calibration):
    return pd.DataFrame({key: np.round(value, DIGITS[key[0]]) for key, value in getBands(route, ts, rain, probability, company, calibration).items()})
