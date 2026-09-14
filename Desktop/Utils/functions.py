import numpy as np
import pandas as pd
from datetime import date, timedelta
from functools import lru_cache


# DATAS LOCAIS SEM FUSO A PARTIR DE TIMESTAMPS UNIX; PRECO E TRANSITO SEGUEM O RELOGIO DO LUGAR DA ROTA
def getLocal(ts, tz):
    return pd.to_datetime(np.asarray(ts), unit='s', utc=True).tz_convert(tz).tz_localize(None)

# FERIADOS NACIONAIS DO CALENDARIO ANBIMA: OS 10 DE LEI MAIS CARNAVAL E CORPUS CHRISTI; PASCOA PELO ALGORITMO DE MEEUS/JONES/BUTCHER
@lru_cache
def getHolidays(year):
    a, b, c = year % 19, year // 100, year % 100
    d, e    = b // 4, b % 4
    g       = (b - (b + 8) // 25 + 1) // 3
    h       = (19 * a + b - d - g + 15) % 30
    l       = (32 + 2 * e + 2 * (c // 4) - h - c % 4) % 7
    m       = (a + 11 * h + 22 * l) // 451
    easter  = date(year, (h + l - 7 * m + 114) // 31, (h + l - 7 * m + 114) % 31 + 1)
    fixed   = [(1, 1), (4, 21), (5, 1), (9, 7), (10, 12), (11, 2), (11, 15), (11, 20), (12, 25)]
    moving  = [-48, -47, -2, 60]    # segunda e terca de carnaval, sexta-feira santa, corpus christi
    return frozenset({date(year, month, day) for month, day in fixed} | {easter + timedelta(days=n) for n in moving})

# RELOGIO DA DEMANDA: DIA DA SEMANA (0 = SEGUNDA, FERIADO VALE COMO DOMINGO), HORA LOCAL CONTINUA E O DIA LOCAL
def getClock(ts, tz):
    local    = getLocal(ts, tz)
    days     = local.values.astype('datetime64[D]')
    holidays = np.array(sorted(day for year in set(local.year) for day in getHolidays(year)), dtype='datetime64[D]')
    return np.where(np.isin(days, holidays), 6, local.weekday), (local.hour + local.minute / 60).to_numpy(), days

# DISTANCIA EM LINHA RETA ENTRE DOIS PONTOS (HAVERSINE), EM KM
def getDistance(src, dst):
    lat1, lon1, lat2, lon2 = np.radians([src['lat'], src['lon'], dst['lat'], dst['lon']])
    return 2 * 6371.0088 * np.arcsin(np.sqrt(np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2))

# VALOR EM REAIS NO PADRAO BRASILEIRO (R$ 1.234,56)
def getMoney(value):
    return 'R$ ' + f'{value:,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.')

# DURACAO LEGIVEL ARREDONDADA AO MINUTO: 47 min ATE UMA HORA, DEPOIS 2h05
def getDuration(minutes):
    total = int(round(float(minutes)))
    return f'{total} min' if total < 60 else f'{total // 60}h{total % 60:02d}'

# FAIXA DE DURACAO SEM REPETIR A UNIDADE QUANDO AS DUAS PONTAS FICAM ABAIXO DE UMA HORA (40 a 45 min, 55 min a 1h05, 1h05 a 1h20)
def getSpan(low, high):
    return f'{int(round(float(low)))} a {getDuration(high)}' if round(float(high)) < 60 else f'{getDuration(low)} a {getDuration(high)}'

# DIFERENCA DE TEMPO COM SINAL (+14 min, -2 min, +1h05), NO FORMATO DA DURACAO
def getDelay(minutes):
    total = int(round(float(minutes)))
    return ('-' if total < 0 else '+') + getDuration(abs(total))
