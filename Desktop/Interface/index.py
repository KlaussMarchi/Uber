import logging, queue
import customtkinter as ctk
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from time import strftime, time
from Api.index import api
from Engine.index import engine
import Oracle.index as oracle
from Oracle.index import TARIFFS, getCalibration, getFree
from Worker.index import worker
from Interface.Chart.index import Chart
from Interface.Locator.index import Locator
from Interface.Search.index import Search
from Utils.functions import getChance, getDelay, getDuration, getLocal, getMoney, getSpan
from Utils.sound import play
from Utils.variables import COLORS, COMPANIES, FONT


ctk.set_appearance_mode('dark')


# JANELA PRINCIPAL; REDE E MODELO RODAM NO POOL E SO OS CALLBACKS NA THREAD DO TK TOCAM OS WIDGETS
class Interface(ctk.CTk):
    POLL   = 50       # ms entre leituras da fila de resultados das threads
    STATUS = 1000     # ms entre atualizacoes da barra de status
    SYNC   = 30000    # ms entre observacoes do preco em tempo real
    ALERT  = 0.10     # cada 10% de afastamento desde a primeira consulta toca um alerta; no mesmo patamar nao repete
    SIDE   = 430      # px da coluna de entrada
    STATS  = [('distance', 'Distância'), ('surge', 'Dinâmica estimada agora'), ('duration', 'Tempo sem trânsito'), ('minutes', 'Tempo com trânsito agora'), ('rain', 'Chuva agora'), ('worst', 'Pior trânsito na janela'), ('peak', 'Pico de preço na janela'), ('low', 'Menor preço na janela')]

    def __init__(self):
        super().__init__(fg_color=COLORS['background'])
        self.title('Tarifa Dinâmica')
        self.geometry('1400x900')
        self.minsize(1040, 660)    # cabe em tela de notebook de 768 px; o painel lateral rola quando falta altura
        self.tasks     = queue.Queue()
        self.pool      = ThreadPoolExecutor(max_workers=4, thread_name_prefix='ui')
        self.company   = next(iter(COMPANIES))
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
        logo = ctk.CTkLabel(header, text='Tarifa Dinâmica', font=ctk.CTkFont(FONT, 24, 'bold'), text_color=COLORS['text'])
        logo.pack(side='left')
        tagline = ctk.CTkLabel(header, text='previsão de preço de corrida por aplicativo', font=ctk.CTkFont(FONT, 13), text_color=COLORS['muted'])
        tagline.pack(side='left', padx=(12, 0), pady=(6, 0))
        self.status = ctk.CTkLabel(header, text='', font=ctk.CTkFont(FONT, 12), text_color=COLORS['secondary'], justify='right', anchor='e')
        self.status.pack(side='right', pady=(6, 0))
        header.bind('<Configure>', lambda event: self.status.configure(wraplength=max(240, event.width - logo.winfo_reqwidth() - tagline.winfo_reqwidth() - 60)))    # status longo quebra a linha em vez de cobrir o subtitulo
        self.dot = ctk.CTkLabel(header, text='●', font=ctk.CTkFont(FONT, 12), text_color=COLORS['muted'])
        self.dot.pack(side='right', padx=(0, 8), pady=(6, 0))

        side = ctk.CTkScrollableFrame(self, width=self.SIDE, fg_color=COLORS['card'], corner_radius=16, scrollbar_fg_color=COLORS['card'], scrollbar_button_color=COLORS['card'], scrollbar_button_hover_color=COLORS['card'])
        side.grid(row=1, column=0, sticky='ns', padx=(24, 12), pady=(0, 24))
        side.bind('<Configure>', self.showScroll, add='+')
        side.master.bind('<Configure>', self.showScroll, add='+')
        self.side = side

        self.selector = ctk.CTkSegmentedButton(side, values=list(COMPANIES.values()), height=38, corner_radius=10, font=ctk.CTkFont(FONT, 14, 'bold'), fg_color=COLORS['input'], selected_color=COLORS['button'], selected_hover_color=COLORS['pressed'], unselected_color=COLORS['input'], unselected_hover_color=COLORS['hover'], text_color=COLORS['text'], command=self.handleCompany)
        self.selector.set(COMPANIES[self.company])
        self.selector.pack(fill='x', padx=22, pady=(16, 0))

        self.origin = Search(side, 'ORIGEM', 'Endereço de partida', self.getForecast, lambda: self.getLocation(self.origin))
        self.origin.pack(fill='x', padx=22, pady=(12, 0))
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
        self.range = ctk.CTkLabel(side, text='', height=20, font=ctk.CTkFont(FONT, 12), text_color=COLORS['secondary'], wraplength=self.SIDE - 44, justify='left', anchor='w')
        self.range.pack(fill='x', padx=22)

        row = ctk.CTkFrame(side, fg_color='transparent')
        row.pack(fill='x', padx=22, pady=(8, 0))
        row.grid_columnconfigure(0, weight=1)

        self.fare = ctk.CTkEntry(row, height=34, corner_radius=8, border_width=1, font=ctk.CTkFont(FONT, 13), fg_color=COLORS['input'], border_color=COLORS['border'], text_color=COLORS['text'], placeholder_text='preço real no app', placeholder_text_color=COLORS['muted'])
        self.fare.grid(row=0, column=0, sticky='ew')
        self.fare.bind('<Return>', lambda event: self.handleFare())
        ctk.CTkButton(row, text='Calibrar', width=92, height=34, corner_radius=8, border_width=1, font=ctk.CTkFont(FONT, 13, 'bold'), fg_color=COLORS['input'], hover_color=COLORS['hover'], border_color=COLORS['border'], text_color=COLORS['text'], command=self.handleFare).grid(row=0, column=1, padx=(8, 0))

        self.hint = ctk.CTkLabel(side, text='', height=18, font=ctk.CTkFont(FONT, 11), text_color=COLORS['muted'], wraplength=self.SIDE - 44, justify='left', anchor='w')
        self.hint.pack(fill='x', padx=22, pady=(3, 0))

        grid = ctk.CTkFrame(side, fg_color='transparent')
        grid.pack(fill='x', padx=17, pady=(10, 0))
        grid.grid_columnconfigure((0, 1), weight=1, uniform='stats')

        for i, (key, title) in enumerate(self.STATS):
            cell = ctk.CTkFrame(grid, fg_color=COLORS['input'], corner_radius=10)
            cell.grid(row=i // 2, column=i % 2, sticky='ew', padx=5, pady=4)
            ctk.CTkLabel(cell, text=title, height=16, font=ctk.CTkFont(FONT, 11), text_color=COLORS['muted'], anchor='w').pack(fill='x', padx=12, pady=(6, 0))
            self.stats[key] = ctk.CTkLabel(cell, text='—', height=24, font=ctk.CTkFont(FONT, 15, 'bold'), text_color=COLORS['text'], anchor='w')
            self.stats[key].pack(fill='x', padx=12, pady=(0, 6))

        ctk.CTkLabel(side, text='Preço estimado pelas médias reais da Uber no último mês na região de origem; a 99, pela razão típica entre as duas. Informe o preço que o app mostra para ajustar. Endereços (OpenStreetMap), rotas (OSRM) e clima (Open-Meteo) são dados reais.', font=ctk.CTkFont(FONT, 11), text_color=COLORS['muted'], wraplength=self.SIDE - 44, justify='left', anchor='w').pack(fill='x', padx=22, pady=(12, 16))

        self.chart = Chart(self, self.showWindow)
        self.chart.grid(row=1, column=1, sticky='nsew', padx=(12, 24), pady=(0, 24))

        self.protocol('WM_DELETE_WINDOW', self.stop)
        self.showFare()
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
        self.submit(lambda: engine.getQuote(src, dst, self.company), self.showQuote)

    # RECALCULA A ROTA NA TELA NO APLICATIVO ESCOLHIDO; A RESPOSTA CARREGA O APLICATIVO DELA E E DESCARTADA SE O USUARIO JA TROCOU
    def getTick(self, route, company):
        self.submit(lambda: engine.get(route, company), lambda df: self.showTick(route, company, df))

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
        company = self.company
        self.submit(lambda: engine.get(route, company), lambda df: self.showTick(route, company, df, sync=True))

    # RESPOSTA DA CONSULTA DO USUARIO: MOTIVO DO ERRO OU ROTA VALIDADA E PRECO DE REFERENCIA DA SESSAO
    def showQuote(self, res):
        self.busy = False
        self.button.configure(state='normal', text='Calcular preço')

        if 'error' in res:
            return self.message.configure(text=res['error'], text_color=COLORS['error'])

        self.origin.set(res['src'])
        self.destination.set(res['dst'])
        self.route = res['route']
        self.showForecast(self.route, res['company'], res['df'])
        worker.wake()

        # aplicativo trocado enquanto a consulta corria: a resposta e de outra tarifa e a rota tem de ser refeita na escolhida
        if res['company'] != self.company:
            self.getTick(self.route, self.company)

    # NOVA OBSERVACAO DO TEMPO REAL: REDESENHA E CONFERE OS ALERTAS
    def showTick(self, route, company, df, sync=False):
        if sync:
            self.syncing = False

        if df is None or route is not self.route or company != self.company:
            return

        self.showForecast(route, company, df)
        self.check(route, company, float(df['price'][0]))

    # PRECO OBSERVADO EM DESTAQUE COM A VARIACAO DESDE A PRIMEIRA CONSULTA, RESUMO DA JANELA E SERIE NO GRAFICO
    def showForecast(self, route, company, df):
        if df is None or route is not self.route or company != self.company:
            return

        self.df = df
        now     = df.iloc[0]
        view    = df[df['ts'] <= df['ts'][0] + self.chart.hours * 3600]
        peak    = view.loc[view['p50'].idxmax()]
        low     = view.loc[view['p50'].idxmin()]
        key   = (route['id'], company)
        base, since = self.baselines.setdefault(key, (float(now['price']), strftime('%H:%M')))
        self.levels.setdefault(key, 0)
        change  = now['price'] / base - 1
        way     = 0 if abs(change) < 0.0005 else -1 if change < 0 else 1    # abaixo de 0,05% a variacao some no arredondamento mostrado
        color   = {0: COLORS['text'], -1: COLORS['price'], 1: COLORS['error']}[way]
        arrow   = {0: '•', -1: '▼', 1: '▲'}[way]

        self.price.configure(text=getMoney(now['price']), text_color=color)
        self.change.configure(text=f"{arrow} {abs(change):.1%} desde {since} · atualizado {strftime('%H:%M:%S')}".replace('.', ','), text_color=color)
        self.range.configure(text=f"próximos 10 min entre {getMoney(df['p10'][1])} e {getMoney(df['p90'][1])} · viagem de {getSpan(df['m10'][1], df['m90'][1])}")

        worst  = view.loc[view['m50'].idxmax()]
        free   = getFree(route)
        values = {
            'distance': f"{route['distance']:.1f} km".replace('.', ','),
            'surge':    f"{now['surge']:.2f}×".replace('.', ','),
            'duration': getDuration(free),
            'minutes':  f"{getDuration(now['minutes'])} · {getDelay(now['minutes'] - free)}",
            'rain':     f"{now['rain']:.1f}".replace('.', ',') + f" mm/h · {getChance(now['probability'])}",
            'worst':    f"{getDuration(worst['m50'])} · {getLocal([worst['ts']], route['tz'])[0]:%H:%M}",
            'peak':     f"{getMoney(peak['p50'])} · {getLocal([peak['ts']], route['tz'])[0]:%H:%M}",
            'low':      f"{getMoney(low['p50'])} · {getLocal([low['ts']], route['tz'])[0]:%H:%M}",
        }

        for key, value in values.items():
            self.stats[key].configure(text=value)

        self.chart.plot(df, route, COMPANIES[company])
        self.showFare()

    # NOVA JANELA ESCOLHIDA NO GRAFICO: REDESENHA COM OS MESMOS DADOS, SEM CONSULTAR NADA
    def showWindow(self):
        if self.route is not None:
            self.showForecast(self.route, self.company, self.df)

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
        self.dot.configure(text_color=COLORS['error'] if worker.status.startswith('Erro') else COLORS['success'])

    def check(self, route, company, price):
        key   = (route['id'], company)
        base  = self.baselines[key][0]
        level = self.levels[key]
        band  = int((price / base - 1) / self.ALERT)
        kind  = 'good' if band < min(level, 0) else 'bad' if band > max(level, 0) else None

        if kind is not None and not play(kind):
            self.bell()

        self.levels[key] = band

    # TROCA DE APLICATIVO: A ROTA NA TELA E RECALCULADA COM A TARIFA E A DINAMICA DO ESCOLHIDO
    def handleCompany(self, label):
        self.company = next(key for key, name in COMPANIES.items() if name == label)
        self.showFare()

        if self.route is None or self.busy:
            return

        self.getTick(self.route, self.company)

    # PRECO REAL LIDO NO APLICATIVO: AJUSTA A TARIFA DELE E RECALCULA A SERIE; ZERO APAGA A CALIBRACAO
    def handleFare(self):
        text = self.fare.get().strip().replace('R$', '').strip()
        text = text.replace('.', '').replace(',', '.') if ',' in text else text    # 1.234,56 e 1234.56 valem o mesmo

        if self.route is None:
            return self.message.configure(text='Calcule um preço antes de calibrar.', text_color=COLORS['error'])

        if not text.replace('.', '', 1).isdigit():
            return self.message.configure(text='Informe o preço que o aplicativo mostra agora, como 54,85 (0 apaga os seus preços).', text_color=COLORS['error'])

        route, company = self.route, self.company
        self.fare.delete(0, 'end')
        self.message.configure(text='Registrando o preço real…', text_color=COLORS['muted'])
        self.submit(lambda: engine.setFare(route, company, float(text)), lambda count: self.showCalibration(route, company, count))

    # TARIFA RECEM-AJUSTADA: A REFERENCIA DA VARIACAO RECOMECA, PORQUE O PRECO MUDOU DE NIVEL
    def showCalibration(self, route, company, count):
        if count is None:
            return self.message.configure(text='Não foi possível registrar agora: a previsão do tempo desta rota está indisponível.', text_color=COLORS['error'])

        self.baselines.pop((route['id'], company), None)
        self.levels.pop((route['id'], company), None)
        self.showFare()
        self.message.configure(text=f"Preço da {COMPANIES[company]} registrado: a previsão parte dele agora e a média da região se ajusta ({count} {'preço seu' if count == 1 else 'preços seus'})." if count else f'Preços informados da {COMPANIES[company]} apagados: voltou à média real da região.', text_color=COLORS['muted'])
        self.getTick(route, self.company)

    # DE ONDE VEM O PRECO EM USO, SOB O CAMPO DE PRECO REAL: A MEDIA REAL DA REGIAO E QUANTOS PRECOS INFORMADOS POR PERTO AJUSTAM ELA
    def showFare(self):
        name    = COMPANIES[self.company]
        updated = date.fromisoformat(oracle.MARKET['updated']).strftime('%d/%m/%Y')

        if self.route is None:
            return self.hint.configure(text=f"{name}: médias reais da Uber de {updated} em {oracle.MARKET['routes']} trechos; informe o preço do app para ajustar")

        calibration = getCalibration(self.route, self.company, time())
        market      = calibration['market']['market']
        ratio       = f"{TARIFFS[self.company]['ratio']:.2f}".replace('.', ',')
        base        = f'média real da UberX em {market}' if self.company == 'uber' else f'UberX de {market} × {ratio}'
        seen        = f"ajustada a {calibration['count']} {'preço seu' if calibration['count'] == 1 else 'preços seus'} por perto" if calibration['count'] else 'sem preço seu por perto'
        self.hint.configure(text=f'{name}: {base} · {seen}')

    # BARRA DE ROLAGEM DO PAINEL LATERAL SO APARECE QUANDO O CONTEUDO NAO CABE NA ALTURA DA JANELA
    def showScroll(self, event=None):
        color = COLORS['card'] if self.side.winfo_reqheight() <= self.side.master.winfo_height() else COLORS['border']
        self.side.configure(scrollbar_button_color=color, scrollbar_button_hover_color=COLORS['hover'] if color == COLORS['border'] else color)

    # ERRO EM CALLBACK DO TK VAI PARA O LOG DO APP EM VEZ DE SUMIR NO TERMINAL
    def report_callback_exception(self, exc, val, tb):
        logging.error('erro em callback da interface', exc_info=(exc, val, tb))

    def stop(self):
        for job in self.jobs.values():
            self.after_cancel(job)

        self.pool.shutdown(wait=False, cancel_futures=True)
        self.destroy()
