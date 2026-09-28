import re, json, html, time, logging, unicodedata, requests
import numpy as np
import pandas as pd
from datetime import date
from urllib.parse import unquote
from scipy.optimize import least_squares
import Oracle.index as oracle
from Oracle.index import DEMAND_SURGE, FREE, KM_REF, LONG, SPEED_REF, VOLUME, getProfile


SITEMAPS = [f'https://www.uber.com/dynamic_routes_route{index}-sitemap.xml' for index in ('', *range(2, 15))]
PAGE     = 'https://www.uber.com/global/pt-br/r/routes/{}/'
SEATS    = 'https://raw.githubusercontent.com/kelvins/municipios-brasileiros/main/csv/municipios.csv'    # sedes dos municipios (ibge), para rotear cada trecho
STATES   = 'https://raw.githubusercontent.com/kelvins/municipios-brasileiros/main/csv/estados.csv'
OSRM     = 'https://router.project-osrm.org/route/v1/driving/{},{};{},{}'
BROWSER  = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36', 'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8', 'Accept-Language': 'pt-BR,pt;q=0.9'}
DELAY    = 2.0       # s entre paginas da uber; o robots.txt libera /r/routes/ e a coleta e lenta de proposito
PAUSE    = 1.1       # s entre rotas do osrm, a politica do servidor publico
FOCUS    = ('rj', 260)    # estado do app e quantos trechos dele entram; os outros estados entram por cidade de origem
PER_CITY = (6, 2)    # trechos por cidade de origem com mais e com menos de 30 trechos publicados
LIMIT    = 720
SEED     = 7
MATCH    = (0.75, 1.35)    # razao entre o km medio da uber e o do osrm de sede a sede em que os dois descrevem a mesma viagem
RIDGE    = {'city': 4.0, 'state': 1.0, 'shape': 2.0, 'pace_city': 4.0, 'pace_state': 1.0}    # encolhimento de cidade para estado, estado para o pais e forma para a tabela publicada
PRIOR    = np.log([2.30, 1.55, 0.32, 0.30])    # tabela publicada da uberx no rio (bandeirada, km, minuto) e um palpite para o km a mais da viagem longa: so a proporcao; o nivel e livre
FLOOR    = 10.0      # r$ de tarifa minima; os trechos publicados quase nunca sao curtos o bastante para medi-la
FOLDS    = 10
Z90      = 1.2815515655446004    # quantil 90% da normal padrao
WEEK     = np.arange(0, 7 * 86400, 600)


def getSlug(text):
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'-+', '-', ''.join(ch if ch.isalnum() else '-' for ch in text)).strip('-')

# TRECHOS BRASILEIROS PUBLICADOS NO SITEMAP DA UBER, SORTEADOS POR CIDADE DE ORIGEM COM MAIS PESO NO ESTADO DO APP
def getSample(session):
    slugs = set()

    for url in SITEMAPS:
        text = session.get(url, timeout=60).text
        slugs |= set(re.findall(r'/global/pt-br/r/routes/([a-z0-9-]+-[a-z]{2}-br-to-[a-z0-9-]+-[a-z]{2}-br)/?<', text))
        time.sleep(1)

    rng    = np.random.default_rng(SEED)
    origin = lambda slug: slug.split('-br-to-')[0]
    focus  = sorted(slug for slug in slugs if origin(slug).endswith(f'-{FOCUS[0]}'))
    others = pd.Series(sorted(slugs - set(focus)))
    pick   = list(rng.choice(focus, min(FOCUS[1], len(focus)), replace=False))

    for _, items in sorted(others.groupby(others.map(origin)), key=lambda kv: -len(kv[1])):
        pick += list(rng.choice(items.to_numpy(), min(PER_CITY[0] if len(items) > 30 else PER_CITY[1], len(items)), replace=False))

        if len(pick) >= LIMIT:
            break

    return pick

