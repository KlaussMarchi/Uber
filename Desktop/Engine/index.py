import numpy as np
import pandas as pd
from time import time
from Api.index import api
from Database.index import database
from Model.index import model
from Oracle.index import getMarket
from Utils.functions import getClock, getDistance
from Utils.variables import STEP, HORIZON


# NUCLEO DA CONSULTA: ROTA, CLIMA, PRECO OBSERVADO AGORA E SERIE DE 12 H ANCORADA NAS OBSERVACOES DE HOJE; USADO PELA JANELA E PELO WORKER
class Engine:
    WEATHER_TTL  = 600    # s de validade da previsao do tempo em cache
    MIN_DISTANCE = 0.3    # km abaixo do qual origem e destino sao o mesmo ponto

    def __init__(self):
        self.weather = {}

    # ROTA COM CACHE NO BANCO E FUSO DA ORIGEM; MARCA O USO PORQUE O WORKER OBSERVA AS ROTAS MAIS RECENTES
    def getRoute(self, src, dst):
        key   = (round(src['lat'], 5), round(src['lon'], 5), round(dst['lat'], 5), round(dst['lon'], 5))
        where = 'WHERE o_lat = ? AND o_lon = ? AND d_lat = ? AND d_lon = ?'

        if database.get(f'SELECT id FROM Routes {where}', key).empty:
            res  = api.getRoute(src, dst)
            zone = None if res is None else api.getZone(src['lat'], src['lon'])

            if zone is None or res['distance'] < self.MIN_DISTANCE:
                return None

            database.set('INSERT OR IGNORE INTO Routes (origin, destination, o_lat, o_lon, d_lat, d_lon, distance, duration, corridor, tz, used_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)', [(src['label'], dst['label'], *key, res['distance'], res['duration'], res['corridor'], zone)])

        database.set(f'UPDATE Routes SET used_at = ? {where}', [(int(time()), *key)])
        return database.get(f'SELECT * FROM Routes {where}', key).iloc[0].to_dict()

    # LUGARES QUE O USUARIO JA CONFIRMOU, DOS MAIS RECENTES; AS ROTAS DO BOOTSTRAP TEM USED_AT 0 E FICAM DE FORA, PORQUE NINGUEM AS ESCOLHEU
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

    def get(self, route, now=None):
        now     = int(time() if now is None else now)
        ts      = np.append(now, np.arange((now // STEP + 1) * STEP, now + HORIZON + 1, STEP))
        weather = self.getWeather(route['o_lat'], route['o_lon'])

        # previsao em cache que ja nao cobre o horizonte viraria chuva constante no fim da serie
        if weather is None or weather['ts'].max() < ts[-1] or not model.ready():
            return None

        rain     = np.interp(ts, weather['ts'], weather['rain'])
        price, minutes = [float(value[0]) for value in getMarket(route, [now], rain[:1])]
        midnight = pd.Timestamp(getClock([now], route['tz'])[2][0]).tz_localize(route['tz'], nonexistent='shift_forward').timestamp()
        today    = database.get('SELECT ts, rain, price, minutes FROM Prices WHERE route_id = ? AND ts >= ? AND ts < ? ORDER BY ts', (route['id'], int(midnight), now // STEP * STEP))
        obs      = pd.concat([today, pd.DataFrame({'ts': [now], 'rain': [rain[0]], 'price': [price], 'minutes': [minutes]})], ignore_index=True).astype({'ts': 'int64', 'rain': 'float64', 'price': 'float64', 'minutes': 'float64'})
        grid     = pd.DataFrame({'ts': ts, 'rain': rain})
        blank    = [np.nan] * (len(ts) - 1)
        return pd.concat([grid, model.get(route, grid, obs)], axis=1).assign(probability=np.interp(ts, weather['ts'], weather['probability']), price=[price] + blank, minutes=[minutes] + blank)

    # CONSULTA COMPLETA: VALIDA OS ENDERECOS, TRACA A ROTA E PREVE; QUANDO UMA ETAPA FALHA DEVOLVE O MOTIVO PARA A TELA
    def getQuote(self, src, dst, now=None):
        if not model.ready():
            return {'error': 'Modelo em preparação: a previsão aparece assim que o primeiro treino terminar.'}

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

        df = self.get(route, now)

        if df is None:
            return {'error': 'Previsão do tempo indisponível (Open-Meteo), tente novamente em instantes.'}

        return {'src': src, 'dst': dst, 'route': route, 'df': df}


engine = Engine()
