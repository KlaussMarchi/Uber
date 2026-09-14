import logging, queue
import customtkinter as ctk
from concurrent.futures import ThreadPoolExecutor
from time import strftime
from Api.index import api
from Engine.index import engine
from Model.index import model
from Worker.index import worker
from Interface.Chart.index import Chart
from Interface.Locator.index import Locator
from Interface.Search.index import Search
from Utils.functions import getDelay, getDuration, getLocal, getMoney, getSpan
from Utils.sound import play
from Utils.variables import COLORS, FONT


ctk.set_appearance_mode('dark')


# JANELA PRINCIPAL; REDE E MODELO RODAM NO POOL E SO OS CALLBACKS NA THREAD DO TK TOCAM OS WIDGETS
class Interface(ctk.CTk):
    POLL   = 50       # ms entre leituras da fila de resultados das threads
    STATUS = 1000     # ms entre atualizacoes da barra de status
    SYNC   = 30000    # ms entre observacoes do preco em tempo real
    ALERT  = 0.10     # cada 10% de afastamento desde a primeira consulta toca um alerta; no mesmo patamar nao repete
    SIDE   = 430      # px da coluna de entrada
    STATS  = [('distance', 'Distância'), ('corridor', 'Trecho na RJ-106'), ('duration', 'Tempo sem trânsito'), ('minutes', 'Tempo com trânsito agora'), ('rain', 'Chuva agora'), ('worst', 'Pior trânsito na janela'), ('peak', 'Pico de preço na janela'), ('low', 'Menor preço na janela')]

    def __init__(self):
        super().__init__(fg_color=COLORS['background'])
        self.title('Tarifa Dinâmica')
        self.geometry('1400x880')
        self.minsize(1120, 780)
        self.tasks     = queue.Queue()
        self.pool      = ThreadPoolExecutor(max_workers=4, thread_name_prefix='ui')
        self.route     = None
        self.locator   = None
        self.df        = None
        self.busy      = False
        self.syncing   = False
        self.baselines = {}
        self.levels    = {}
        self.jobs      = {}
        self.stats     = {}

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color='transparent')
        header.grid(row=0, column=0, columnspan=2, sticky='ew', padx=24, pady=(18, 12))
        ctk.CTkLabel(header, text='Tarifa Dinâmica', font=ctk.CTkFont(FONT, 24, 'bold'), text_color=COLORS['text']).pack(side='left')
        ctk.CTkLabel(header, text='previsão de preço de corrida por aplicativo', font=ctk.CTkFont(FONT, 13), text_color=COLORS['muted']).pack(side='left', padx=(12, 0), pady=(6, 0))
        self.status = ctk.CTkLabel(header, text='', font=ctk.CTkFont(FONT, 12), text_color=COLORS['secondary'])
        self.status.pack(side='right', pady=(6, 0))
        self.dot = ctk.CTkLabel(header, text='●', font=ctk.CTkFont(FONT, 12), text_color=COLORS['muted'])
        self.dot.pack(side='right', padx=(0, 8), pady=(6, 0))

        side = ctk.CTkFrame(self, width=self.SIDE, fg_color=COLORS['card'], corner_radius=16)
        side.grid(row=1, column=0, sticky='ns', padx=(24, 12), pady=(0, 24))
        side.pack_propagate(False)

        self.origin = Search(side, 'ORIGEM', 'Endereço de partida', self.getForecast, lambda: self.getLocation(self.origin))
        self.origin.pack(fill='x', padx=22, pady=(20, 0))
        self.destination = Search(side, 'DESTINO', 'Endereço de destino', self.getForecast, lambda: self.getLocation(self.destination))
        self.destination.pack(fill='x', padx=22, pady=(12, 0))
        self.button = ctk.CTkButton(side, text='Calcular preço', height=44, corner_radius=10, font=ctk.CTkFont(FONT, 15, 'bold'), fg_color=COLORS['button'], hover_color=COLORS['pressed'], text_color='#FFFFFF', text_color_disabled=COLORS['secondary'], command=self.getForecast)
        self.button.pack(fill='x', padx=22, pady=(16, 0))
        self.message = ctk.CTkLabel(side, text='', height=32, font=ctk.CTkFont(FONT, 12), text_color=COLORS['muted'], wraplength=self.SIDE - 44, justify='left', anchor='w')
        self.message.pack(fill='x', padx=22, pady=(4, 0))

        top = ctk.CTkFrame(side, fg_color='transparent')
        top.pack(fill='x', padx=22, pady=(4, 0))
        ctk.CTkLabel(top, text='PREÇO AGORA', height=22, font=ctk.CTkFont(FONT, 12, 'bold'), text_color=COLORS['muted'], anchor='w').pack(side='left')
        ctk.CTkLabel(top, text=f'tempo real · {self.SYNC // 1000} s', height=22, font=ctk.CTkFont(FONT, 12), text_color=COLORS['muted']).pack(side='right')
        ctk.CTkLabel(top, text='●', height=22, font=ctk.CTkFont(FONT, 12), text_color=COLORS['success']).pack(side='right', padx=(0, 6))

        self.price = ctk.CTkLabel(side, text='—', font=ctk.CTkFont(FONT, 40, 'bold'), text_color=COLORS['text'], anchor='w')
        self.price.pack(fill='x', padx=22)
        self.change = ctk.CTkLabel(side, text='', height=20, font=ctk.CTkFont(FONT, 13, 'bold'), text_color=COLORS['muted'], anchor='w')
        self.change.pack(fill='x', padx=22)
        self.range = ctk.CTkLabel(side, text='', height=20, font=ctk.CTkFont(FONT, 12), text_color=COLORS['secondary'], anchor='w')
        self.range.pack(fill='x', padx=22)

        grid = ctk.CTkFrame(side, fg_color='transparent')
        grid.pack(fill='x', padx=17, pady=(14, 0))
        grid.grid_columnconfigure((0, 1), weight=1, uniform='stats')

        for i, (key, title) in enumerate(self.STATS):
            cell = ctk.CTkFrame(grid, fg_color=COLORS['input'], corner_radius=10)
            cell.grid(row=i // 2, column=i % 2, sticky='ew', padx=5, pady=5)
            ctk.CTkLabel(cell, text=title, height=16, font=ctk.CTkFont(FONT, 11), text_color=COLORS['muted'], anchor='w').pack(fill='x', padx=12, pady=(6, 0))
            self.stats[key] = ctk.CTkLabel(cell, text='—', height=24, font=ctk.CTkFont(FONT, 15, 'bold'), text_color=COLORS['text'], anchor='w')
            self.stats[key].pack(fill='x', padx=12, pady=(0, 6))

        ctk.CTkLabel(side, text='Preços do oráculo sintético calibrado; endereços (OpenStreetMap), rotas (OSRM) e clima (Open-Meteo) são dados reais.', font=ctk.CTkFont(FONT, 11), text_color=COLORS['muted'], wraplength=self.SIDE - 44, justify='left', anchor='w').pack(side='bottom', fill='x', padx=22, pady=(0, 16))

        self.chart = Chart(self, self.showWindow)
        self.chart.grid(row=1, column=1, sticky='nsew', padx=(12, 24), pady=(0, 24))

        self.protocol('WM_DELETE_WINDOW', self.stop)
        self.handle()
        self.showStatus()
        self.jobs['sync'] = self.after(self.SYNC, self.handleTick)

    # TRABALHO DE REDE OU DE MODELO FORA DA THREAD DO TK; O CALLBACK VOLTA PELA FILA E RODA NA THREAD DA JANELA
    def submit(self, work, cb):
        self.pool.submit(work).add_done_callback(lambda future: self.tasks.put(lambda: cb(future.result())))

    def handle(self):
        self.jobs['handle'] = self.after(self.POLL, self.handle)

        while not self.tasks.empty():
            self.tasks.get_nowait()()

    # LE OS CAMPOS NA THREAD DA JANELA E PEDE AO ENGINE A CONSULTA COMPLETA EM SEGUNDO PLANO
    def getForecast(self):
        src, dst = self.origin.get(), self.destination.get()

        if self.busy:
            return

        if not src['label'] or not dst['label']:
            return self.message.configure(text='Preencha origem e destino.', text_color=COLORS['error'])

        self.busy = True
        self.button.configure(state='disabled', text='Calculando…')
        self.message.configure(text='')
        self.submit(lambda: engine.getQuote(src, dst), self.showQuote)

    # BOTAO DE LOCALIZACAO DO CAMPO: ESTIMA POR WI-FI OU IP E ABRE O MAPA PARA CONFIRMAR O PONTO EXATO
    def getLocation(self, field):
        if self.locator is not None and self.locator.winfo_exists():
            return self.locator.focus()

        field.button.configure(state='disabled')
        self.message.configure(text='Localizando…', text_color=COLORS['muted'])
        self.submit(api.locate, lambda place: self.showLocation(field, place))

    # TEMPO REAL SEMPRE LIGADO: A CADA 30 S OBSERVA DE NOVO O PRECO DA ROTA NA TELA
    def handleTick(self):
        self.jobs['sync'] = self.after(self.SYNC, self.handleTick)
        route = self.route

        if route is None or self.busy or self.syncing:
            return

        self.syncing = True
        self.submit(lambda: engine.get(route), lambda df: self.showTick(route, df))

    # RESPOSTA DA CONSULTA DO USUARIO: MOTIVO DO ERRO OU ROTA VALIDADA E PRECO DE REFERENCIA DA SESSAO
    def showQuote(self, res):
        self.busy = False
        self.button.configure(state='normal', text='Calcular preço')

        if 'error' in res:
            return self.message.configure(text=res['error'], text_color=COLORS['error'])

        self.origin.set(res['src'])
        self.destination.set(res['dst'])
        self.route = res['route']
        self.baselines.setdefault(self.route['id'], (float(res['df']['price'][0]), strftime('%H:%M')))
        self.levels.setdefault(self.route['id'], 0)
        self.showForecast(self.route, res['df'])
        worker.wake()

    # NOVA OBSERVACAO DO TEMPO REAL: REDESENHA E CONFERE OS ALERTAS
    def showTick(self, route, df):
        self.syncing = False

        if df is None or route is not self.route:
            return

        self.showForecast(route, df)
        self.check(route, float(df['price'][0]))

    # PRECO OBSERVADO EM DESTAQUE COM A VARIACAO DESDE A PRIMEIRA CONSULTA, RESUMO DA JANELA E SERIE NO GRAFICO
    def showForecast(self, route, df):
        if df is None or route is not self.route:
            return

        self.df = df
        now     = df.iloc[0]
        view    = df[df['ts'] <= df['ts'][0] + self.chart.hours * 3600]
        peak    = view.loc[view['p50'].idxmax()]
        low     = view.loc[view['p50'].idxmin()]
        base, since = self.baselines[route['id']]
        change  = now['price'] / base - 1
        way     = 0 if abs(change) < 0.0005 else -1 if change < 0 else 1    # abaixo de 0,05% a variacao some no arredondamento mostrado
        color   = {0: COLORS['text'], -1: COLORS['price'], 1: COLORS['error']}[way]
        arrow   = {0: '•', -1: '▼', 1: '▲'}[way]

        self.price.configure(text=getMoney(now['price']), text_color=color)
        self.change.configure(text=f"{arrow} {abs(change):.1%} desde {since} · atualizado {strftime('%H:%M:%S')}".replace('.', ','), text_color=color)
        self.range.configure(text=f"próximos 10 min entre {getMoney(df['p10'][1])} e {getMoney(df['p90'][1])} · viagem de {getSpan(df['m10'][1], df['m90'][1])}")

        worst  = view.loc[view['m50'].idxmax()]
        values = {
            'distance': f"{route['distance']:.1f} km".replace('.', ','),
            'corridor': f"{route['corridor']:.0%}",
            'duration': getDuration(route['duration']),
            'minutes':  f"{getDuration(now['minutes'])} · {getDelay(now['minutes'] - route['duration'])}",
            'rain':     f"{now['rain']:.1f} mm/h · {now['probability']:.0f}%".replace('.', ','),
            'worst':    f"{getDuration(worst['m50'])} · {getLocal([worst['ts']], route['tz'])[0]:%H:%M}",
            'peak':     f"{getMoney(peak['p50'])} · {getLocal([peak['ts']], route['tz'])[0]:%H:%M}",
            'low':      f"{getMoney(low['p50'])} · {getLocal([low['ts']], route['tz'])[0]:%H:%M}",
        }

        for key, value in values.items():
            self.stats[key].configure(text=value)

        self.chart.plot(df, route)

    # NOVA JANELA ESCOLHIDA NO GRAFICO: REDESENHA COM OS MESMOS DADOS, SEM CONSULTAR NADA
    def showWindow(self):
        if self.route is not None:
            self.showForecast(self.route, self.df)

    # ESTIMATIVA PRONTA OU INDISPONIVEL: O MAPA ABRE DE QUALQUER JEITO (NA ESTIMATIVA, NUM LUGAR JA USADO OU NA REGIAO) PARA CONFIRMAR O PONTO EXATO
    def showLocation(self, field, place):
        field.button.configure(state='normal')
        self.message.configure(text='Confirme o ponto no mapa ou escolha um lugar já usado.' if place else 'Localização indisponível: marque o ponto no mapa.', text_color=COLORS['muted'])
        self.locator = Locator(self, place, lambda chosen: self.showChoice(field, chosen))

    # PONTO ESCOLHIDO NO MAPA OU NA LISTA: VIRA O ENDERECO DO CAMPO
    def showChoice(self, field, place):
        field.set(place)
        self.message.configure(text=f"Ponto definido: {place['label']}", text_color=COLORS['muted'])

    # ESTADO DO SERVICO EM SEGUNDO PLANO NA BARRA DE TITULO
    def showStatus(self):
        self.jobs['status'] = self.after(self.STATUS, self.showStatus)
        self.status.configure(text=worker.status)
        self.dot.configure(text_color=COLORS['success'] if model.ready() else COLORS['muted'])

    def check(self, route, price):
        base  = self.baselines[route['id']][0]
        level = self.levels[route['id']]
        band  = int((price / base - 1) / self.ALERT)
        kind  = 'good' if band < min(level, 0) else 'bad' if band > max(level, 0) else None

        if kind is not None and not play(kind):
            self.bell()

        self.levels[route['id']] = band

    # ERRO EM CALLBACK DO TK VAI PARA O LOG DO APP EM VEZ DE SUMIR NO TERMINAL
    def report_callback_exception(self, exc, val, tb):
        logging.error('erro em callback da interface', exc_info=(exc, val, tb))

    def stop(self):
        for job in self.jobs.values():
            self.after_cancel(job)

        self.pool.shutdown(wait=False, cancel_futures=True)
        self.destroy()