# MEDIA REAL DO ULTIMO MES NUM TRECHO: KM E MINUTOS MEDIOS DAS VIAGENS E TARIFA MEDIA E NUMERO DE VIAGENS DA UBERX
def getPage(session, slug):
    time.sleep(DELAY)

    try:
        res = session.get(PAGE.format(slug), timeout=30)
    except requests.RequestException as err:
        logging.warning(f'falha na pagina {slug}: {err}')
        return None

    plain = re.sub(r'\s+', ' ', re.sub('<[^>]+>', ' ', html.unescape(res.text))).replace('\\n', ' ')
    km    = re.search(r'pelo app da Uber é de\s+([\d.,]+)\s*quil', plain)
    dur   = re.search(r'duração média desta viagem é de\s+([\d.,]+)\s*minuto', plain)
    found = re.search(r'%5B%7B%22avgFare%22', res.text)

    if res.status_code != 200 or not (km and dur and found):
        return None

    chunk    = res.text[found.start():found.start() + 20000]
    products = {item['productName']: item for item in json.loads(unquote(chunk[:chunk.find('%5D') + 3]))}
    number   = lambda text: float(text.replace('.', '').replace(',', '.'))

    if 'UberX' not in products:
        return None

    return {'slug': slug, 'fetched': str(date.today()), 'km': number(km.group(1)), 'min': number(dur.group(1)), 'uberx': float(products['UberX']['avgFare']), 'trips': int(products['UberX'].get('productTrips') or 0)}

# ROTA DE SEDE A SEDE NO OSRM, PARA COMPARAR O RITMO REAL DA UBER COM O TEMPO SEM TRANSITO QUE O APP RECEBE
def getRoute(session, src, dst):
    time.sleep(PAUSE)

    try:
        res = session.get(OSRM.format(src['longitude'], src['latitude'], dst['longitude'], dst['latitude']), params={'overview': 'false'}, timeout=30).json()
    except (requests.RequestException, ValueError) as err:
        logging.warning(f'falha no osrm: {err}')
        return None

    if res.get('code') != 'Ok':
        return None

    return res['routes'][0]['distance'] / 1000, res['routes'][0]['duration'] / 60

# COLETA COMPLETA: AMOSTRA DO SITEMAP, PAGINA DE CADA TRECHO, SEDES DO IBGE E ROTA DO OSRM
def getRoutes():
    session = requests.Session()
    session.headers.update(BROWSER)
    seats   = pd.read_csv(SEATS).merge(pd.read_csv(STATES, encoding='utf-8-sig')[['codigo_uf', 'uf']], on='codigo_uf')
    seats   = seats.assign(slug=seats['nome'].map(getSlug) + '-' + seats['uf'].str.lower() + '-br').drop_duplicates('slug').set_index('slug')
    rows    = []

    for slug in getSample(session):
        src, dst = slug.split('-br-to-')[0] + '-br', slug.split('-br-to-')[1]
        page     = getPage(session, slug)

        if page is None or src not in seats.index or dst not in seats.index:
            continue

        route = getRoute(session, seats.loc[src], seats.loc[dst])
        rows.append({**page, 'src': seats.loc[src, 'nome'], 'uf': seats.loc[src, 'uf'], 'lat': seats.loc[src, 'latitude'], 'lon': seats.loc[src, 'longitude'], 'o_km': route[0] if route else np.nan, 'o_min': route[1] if route else np.nan})

        if len(rows) % 50 == 0:
            logging.info(f'{len(rows)} trechos coletados')

    return pd.DataFrame(rows)

# RITMO DA SEMANA PONDERADO PELO VOLUME DE VIAGENS: E O QUE A MEDIA DO MES DA UBER MISTURA
def getWeek():
    weekday, hour = WEEK // 86400, WEEK % 86400 / 3600
    traffic, demand = getProfile(weekday, hour)
    weight = VOLUME + (traffic + demand) / 2
    return weight / weight.sum(), traffic, 1 + DEMAND_SURGE * demand

# RITMO REAL SOBRE O OSRM: LOG DA RAZAO ENTRE OS PACES POR ESTADO E CIDADE (ENCOLHIDOS) MAIS A INCLINACAO NO KM E NA VELOCIDADE DO OSRM
def getPaceFit(df, mask):
    rows   = df[mask]
    ratio  = np.log((rows['min'] / rows['km']) / (rows['o_min'] / rows['o_km']))
    cities = sorted(df['src_key'].unique())
    states = sorted(df['uf'].unique())
    X      = np.zeros((len(rows), 3 + len(states) + len(cities)))
    X[:, 0] = 1
    X[:, 1] = np.log(rows['km'] / KM_REF)
    X[:, 2] = np.log(rows['o_km'] / rows['o_min'] * 60 / SPEED_REF)
    X[np.arange(len(rows)), 3 + rows['uf'].map(states.index).to_numpy()] = 1
    X[np.arange(len(rows)), 3 + len(states) + rows['src_key'].map(cities.index).to_numpy()] = 1
    penalty = np.diag([0, 0, 0] + [RIDGE['pace_state']] * len(states) + [RIDGE['pace_city']] * len(cities))
    coef    = np.linalg.solve(X.T @ X + penalty, X.T @ ratio.to_numpy())
    return {'km': coef[1], 'speed': coef[2], 'national': coef[0], 'states': dict(zip(states, coef[3:3 + len(states)])), 'cities': dict(zip(cities, coef[3 + len(states):]))}

