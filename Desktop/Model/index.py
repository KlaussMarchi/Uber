import json, os, logging, threading
import numpy as np
import pandas as pd
import lightgbm as lgb
from time import time
from Oracle.index import getPrice, getTariff
from Utils.functions import getClock
from Utils.variables import COMPANIES, MODEL_PATH, STEP, HORIZON


# QUANTIS (LIGHTGBM) DO PRECO E DO TEMPO DE VIAGEM DE CADA APLICATIVO, ANCORADOS NO QUE O MERCADO MOSTRA AGORA E NO NIVEL DO DIA, COM FAIXA CONFORMAL POR HORIZONTE
class Model:
    QUANTILES   = {'10': 0.10, '50': 0.50, '90': 0.90}
    TARGETS     = {'price': 'p', 'minutes': 'm'}          # coluna observada -> prefixo das colunas previstas
    FEATURES    = ('hour', 'weekday', 'distance', 'duration', 'corridor', 'rain', 'company')
    CATEGORIES  = ['weekday', 'company']
    PARAMS      = {'objective': 'quantile', 'learning_rate': 0.05, 'num_leaves': 31, 'min_data_in_leaf': 80, 'bagging_fraction': 0.8, 'bagging_freq': 1, 'seed': 7, 'deterministic': True, 'verbose': -1, 'num_threads': max(1, os.cpu_count() // 2)}
    ROUNDS      = 400
    MIN_ROWS    = 2000
    CALIBRATION = 0.20                                    # fracao dos blocos de dias locais reservada para ancora e conformal
    BLOCK       = 7                                       # dias por bloco sorteado; semanas inteiras deixam na calibracao os horizontes que atravessam a meia-noite
    CLIP        = 2.5                                     # desvios do residuo acima dos quais a observacao e choque (acidente), e nao nivel do dia
    RAIN_GRID   = np.array([0, 0.5, 1, 2, 3, 5, 8, 12])   # mm/h, grade do rearranjo monotono
    MIN_BAND    = 0.01                                    # largura minima da faixa, fracao da mediana
    LEADS       = HORIZON // STEP + 1                     # horizontes de 0 a 12 h em slots
    REGIMES     = (1, 2, 6)                               # observacoes de hoje que abrem cada regime: rota nova, rastreada ha menos de 1 h, rastreada
    WINDOW      = 3                                       # observacoes que representam o regime intermediario na calibracao
    DIGITS      = {'p': 2, 'm': 2}                        # centavos e centesimo de minuto, para a faixa nao empatar em viagem curta

    # o modelo aprende o multiplicador sobre a tarifa sem dinamica e sobre o tempo sem transito, que valem para rotas de qualquer tamanho
    SCALES = {
        'price':   lambda df, company: getTariff(df['distance'].to_numpy(), df['duration'].to_numpy(), 1.0, company),
        'minutes': lambda df, company: df['duration'].to_numpy(),
    }

    def __init__(self):
        self.path     = MODEL_PATH
        self.boosters = None
        self.state    = None
        self.version  = 0
        self.metrics  = {}
        self.lock     = threading.Lock()

    def setup(self):
        if not os.path.exists(self.path):
            return False

        try:
            with open(self.path) as file:
                data = json.load(file)

            boosters = {column: {key: lgb.Booster(model_str=text) for key, text in data['boosters'][column].items()} for column in self.TARGETS}
            state    = {column: {'lam': float(data['state'][column]['lam']), 'clip': float(data['state'][column]['clip']), 'beta': np.array(data['state'][column]['beta']), 'offsets': np.array(data['state'][column]['offsets'])} for column in self.TARGETS}
        except (OSError, ValueError, KeyError, TypeError, lgb.basic.LightGBMError) as err:
            logging.warning(f'modelo salvo ilegivel ou de outra versao, sera retreinado: {err}')
            return False

        if data.get('features') != list(self.FEATURES):
            logging.warning('modelo salvo com outros atributos, sera retreinado')
            return False

        if any(state[column]['beta'].shape != (self.LEADS, 4) or state[column]['offsets'].shape != (self.LEADS, len(self.REGIMES), 2) for column in self.TARGETS):
            logging.warning('modelo salvo com horizontes diferentes, sera retreinado')
            return False

        with self.lock:
            self.boosters, self.state = boosters, state

        self.version, self.metrics = data['version'], data['metrics']
        return True

    def ready(self):
        return self.boosters is not None

    # O HISTORICO GUARDA O ESTADO DO MERCADO, QUE VALE PARA OS DOIS APLICATIVOS; CADA UM VIRA UM BLOCO DE LINHAS COM O SEU PRECO E A SUA CATEGORIA
    def update(self, df):
        if len(df) < self.MIN_ROWS:
            return False

        df     = df.sort_values(['route_id', 'ts'], ignore_index=True)
        blocks = [df.assign(company=index, key=df['route_id'] * len(COMPANIES) + index, price=getPrice(df['distance'], df['minutes'], df['excess'], df['noise'], company)) for index, company in enumerate(COMPANIES)]
        frame  = pd.concat(blocks, ignore_index=True)
        weekday, hour, day = self.getClock(frame)
        X      = self.process(frame, weekday, hour)
        cal    = np.random.default_rng(self.PARAMS['seed']).random((day.max() - day.min()) // self.BLOCK + 1)[(day - day.min()) // self.BLOCK] < self.CALIBRATION
        boosters, state, mape = {}, {}, {}

        for column in self.TARGETS:
            y = np.concatenate([block[column].to_numpy() / self.SCALES[column](block, company) for block, company in zip(blocks, COMPANIES)])
            data = lgb.Dataset(X[~cal], y[~cal], categorical_feature=self.CATEGORIES)
            boosters[column] = {key: lgb.train({**self.PARAMS, 'alpha': alpha}, data, self.ROUNDS) for key, alpha in self.QUANTILES.items()}
            state[column], mape[column] = self.getCalibration(frame[cal], day[cal], y[cal], self.getQuantiles(boosters[column], X[cal]))

        with self.lock:
            self.boosters, self.state = boosters, state

        self.version += 1
        self.metrics  = {'rows': len(frame), 'trained_at': int(time()), **{column: {'lam': state[column]['lam'], 'mape': mape[column]} for column in self.TARGETS}}
        logging.info(f"modelo v{self.version}: {len(frame)} observacoes, erro p50 em 1 h de {mape['price'][6]:.2%} no preco e {mape['minutes'][6]:.2%} no tempo")
        return True

    def get(self, route, grid, obs, company='uber'):
        with self.lock:
            boosters, state = self.boosters, self.state

        if boosters is None:
            return None

        both = pd.concat([grid, obs], ignore_index=True).assign(distance=route['distance'], duration=route['duration'], corridor=route['corridor'], tz=route['tz'], company=list(COMPANIES).index(company))
        weekday, hour, day = self.getClock(both)
        X    = self.process(both, weekday, hour)
        n    = len(grid)
        lead = np.clip(np.ceil((grid['ts'].to_numpy() - obs['ts'].iloc[-1]) / STEP), 0, self.LEADS - 1).astype(int)
        same = day[:n] == day[-1]
        out  = {}

        for column, prefix in self.TARGETS.items():
            scale = self.SCALES[column](both, company)
            lo, mid, hi = self.getQuantiles(boosters[column], X)
            r      = np.log(obs[column].to_numpy(dtype=float) / scale[n:] / mid[n:])
            clip   = np.clip(r, -state[column]['clip'], state[column]['clip'])
            beta   = state[column]['beta'][lead]
            shift  = np.exp(beta[:, 0] + beta[:, 1] * clip[-1] + beta[:, 2] * (r[-1] - clip[-1]) + beta[:, 3] * clip.sum() / (len(r) + state[column]['lam']) * same)
            side   = state[column]['offsets'][lead, np.searchsorted(self.REGIMES, len(r), side='right') - 1]
            middle = mid[:n] * shift
            lower  = np.minimum(lo[:n] * shift - side[:, 0], middle * (1 - self.MIN_BAND / 2))
            upper  = np.maximum(hi[:n] * shift + side[:, 1], middle * (1 + self.MIN_BAND / 2))
            out.update({f'{prefix}{key}': value * scale[:n] for key, value in zip(self.QUANTILES, (lower, middle, upper))})

        return pd.DataFrame(out).round({f'{prefix}{key}': digits for prefix, digits in self.DIGITS.items() for key in self.QUANTILES})

    # NOS DIAS RESERVADOS: LAMBDA DO NIVEL DO DIA, ANCORA POR HORIZONTE (MQO SOBRE RESIDUO ATUAL, CHOQUE ACIMA DO CORTE E NIVEL DO DIA) E OFFSETS POR HORIZONTE E REGIME
    def getCalibration(self, df, day, y, quantiles):
        lo, mid, hi = quantiles
        frame  = pd.DataFrame({'route': df['key'].to_numpy(), 'ts': df['ts'].to_numpy(), 'day': day, 'r': np.log(y / mid)})
        r      = frame['r'].to_numpy()
        cut    = float(self.CLIP * np.sqrt(frame.groupby(['route', 'day'])['r'].var().mean()))
        frame['clip'] = np.clip(r, -cut, cut)
        group  = frame.groupby(['route', 'day'])['clip']
        within = group.var().mean()

        # razao entre variancia dentro do dia e variancia do nivel diario, base do encolhimento da media de hoje
        lam    = float(within / max(group.mean().var() - within / (86400 // STEP), 1e-9))
        k      = group.cumcount().to_numpy() + 1
        c      = frame['clip'].to_numpy()
        means  = [(c / (1 + lam), k >= 1), (group.rolling(self.WINDOW, min_periods=1).sum().to_numpy() / (np.minimum(k, self.WINDOW) + lam), k >= self.WINDOW), (group.cumsum().to_numpy() / (k + lam), k >= self.REGIMES[-1])]
        series = pd.DataFrame({'r': r, 'day': day, 'y': y, 'lo': lo, 'mid': mid, 'hi': hi}, index=pd.MultiIndex.from_arrays([frame['route'], frame['ts']]))
        beta, offsets, mape = [], [], []

        for h in range(self.LEADS):
            target = series.reindex(pd.MultiIndex.from_arrays([frame['route'], frame['ts'] + h * STEP]))
            valid  = target['r'].notna().to_numpy()
            same   = target['day'].to_numpy() == day
            rows   = [(np.column_stack([np.ones(len(r)), c, r - c, m * same]), mask & valid) for m, mask in means]
            b      = np.linalg.lstsq(np.vstack([A[mask] for A, mask in rows]), np.concatenate([target['r'].to_numpy()[mask] for _, mask in rows]), rcond=None)[0]
            side   = []
            error  = 0.0

            for A, mask in rows:
                shift = np.exp(A[mask] @ b)
                t     = target[mask]
                level = min(1.0, self.QUANTILES['90'] * (1 + 1 / mask.sum()))
                side.append([np.quantile(t['lo'] * shift - t['y'], level, method='higher'), np.quantile(t['y'] - t['hi'] * shift, level, method='higher')])
                error = np.mean(np.abs(t['mid'] * shift - t['y']) / t['y'])    # vale o ultimo regime, o da rota rastreada, que e o da barra de status

            beta.append(b)
            offsets.append(side)
            mape.append(float(error))

        return {'lam': lam, 'clip': cut, 'beta': np.array(beta), 'offsets': np.array(offsets)}, mape

    # QUANTIS DO MULTIPLICADOR REARRANJADOS NA GRADE DE CHUVA (MAIS CHUVA NUNCA BARATEIA NEM ACELERA) E ORDENADOS PARA NUNCA CRUZAREM
    def getQuantiles(self, boosters, X):
        n, k   = len(X), len(self.RAIN_GRID)
        grid   = X.loc[X.index.repeat(k)].assign(rain=np.tile(self.RAIN_GRID, n))
        rain   = np.clip(X['rain'].to_numpy(), 0, self.RAIN_GRID[-1])
        j      = np.clip(np.searchsorted(self.RAIN_GRID, rain, side='right') - 1, 0, k - 2)
        w      = (rain - self.RAIN_GRID[j]) / (self.RAIN_GRID[j + 1] - self.RAIN_GRID[j])
        rows   = np.arange(n)
        curves = [np.sort(boosters[key].predict(grid).reshape(n, k), axis=1) for key in self.QUANTILES]
        return np.sort([curve[rows, j] * (1 - w) + curve[rows, j + 1] * w for curve in curves], axis=0)

    # RELOGIO DE CADA LINHA NO FUSO DA SUA ROTA: DIA DA SEMANA COM FERIADO COMO DOMINGO, HORA E DIA LOCAL
    def getClock(self, df):
        weekday, hour, day = np.zeros(len(df), int), np.zeros(len(df)), np.zeros(len(df), 'datetime64[D]')

        for tz, ix in df.groupby('tz').indices.items():
            weekday[ix], hour[ix], day[ix] = getClock(df['ts'].to_numpy()[ix], tz)

        return weekday, hour, day.astype(np.int64)

    # AS COLUNAS SAEM NA ORDEM DE FEATURES, QUE E A ORDEM QUE O BOOSTER TREINADO ESPERA
    def process(self, df, weekday, hour):
        values = {'hour': hour, 'weekday': weekday, **df}
        return pd.DataFrame({key: np.asarray(values[key]) for key in self.FEATURES})

    def export(self):
        with self.lock:
            boosters, state = self.boosters, self.state

        tmp = self.path + '.tmp'

        with open(tmp, 'w') as file:
            json.dump({
                'version':  self.version,
                'features': list(self.FEATURES),
                'metrics':  self.metrics,
                'state':    {column: {'lam': state[column]['lam'], 'clip': state[column]['clip'], 'beta': state[column]['beta'].tolist(), 'offsets': state[column]['offsets'].tolist()} for column in self.TARGETS},
                'boosters': {column: {key: booster.model_to_string() for key, booster in boosters[column].items()} for column in self.TARGETS},
            }, file)

        os.replace(tmp, self.path)


model = Model()
