import logging, subprocess, threading, requests
import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
from time import time, sleep
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from Utils.functions import getDistance


# CLIENTE DOS SERVICOS ABERTOS (PHOTON, NOMINATIM, OSRM, OPEN-METEO, BEACONDB) COM FILA POR SERVIDOR E RETENTATIVA EM FALHA 5XX
class Api:
    PHOTON    = 'https://photon.komoot.io/api/'
    REVERSE   = 'https://photon.komoot.io/reverse'
    NOMINATIM = 'https://nominatim.openstreetmap.org/search'
    OSRM      = ('https://router.project-osrm.org/route/v1/driving/', 'https://routing.openstreetmap.de/routed-car/route/v1/driving/')    # demonstracao do projeto e espelho da FOSSGIS com o mesmo mapa; o segundo so entra quando o primeiro falha
    FORECAST  = 'https://api.open-meteo.com/v1/forecast'
    ARCHIVE   = 'https://archive-api.open-meteo.com/v1/archive'
    BEACONDB  = 'https://api.beacondb.net/v1/geolocate'

    # provedores gratuitos de posicao por IP, sem cadastro, e onde cada um guarda a coordenada; a mediana protege contra um deles errar centenas de km
    ADDRESSES = {
        'https://get.geojs.io/v1/ip/geo.json': lambda res: (res.get('latitude'), res.get('longitude')),
        'https://ipwho.is/':                   lambda res: (res.get('latitude'), res.get('longitude')),
        'https://ipinfo.io/json':              lambda res: (res.get('loc') or ',').split(',')[:2],
    }
    AGENT     = 'TarifaDinamica/1.0 (desktop app em Python)'
    TIMEOUT   = 12
    CORRIDOR  = ('Rodovia Amaral Peixoto', 'RJ-106')
    BIAS      = {'lat': -22.43, 'lon': -41.85, 'zoom': 12}    # prioriza a regiao de Macae e Rio das Ostras sem esconder o resto do pais
    BRAZIL    = '-74.0,-33.8,-34.7,5.3'
    MAX_SNAP  = 2000     # m do ponto pedido ate a via mais proxima; acima disso nao ha acesso de carro (ilha, mar)
    STREET    = 1000     # m de precisao abaixo da qual a localizacao atual vira endereco de rua, e nao so cidade
    IP_ERROR  = 5000     # m de erro que a posicao por IP tem no melhor caso: ela aponta o bairro do provedor, nunca a rua

    # segundos entre requisicoes por servidor, conforme as politicas de uso publicas
    INTERVALS = {'nominatim.openstreetmap.org': 1.1, 'router.project-osrm.org': 1.1, 'routing.openstreetmap.de': 1.1, 'photon.komoot.io': 0.3}

    def __init__(self):
        self.session = requests.Session()
        self.session.headers['User-Agent'] = self.AGENT
        self.session.mount('https://', HTTPAdapter(max_retries=Retry(total=2, backoff_factor=0.8, status_forcelist=(500, 502, 503, 504), allowed_methods=('GET',))))
        self.slots = {}
        self.lock  = threading.Lock()

    def wait(self, host):
        with self.lock:
            slot = max(time(), self.slots.get(host, 0))
            self.slots[host] = slot + self.INTERVALS.get(host, 0)

        sleep(max(0, slot - time()))

    def get(self, url, params, body=None):
        host = url.split('/')[2]
        self.wait(host)

        try:
            res = self.session.get(url, params=params, timeout=self.TIMEOUT) if body is None else self.session.post(url, json=body, timeout=self.TIMEOUT)
            res.raise_for_status()
            return res.json()
        except (requests.RequestException, ValueError) as err:
            logging.warning(f'falha ao consultar {host}: {err}')
            return None

    # SUGESTOES DO AUTOCOMPLETE; O PHOTON CASA PREFIXOS ("RUA PROFESSOR ANT") E ACEITA BUSCA POR TECLA, O NOMINATIM NAO
    def search(self, text, limit=6):
        res = self.get(self.PHOTON, {'q': text, 'limit': limit * 2, 'bbox': self.BRAZIL, **self.BIAS})

        if res is None:
            return None

        places = [self.getPlace(item) for item in res.get('features', []) if item['properties'].get('countrycode') == 'BR']
        return list({place['label']: place for place in places}.values())[:limit]

    # VALIDA TEXTO DIGITADO SEM ESCOLHER SUGESTAO; E UMA CONSULTA UNICA, ENTAO O NOMINATIM PODE ENTRAR COMO SEGUNDA FONTE
    def geocode(self, text):
        places = self.search(text, limit=1)

        if places:
            return places[0]

        res = self.get(self.NOMINATIM, {'q': text, 'format': 'jsonv2', 'limit': 1, 'countrycodes': 'br'})

        if not res:
            return None

        return {'label': ', '.join(res[0]['display_name'].split(', ')[:3]), 'lat': float(res[0]['lat']), 'lon': float(res[0]['lon'])}

    # POSICAO ATUAL SEM GPS: AS REDES WI-FI VISIVEIS E OS PROVEDORES DE IP AO MESMO TEMPO; VALE A FONTE MAIS PRECISA, QUE E O WI-FI ONDE O BEACONDB TEM COBERTURA
    def locate(self):
        with ThreadPoolExecutor(2) as pool:
            found = [place.result() for place in (pool.submit(self.getBeacon), pool.submit(self.getAddress))]

        found = [place for place in found if place is not None]

        if not found:
            return None

        place = min(found, key=lambda item: item['accuracy'])
        return {**self.reverse(place['lat'], place['lon'], place['accuracy'] <= self.STREET), 'accuracy': place['accuracy']}

    # POSICAO PELAS REDES WI-FI VISIVEIS (NMCLI) NO BEACONDB, QUE E ABERTO E DEVOLVE A PRECISAO EM METROS; SEM VARREDURA NA BASE ELE CAI PARA O IP E DECLARA DEZENAS DE KM
    def getBeacon(self):
        try:
            scan = subprocess.run(['nmcli', '-t', '-f', 'BSSID,SIGNAL', 'device', 'wifi', 'list'], capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.TimeoutExpired):
            scan = ''

        # sinal do nmcli em % vira dBm pela escala do NetworkManager (% = 2 * (dBm + 100))
        networks = [line.replace('\\:', ':').rsplit(':', 1) for line in scan.splitlines() if line.count('\\:') == 5]
        body     = {'wifiAccessPoints': [{'macAddress': bssid.lower(), 'signalStrength': int(signal) // 2 - 100} for bssid, signal in networks]} if networks else {}
        res      = self.get(self.BEACONDB, {}, body)

        if res is None or 'location' not in res:
            return None

        return {'lat': res['location']['lat'], 'lon': res['location']['lng'], 'accuracy': res.get('accuracy', 0) or self.IP_ERROR}

    # POSICAO PELO IP: A MEDIANA DOS PROVEDORES QUE RESPONDEREM, COM A DISPERSAO ENTRE ELES COMO ERRO DECLARADO
    def getAddress(self):
        with ThreadPoolExecutor(len(self.ADDRESSES)) as pool:
            found = [point for point in pool.map(self.getCoords, self.ADDRESSES) if point is not None]

        if not found:
            return None

        place  = {'lat': float(np.median([point[0] for point in found])), 'lon': float(np.median([point[1] for point in found]))}
        spread = max(getDistance(place, {'lat': lat, 'lon': lon}) for lat, lon in found) * 1000
        return {**place, 'accuracy': float(max(spread, self.IP_ERROR))}

    # COORDENADA DE UM PROVEDOR DE IP; RESPOSTA AUSENTE, FORA DO GLOBO OU NO PONTO NULO NAO CONTA
    def getCoords(self, url):
        try:
            lat, lon = (float(value) for value in self.ADDRESSES[url](self.get(url, {}) or {}))
        except (AttributeError, TypeError, ValueError):    # provedor que muda o formato da resposta nao derruba a localizacao
            return None

        return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 and (lat or lon) else None

    # ENDERECO DE UMA COORDENADA (PHOTON REVERSO); COM ESTIMATIVA GROSSEIRA MOSTRA SO A CIDADE, PARA NAO SUGERIR UMA RUA ERRADA
    def reverse(self, lat, lon, street=True):
        found = (self.get(self.REVERSE, {'lat': lat, 'lon': lon, 'limit': 1}) or {}).get('features') or []
        props = found[0]['properties'] if found else {}
        label = self.getPlace(found[0])['label'] if found and street else ', '.join(filter(None, [props.get('city'), props.get('state')]))
        return {'label': label or f'{lat:.5f}, {lon:.5f}', 'lat': lat, 'lon': lon}

    # ROTULO LEGIVEL DE UM RESULTADO DO PHOTON: NOME, RUA E NUMERO, BAIRRO, CIDADE E ESTADO, SEM REPETIR PARTES
    def getPlace(self, item):
        props    = item['properties']
        lon, lat = item['geometry']['coordinates']
        street   = ', '.join(filter(None, [props.get('street'), props.get('housenumber')]))
        parts    = [props.get('name'), street, props.get('district'), props.get('city'), props.get('state')]
        return {'label': ', '.join(dict.fromkeys(filter(None, parts))), 'lat': lat, 'lon': lon}

    # ROTA DE CARRO NO OSRM (COM O ESPELHO COMO RESERVA) E A FRACAO NA RODOVIA AMARAL PEIXOTO (RJ-106); PONTO LONGE DE QUALQUER VIA (ILHA, MAR) NAO TEM ROTA
    def getRoute(self, src, dst):
        path = f"{src['lon']},{src['lat']};{dst['lon']},{dst['lat']}"
        res  = next(filter(None, (self.get(url + path, {'overview': 'false', 'steps': 'true'}) for url in self.OSRM)), None)

        if res is None or res.get('code') != 'Ok' or not res.get('routes') or max(point['distance'] for point in res['waypoints']) > self.MAX_SNAP:
            return None

        route = res['routes'][0]
        steps = [step for leg in route['legs'] for step in leg['steps']]
        onCorridor = sum(step['distance'] for step in steps if any(key in f"{step.get('name', '')} {step.get('ref', '')}" for key in self.CORRIDOR))
        return {'distance': route['distance'] / 1000, 'duration': route['duration'] / 60, 'corridor': onCorridor / max(route['distance'], 1.0)}

    # FUSO IANA DE UMA COORDENADA; O BRASIL TEM QUATRO FUSOS E A DEMANDA SEGUE A HORA LOCAL DA ROTA
    def getZone(self, lat, lon):
        res = self.get(self.FORECAST, {'latitude': lat, 'longitude': lon, 'timezone': 'auto'})
        return None if res is None else res.get('timezone')

    # CHUVA HORARIA (MM/H) E CHANCE DE CHUVA (%) EM UNIX TIME; COM DATA INICIAL LE O ARQUIVO HISTORICO, QUE NAO TEM CHANCE, SENAO A PREVISAO
    def getWeather(self, lat, lon, **period):
        archive = 'start_date' in period
        res     = self.get(self.ARCHIVE if archive else self.FORECAST, {'latitude': lat, 'longitude': lon, 'hourly': 'precipitation' if archive else 'precipitation,precipitation_probability', 'timeformat': 'unixtime', **period})

        if res is None or 'hourly' not in res:
            return None

        # o valor horario e a soma da hora anterior, entao a taxa e a chance valem no meio dela
        hourly = res['hourly']
        df     = pd.DataFrame({'ts': pd.Series(hourly['time']) - 1800, 'rain': hourly['precipitation'], 'probability': hourly.get('precipitation_probability', float('nan'))}).dropna(subset='rain')
        return df if len(df) else None


api = Api()