# RITMO PREVISTO DE UM TRECHO PELO AJUSTE; SEM OSRM VALE A INCLINACAO SO NO KM
def getPace(fit, rows):
    speed = np.log(rows['o_km'] / rows['o_min'] * 60 / SPEED_REF).fillna(0).to_numpy()
    log   = fit['national'] + rows['uf'].map(fit['states']).fillna(0).to_numpy() + rows['src_key'].map(fit['cities']).fillna(0).to_numpy() + fit['km'] * np.log(rows['km'] / KM_REF).to_numpy() + fit['speed'] * speed
    return np.clip(np.exp(log), *oracle.PACE)

# PRECO MEDIO DO MES QUE O MODELO DA A CADA TRECHO: TARIFA SEM DINAMICA NO RITMO DE CADA HORA, VEZES A DINAMICA, PONDERADA PELO VOLUME
def getAverage(shape, floor, km, minutes, pace):
    weight, traffic, surge = getWeek()
    free  = 1 + (pace - 1) * FREE
    ratio = (free[:, None] + (pace - free)[:, None] * traffic / (weight @ traffic)) / pace[:, None]
    base, perkm, permin, extra = np.exp(shape)
    price = np.maximum(floor, base + perkm * km[:, None] + permin * minutes[:, None] * ratio + extra * np.maximum(km - LONG, 0)[:, None]) * surge
    return price @ weight

# NIVEL POR CIDADE E ESTADO E FORMA DA TARIFA, ALTERNANDO: DADA A FORMA, OS NIVEIS SAO UMA CRISTA LINEAR; DADOS OS NIVEIS, A FORMA SAI DE MINIMOS QUADRADOS
def getPriceFit(df, mask, pace):
    rows    = df[mask]
    y       = np.log(rows['uberx'].to_numpy())
    w       = np.clip(np.log1p(rows['trips'].to_numpy()), 0.7, 6)
    cities  = sorted(df['src_key'].unique())
    states  = sorted(df['uf'].unique())
    X       = np.zeros((len(rows), 1 + len(states) + len(cities)))
    X[:, 0] = 1
    X[np.arange(len(rows)), 1 + rows['uf'].map(states.index).to_numpy()] = 1
    X[np.arange(len(rows)), 1 + len(states) + rows['src_key'].map(cities.index).to_numpy()] = 1
    penalty = np.diag([0] + [RIDGE['state']] * len(states) + [RIDGE['city']] * len(cities))
    shape   = PRIOR - PRIOR.mean()
    km, minutes = rows['km'].to_numpy(), rows['min'].to_numpy()

    for _ in range(8):
        base    = np.log(getAverage(shape, FLOOR, km, minutes, pace[mask]))
        coef    = np.linalg.solve(X.T @ (X * w[:, None]) + penalty, X.T @ (w * (y - base)))
        shape   = shape + coef[0]    # o nivel nacional entra na tarifa, que fica em reais, e o piso vale em todo o pais
        coef[0] = 0
        rest    = y - X @ coef
        cost    = lambda s: np.concatenate([np.sqrt(w) * (np.log(getAverage(s, FLOOR, km, minutes, pace[mask])) - rest), np.sqrt(RIDGE['shape']) * (s - PRIOR - (s - PRIOR).mean())])
        shape   = least_squares(cost, shape).x

    return {'shape': shape, 'national': coef[0], 'states': dict(zip(states, coef[1:1 + len(states)])), 'cities': dict(zip(cities, coef[1 + len(states):]))}

