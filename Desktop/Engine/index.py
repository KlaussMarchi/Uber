import numpy as np
import pandas as pd
from time import time
from Api.index import api
from Database.index import database
from Model.index import get, getBands
import Oracle.index as oracle
from Oracle.index import getAnchor, getCalibration, getState, getSurge
from Utils.functions import getDistance
from Utils.variables import STEP, HORIZON


# NUCLEO DA CONSULTA: ROTA, CLIMA, PRECO E TEMPO ESPERADOS AGORA E SERIE DE 12 H CALIBRADA PELOS PRECOS INFORMADOS; USADO PELA JANELA E PELO WORKER
class Engine:
    WEATHER_TTL  = 600    # s de validade da previsao do tempo em cache
    MIN_DISTANCE = 0.3    # km abaixo do qual origem e destino sao o mesmo ponto

    def __init__(self):
        self.weather = {}

    # TABELA AJUSTADA AS MEDIAS REAIS E PRECOS JA INFORMADOS PELO USUARIO
    def setup(self):
        oracle.load()
        self.update()

    def update(self):
        oracle.update(database.get('SELECT company, ts, route_id, lat, lon, city, uf, level, expected, observed FROM Fares ORDER BY ts, id'))

    # ROTA COM CACHE NO BANCO, FUSO E MUNICIPIO DA ORIGEM; MARCA O USO PORQUE O WORKER OBSERVA AS ROTAS MAIS RECENTES
    def getRoute(self, src, dst):
        key   = (round(src['lat'], 5), round(src['lon'], 5), round(dst['lat'], 5), round(dst['lon'], 5))
        where = 'WHERE o_lat = ? AND o_lon = ? AND d_lat = ? AND d_lon = ?'

        if database.get(f'SELECT id FROM Routes {where}', key).empty:
            res  = api.getRoute(src, dst)
            zone = None if res is None else api.getZone(src['lat'], src['lon'])

            if zone is None or res['distance'] < self.MIN_DISTANCE:
                return None

            database.set('INSERT OR IGNORE INTO Routes (origin, destination, o_lat, o_lon, d_lat, d_lon, distance, duration, tz, used_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)', [(src['label'], dst['label'], *key, res['distance'], res['duration'], zone)])

        database.set(f'UPDATE Routes SET used_at = ? {where}', [(int(time()), *key)])
        route = database.get(f'SELECT * FROM Routes {where}', key).iloc[0].to_dict()
        place = None if route['city'] else api.getCity(route['o_lat'], route['o_lon'])    # sem o municipio vale a vizinhanca; a proxima consulta tenta de novo

        if place is not None:
            database.set('UPDATE Routes SET city = ?, uf = ? WHERE id = ?', [(place['city'], place['uf'], route['id'])])
            route.update(place)

        return route

    # LUGARES QUE O USUARIO JA CONFIRMOU, DOS MAIS RECENTES
    def getPlaces(self, limit=5):
        df = database.get('SELECT label, lat, lon FROM (SELECT origin AS label, o_lat AS lat, o_lon AS lon, used_at FROM Routes UNION ALL SELECT destination, d_lat, d_lon, used_at FROM Routes) WHERE used_at > 0 ORDER BY used_at DESC')
        return df.drop_duplicates('label').head(limit).to_dict('records')

    # PREVISAO DE CHUVA POR CELULA DE ~1 KM COM CACHE CURTO; SE A API FALHA, SEGUE COM A ULTIMA PREVISAO RECEBIDA
    def getWeather(self, lat, lon):
        key = (round(lat, 2), round(lon, 2))
        hit = self.weather.get(key)

        if hit is None or time() - hit[0] > self.WEATHER_TTL:
            df  = api.getWeather(*key, past_days=1, forecast_days=2)
            hit = (time(), df) if df is not None else hit

        if hit is None:
            return None

        self.weather[key] = hit
        return hit[1]

    # CHUVA E CHANCE NA GRADE DA SERIE: A LINHA 0 E O INSTANTE EXATO, AS DEMAIS SAO OS SLOTS DE 10 MIN ATE 12 H
    def getGrid(self, route, now):
        ts      = np.append(now, np.arange((now // STEP + 1) * STEP, now + HORIZON + 1, STEP))
        weather = self.getWeather(route['o_lat'], route['o_lon'])

        # previsao em cache que ja nao cobre o horizonte viraria chuva constante no fim da serie
        if weather is None or weather['ts'].max() < ts[-1]:
            return None

        return pd.DataFrame({'ts': ts, 'rain': np.interp(ts, weather['ts'], weather['rain']), 'probability': np.interp(ts, weather['ts'], weather['probability'])})

    def get(self, route, company='uber', now=None):
        now  = int(time() if now is None else now)
        grid = self.getGrid(route, now)

        if grid is None:
            return None

        bands  = get(route, grid['ts'], grid['rain'], grid['probability'], company, getCalibration(route, company, now))
        excess = getState(route, grid['ts'][:1], grid['rain'][:1])[0]
        blank  = [np.nan] * (len(grid) - 1)
        return pd.concat([grid, bands], axis=1).assign(price=[bands['p50'][0]] + blank, minutes=[bands['m50'][0]] + blank, surge=[float(getSurge(excess, company)[0])] + blank)

    # PRECO REAL LIDO NO APLICATIVO: GUARDA COM O PRECO CENTRAL QUE O APP MOSTRARIA SEM CALIBRACAO NAQUELE INSTANTE E RECALIBRA; ZERO APAGA OS PRECOS DO APLICATIVO
    def setFare(self, route, company, observed, now=None):
        now = int(time() if now is None else now)

        if observed <= 0:
            database.set('DELETE FROM Fares WHERE company = ?', [(company,)])
        else:
            grid = self.getGrid(route, now)

            if grid is None:
                return None

            anchor   = getAnchor(route, now)
            expected = float(getBands(route, grid['ts'][:1], grid['rain'][:1], grid['probability'][:1], company, anchor)['p50'][0])
            database.set('INSERT INTO Fares (company, ts, route_id, lat, lon, city, uf, level, expected, observed) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', [(company, now, int(route['id']), route['o_lat'], route['o_lon'], route['city'], route['uf'], anchor['market']['level'], expected, float(observed))])

        self.update()
        return int(database.get('SELECT COUNT(*) AS n FROM Fares WHERE company = ?', (company,))['n'][0])

    # CONSULTA COMPLETA: VALIDA OS ENDERECOS, TRACA A ROTA E PREVE; QUANDO UMA ETAPA FALHA DEVOLVE O MOTIVO PARA A TELA
    def getQuote(self, src, dst, company='uber', now=None):
        src = src if 'lat' in src else api.geocode(src['label'])

        if src is None:
            return {'error': 'Origem não encontrada. Escolha uma das sugestões da lista.'}

        dst = dst if 'lat' in dst else api.geocode(dst['label'])

        if dst is None:
            return {'error': 'Destino não encontrado. Escolha uma das sugestões da lista.'}

        if getDistance(src, dst) < self.MIN_DISTANCE:
            return {'error': 'Origem e destino são praticamente o mesmo ponto.'}

        route = self.getRoute(src, dst)

        if route is None:
            return {'error': 'Sem rota de carro entre esses pontos (ilha, mar ou via inacessível) ou OSRM/Open-Meteo fora do ar.'}

        df = self.get(route, company, now)

        if df is None:
            return {'error': 'Previsão do tempo indisponível (Open-Meteo), tente novamente em instantes.'}

        return {'src': src, 'dst': dst, 'route': route, 'company': company, 'df': df}


engine = Engine()
