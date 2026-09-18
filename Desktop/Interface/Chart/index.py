import matplotlib
import numpy as np
import pandas as pd
import customtkinter as ctk
import matplotlib.dates as mdates
from datetime import datetime
from matplotlib.colors import to_rgb
from matplotlib.figure import Figure
from matplotlib.patheffects import withStroke
from matplotlib.ticker import FuncFormatter, MaxNLocator, MultipleLocator
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from Utils.functions import getDelay, getDuration, getLocal, getMoney, getSpan
from Utils.variables import COLORS, FONT, HORIZON, STEP


matplotlib.rcParams.update({
    'font.family':       [FONT, 'DejaVu Sans'],
    'font.size':         10,
    'text.color':        COLORS['text'],
    'axes.facecolor':    COLORS['card'],
    'axes.edgecolor':    COLORS['border'],
    'axes.labelcolor':   COLORS['muted'],
    'axes.spines.top':   False,
    'axes.spines.right': False,
    'axes.grid':         True,
    'axes.grid.axis':    'y',
    'grid.color':        COLORS['grid'],
    'grid.linewidth':    1,
    'xtick.color':       COLORS['muted'],
    'ytick.color':       COLORS['muted'],
})


# TRES LINHAS NO MESMO EIXO DE TEMPO: PRECO COM A FAIXA P10-P90, CHUVA (CHANCE POR HORA E INTENSIDADE) E TEMPO DE VIAGEM COM A FAIXA M10-M90
class Chart(ctk.CTkFrame):
    HOURS  = HORIZON // 3600
    RATIOS = [3, 0.32, 0.95, 1.35]                                       # preco, faixa de chance, intensidade da chuva, tempo de viagem
    LEVELS = ((0, 'sem trânsito'), (10, 'moderado'), (30, 'intenso'))    # % acima do tempo livre das linhas de referencia, rotuladas com o tempo de viagem de cada uma
    STEPS  = (1, 2, 5, 10, 15, 20, 30, 60, 120, 180, 360)                # minutos entre marcas do eixo do tempo de viagem; vale o menor passo com ate 3 intervalos
    DRY    = 0.05                                                        # mm/h abaixo do qual a janela inteira conta como seca
    CHANCE = 0.65                                                        # opacidade da celula com 100% de chance; acima disso o texto claro perde contraste

    def __init__(self, master, cb):
        super().__init__(master, fg_color=COLORS['card'], corner_radius=16)
        self.cb    = cb
        self.hours = 5
        self.df    = None

        header = ctk.CTkFrame(self, fg_color='transparent')
        header.pack(fill='x', padx=22, pady=(18, 0))
        self.heading = ctk.CTkLabel(header, text='Previsão de preço, chuva e tempo de viagem', font=ctk.CTkFont(FONT, 16, 'bold'), text_color=COLORS['text'], anchor='w')
        self.heading.pack(side='left')
        self.window = ctk.CTkSlider(header, from_=1, to=self.HOURS, number_of_steps=self.HOURS - 1, width=170, height=16, command=self.handleWindow, fg_color=COLORS['input'], progress_color=COLORS['button'], button_color=COLORS['button'], button_hover_color=COLORS['pressed'])
        self.window.set(self.hours)
        self.window.pack(side='right', padx=(10, 0))
        self.label = ctk.CTkLabel(header, text=f'janela {self.hours} h', width=76, font=ctk.CTkFont(FONT, 12, 'bold'), text_color=COLORS['secondary'], anchor='e')
        self.label.pack(side='right')

        self.figure = Figure(figsize=(9, 7), facecolor=COLORS['card'], layout='constrained')
        self.price, self.chance, self.rain, self.traffic = self.figure.subplots(4, 1, sharex=True, height_ratios=self.RATIOS)
        self.canvas = FigureCanvasTkAgg(self.figure, master=self)
        self.canvas.get_tk_widget().configure(bg=COLORS['card'], highlightthickness=0)
        self.canvas.get_tk_widget().pack(fill='both', expand=True, padx=16, pady=(4, 16))
        self.canvas.mpl_connect('motion_notify_event', self.handleMotion)
        self.canvas.mpl_connect('figure_leave_event', self.hideTip)
        self.reset('Informe origem e destino e clique em Calcular preço')

    def reset(self, message):
        self.df = None
        self.heading.configure(text='Previsão de preço, chuva e tempo de viagem')

        for ax in self.figure.axes:
            ax.clear()
            ax.set_axis_off()

        self.price.text(0.5, 0.5, message, transform=self.price.transAxes, ha='center', va='center', color=COLORS['muted'], fontsize=13)
        self.canvas.draw_idle()

    # TROCA A JANELA EXIBIDA SEM RECALCULAR: A SERIE INTEIRA JA ESTA PRONTA E A JANELA PRINCIPAL SO REDESENHA
    def handleWindow(self, value):
        hours = int(round(value))

        if hours == self.hours:
            return

        self.hours = hours
        self.label.configure(text=f'janela {hours} h')
        self.cb()

    def plot(self, df, route, company):
        view    = df[df['ts'] <= df['ts'].iloc[0] + self.hours * 3600].reset_index(drop=True)
        local   = getLocal(view['ts'], route['tz'])
        free    = route['duration']
        self.df = view
        self.route = route
        self.x  = mdates.date2num(local)
        price, chance, rain, traffic = self.price, self.chance, self.rain, self.traffic

        for ax in self.figure.axes:
            ax.clear()
            ax.set_axis_on()
            ax.tick_params(length=0, labelbottom=ax is traffic)

        # sem legenda: o titulo de cada linha diz o que ela mostra e os rotulos ficam direto nas marcas
        for ax, title, note in ((price, f'Preço da corrida · {company}', 'previsto (p50) e faixa p10–p90'), (chance, 'Chuva prevista', 'chance por hora e intensidade'), (traffic, 'Tempo de viagem previsto', 'previsto (m50) e faixa m10–m90')):
            ax.set_title(title, loc='left', fontsize=11, color=COLORS['text'], pad=6)
            ax.set_title(note, loc='right', fontsize=9, color=COLORS['muted'], pad=6)

        price.fill_between(self.x, view['p10'], view['p90'], color=COLORS['price'], alpha=0.18, linewidth=0)
        price.plot(self.x, view['p50'], color=COLORS['price'], linewidth=2, solid_capstyle='round')
        price.scatter(self.x[0], view['price'].iloc[0], s=70, color=COLORS['text'], edgecolors=COLORS['card'], linewidths=2, zorder=5, clip_on=False)
        price.annotate('agora', (self.x[0], 0), xycoords=('data', 'axes fraction'), xytext=(5, 5), textcoords='offset points', color=COLORS['muted'], fontsize=9)

        peak = int(view['p50'].to_numpy().argmax())
        ha   = 'left' if peak < len(view) / 4 else 'right' if peak > len(view) * 3 / 4 else 'center'
        price.scatter(self.x[peak], view['p50'].iloc[peak], s=70, color=COLORS['price'], edgecolors=COLORS['card'], linewidths=2, zorder=4, clip_on=False)
        price.annotate(f"pico {getMoney(view['p50'].iloc[peak])} às {local[peak]:%H:%M}", (self.x[peak], view['p90'].iloc[peak]), xytext=(0, 8), textcoords='offset points', ha=ha, va='bottom', color=COLORS['text'], fontsize=10)

        low, high = view['p10'].min(), view['p90'].max()
        pad = max(high - low, 2.0)
        price.set_ylim(low - 0.10 * pad, high + 0.25 * pad)
        price.set_xlim(self.x[0], self.x[-1])
        price.yaxis.set_major_formatter(FuncFormatter(lambda value, pos: f'R$ {value:.0f}'))

        # a chance vem de hora em hora: a cor de cada slot mostra a transicao e o numero fica no meio do trecho visivel de cada hora
        odds  = view['probability'].to_numpy()
        hours = pd.DataFrame({'x': self.x, 'odds': odds}).groupby(local.floor('h')).agg(start=('x', 'min'), end=('x', 'max'), odds=('odds', 'mean'), n=('x', 'size'))
        chance.set_facecolor(COLORS['input'])
        chance.bar(self.x, 1, width=np.diff(self.x, append=self.x[-1]), align='edge', color=[(*to_rgb(COLORS['rain']), self.CHANCE * p / 100) for p in odds], linewidth=0)

        for start, end, p, n in hours.itertuples(index=False):
            if n >= 4:    # ao menos 40 min visiveis da hora para o rotulo caber sem encostar na borda; a janela de 1 h sempre tem uma assim
                chance.text((start + min(end + STEP / 86400, self.x[-1])) / 2, 0.5, f'{p:.0f}%', ha='center', va='center', fontsize=9, color=COLORS['text'])

        chance.grid(False)
        chance.spines[['left', 'bottom']].set_visible(False)
        chance.set_ylim(0, 1)
        chance.set_yticks([0.5], ['chance'])

        rain.fill_between(self.x, 0, view['rain'], color=COLORS['rain'], alpha=0.30, linewidth=0)
        rain.plot(self.x, view['rain'], color=COLORS['rain'], linewidth=1.8)
        rain.set_ylim(0, max(view['rain'].max() * 1.3, 2))
        rain.yaxis.set_major_locator(MaxNLocator(2))
        rain.yaxis.set_major_formatter(FuncFormatter(lambda value, pos: f'{value:g} mm/h'.replace('.', ',')))

        if view['rain'].max() < self.DRY:
            rain.text(0.5, 0.55, 'sem chuva prevista na janela', transform=rain.transAxes, ha='center', va='center', color=COLORS['muted'], fontsize=9)

        # o tempo de viagem em minutos (ou horas) com a mesma escala do preco: faixa, mediana e linhas de referencia com o tempo de cada nivel de transito
        fast, slow = min(view['m10'].min(), free), view['m90'].max()
        span       = max(slow - fast, 2.0)
        bottom     = fast - 0.10 * span
        top        = slow + 0.25 * span
        traffic.fill_between(self.x, view['m10'], view['m90'], color=COLORS['traffic'], alpha=0.18, linewidth=0)
        traffic.plot(self.x, view['m50'], color=COLORS['traffic'], linewidth=2, solid_capstyle='round')
        traffic.set_ylim(bottom, top)
        traffic.yaxis.set_major_locator(MultipleLocator(next((step for step in self.STEPS if (top - bottom) / step <= 3), self.STEPS[-1])))
        traffic.yaxis.set_major_formatter(FuncFormatter(lambda value, pos: getDuration(value)))
        traffic.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=9))
        traffic.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))

        for level, name in self.LEVELS:
            minutes = free * (1 + level / 100)

            if minutes <= top:
                traffic.axhline(minutes, color=COLORS['border'], linewidth=1)
                traffic.annotate(f'{name} · {getDuration(minutes)}', (self.x[-1], minutes), xytext=(-4, 3), textcoords='offset points', ha='right', color=COLORS['muted'], fontsize=9, path_effects=[withStroke(linewidth=3, foreground=COLORS['card'])])

        # o eixo mostra a hora da rota; so avisa quando o fuso dela difere do relogio deste computador
        start = pd.Timestamp(int(view['ts'].iloc[0]), unit='s', tz=route['tz'])
        note  = f" · horário de {route['tz'].split('/')[-1].replace('_', ' ')} (UTC{start.utcoffset().total_seconds() / 3600:+.0f})" if start.utcoffset() != datetime.fromtimestamp(start.timestamp()).astimezone().utcoffset() else ''
        self.heading.configure(text=f"Próxima{'s' if self.hours > 1 else ''} {self.hours} h · passos de 10 min{note}")

        self.cursor = [ax.axvline(self.x[0], color=COLORS['muted'], linewidth=1, visible=False) for ax in self.figure.axes]
        self.tip    = price.annotate('', (self.x[0], low), xytext=(14, 0), textcoords='offset points', va='center', ma='left', fontsize=10, color=COLORS['text'], visible=False, zorder=10, bbox={'boxstyle': 'round,pad=0.6', 'fc': COLORS['input'], 'ec': COLORS['border']})
        self.canvas.draw_idle()

    # CRUZETA NAS TRES LINHAS E UM RESUMO COM PRECO, TEMPO DE VIAGEM, HORA DE CHEGADA, TRANSITO E CHUVA DAQUELE HORARIO
    def handleMotion(self, event):
        if self.df is None or event.inaxes not in self.figure.axes:
            return self.hideTip()

        i       = int(np.abs(self.x - event.xdata).argmin())
        row     = self.df.iloc[i]
        right   = i > len(self.df) / 2
        rain    = f"{row['rain']:.1f}".replace('.', ',')
        seen    = f"\n{getMoney(row['price'])} e {getDuration(row['minutes'])}   observados" if i == 0 else ''
        arrival = getLocal([row['ts'] + row['m50'] * 60], self.route['tz'])[0]

        for line in self.cursor:
            line.set_xdata([self.x[i], self.x[i]])
            line.set_visible(True)

        self.tip.xy = (self.x[i], row['p50'])
        self.tip.set_text(f"{getLocal([row['ts']], self.route['tz'])[0]:%H:%M}{seen}\n{getMoney(row['p50'])}   previsto ({getMoney(row['p10'])} a {getMoney(row['p90'])})\n{getDuration(row['m50'])}   viagem ({getSpan(row['m10'], row['m90'])})\nchegada às {arrival:%H:%M}   {getDelay(row['m50'] - self.route['duration'])} de trânsito\n{rain} mm/h · {row['probability']:.0f}% de chance   chuva")
        self.tip.set_position((-14 if right else 14, 0))
        self.tip.set_ha('right' if right else 'left')
        self.tip.set_visible(True)
        self.canvas.draw_idle()

    # APAGA CRUZETA E RESUMO QUANDO O CURSOR SAI DO GRAFICO
    def hideTip(self, event=None):
        if self.df is None or not self.tip.get_visible():
            return

        self.tip.set_visible(False)

        for line in self.cursor:
            line.set_visible(False)

        self.canvas.draw_idle()