# PRECO MEDIO PREVISTO PARA TRECHOS FORA DO AJUSTE; CIDADE NOVA CAI NO NIVEL DO ESTADO
def getPrediction(fit, rows, pace):
    level = fit['national'] + rows['uf'].map(fit['states']).fillna(0).to_numpy() + rows['src_key'].map(fit['cities']).fillna(0).to_numpy()
    return np.exp(level) * getAverage(fit['shape'], FLOOR, rows['km'].to_numpy(), rows['min'].to_numpy(), pace)

# VALIDACAO CRUZADA: POR TRECHO (A CIDADE SEGUE CONHECIDA PELOS OUTROS TRECHOS DELA) E POR CIDADE (A CIDADE INTEIRA FICA DE FORA)
def getValidation(df, pace):
    rng    = np.random.default_rng(SEED)
    cities = dict(zip(sorted(df['src_key'].unique()), rng.integers(0, FOLDS, df['src_key'].nunique())))
    out    = {}

    for name, group in (('rota', rng.integers(0, FOLDS, len(df))), ('cidade', df['src_key'].map(cities).to_numpy())):
        price, ratio = np.zeros(len(df)), np.full(len(df), np.nan)

        for k in range(FOLDS):
            test = group == k
            fit  = getPriceFit(df, ~test, pace)
            pfit = getPaceFit(df, ~test & df['match'])
            rows = df[test] if name == 'rota' else df[test].assign(src_key='')    # cidade nova: so o estado e conhecido
            price[test] = getPrediction(fit, rows, pace[test])
            ratio[test] = getPace(pfit, rows)

        out[name] = {'price': np.log(price / df['uberx']), 'pace': np.log(ratio / ((df['min'] / df['km']) / (df['o_min'] / df['o_km'])))}

    return out

# TRECHOS REAIS VALIDOS COM A CHAVE DA CIDADE DE ORIGEM E SE O OSRM DESCREVE A MESMA VIAGEM
def getFrame(path):
    df = pd.read_csv(path)
    df = df[(df['km'] > 0) & (df['min'] > 0) & (df['uberx'] > 0)].reset_index(drop=True)
    return df.assign(src_key=df['src'] + '/' + df['uf'], match=(df['km'] / df['o_km']).between(*MATCH))

# TABELA DO APP (TARIFA SEM DINAMICA, RITMO E NIVEL DE CADA CIDADE) E OS ERROS DA VALIDACAO CRUZADA QUE VIRAM A INCERTEZA DELA
def getTable(df):
    pfit   = getPaceFit(df, df['match'])
    pace   = getPace(pfit, df)
    fit    = getPriceFit(df, np.ones(len(df), bool), pace)
    check  = getValidation(df, pace)
    robust = lambda e: float(np.nanquantile(np.abs(e), 0.8) / Z90)    # desvio que faz a faixa p10-p90 cobrir 80% dos erros fora do ajuste
    weight, traffic, _ = getWeek()
    places = df.groupby('src_key')[['src', 'uf', 'lat', 'lon']].first()

    market = {
        'updated': df['fetched'].max(),    # dia da coleta mais recente: e dele que as medias do ultimo mes falam
        'routes': len(df),
        'tariff': dict(zip(('base', 'km', 'minute', 'long'), np.exp(fit['shape']).round(4).tolist()), floor=FLOOR),
        'pace': {'km': round(pfit['km'], 4), 'speed': round(pfit['speed'], 4)},
        'traffic': round(float(weight @ traffic), 6),
        'week': oracle.TRAFFIC.tolist(),    # congestionamento da semana para o celular, que nao tem o oracle em python
        'sigma': {'city': round(robust(check['rota']['price']), 4), 'state': round(robust(check['cidade']['price']), 4), 'pace': round(robust(check['rota']['pace'][df['match']]), 4)},
        'national': [round(fit['national'], 4), round(pfit['national'], 4)],
        'states': {uf: [round(fit['national'] + fit['states'][uf], 4), round(pfit['national'] + pfit['states'].get(uf, 0), 4)] for uf in fit['states']},
        'cities': [[row.src, row.uf, round(row.lat, 4), round(row.lon, 4), round(fit['national'] + fit['states'][row.uf] + fit['cities'][key], 4), round(pfit['national'] + pfit['states'].get(row.uf, 0) + pfit['cities'].get(key, 0), 4)] for key, row in places.iterrows()],
    }

    return market, check
