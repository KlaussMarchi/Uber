import math, requests
import tkinter as tk
import customtkinter as ctk
from io import BytesIO
from PIL import Image, ImageOps, ImageTk
from Api.index import api
from Engine.index import engine
from Utils.functions import getDistance
from Utils.variables import COLORS, FONT


# ESCOLHA DO PONTO EXATO: SEM GPS A LOCALIZACAO AUTOMATICA ERRA QUILOMETROS, ENTAO VALEM OS LUGARES JA USADOS E O CLIQUE NUM MAPA QUE SE ARRASTA
class Locator(ctk.CTkToplevel):
    TILES  = ('https://tile.opentopomap.org/{z}/{x}/{y}.png', 'https://a.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png')    # opentopomap e, se ele falhar, a osm france; os servidores principais do OSM recusam app sem cadastro
    SIZE   = 256      # lado do tile em pixels
    WIDTH  = 660
    HEIGHT = 400
    ZOOMS  = ((500, 16), (5000, 14), (float('inf'), 12))    # zoom inicial conforme o erro da estimativa: quanto pior, mais aberto
    NEAR   = 15       # zoom ao abrir num lugar ja usado: nivel de rua, para so ajustar o ponto
    LIMITS = (11, 17)    # o opentopomap renderiza ate o zoom 17
    DRAG   = 4        # px de movimento a partir do qual o clique vira arraste do mapa
    DELAY  = 500      # ms de pausa antes de buscar o endereco do ponto marcado
    PLACES = 5
    REGION = {'label': 'Macaé e Rio das Ostras', 'lat': api.BIAS['lat'], 'lon': api.BIAS['lon']}

    # como o mapa abriu, na primeira linha da janela
    NOTES = {
        'estimativa': 'Estimativa automática com erro de ~{}.',
        'perto':      'Estimativa automática com erro de ~{}: o mapa abriu no último lugar usado dentro dessa área.',
        'lugar':      'Localização automática indisponível: o mapa abriu no último lugar usado.',
        'regiao':     'Localização automática indisponível.',
    }

    def __init__(self, master, estimate, cb):
        super().__init__(master, fg_color=COLORS['background'])
        self.title('Onde você está?')
        self.resizable(False, False)
        self.transient(master)
        recent      = engine.getPlaces(self.PLACES)
        self.cb     = cb
        self.place, self.zoom, mode = self.getStart(estimate, recent)
        self.center = (self.place['lat'], self.place['lon'])
        self.marker = self.center
        self.tiles  = {}
        self.failed = set()
        self.photo  = None
        self.job    = None
        self.drag   = None

        accuracy  = estimate['accuracy'] if estimate else 0
        precision = f'{accuracy:.0f} m' if accuracy < 1000 else f'{accuracy / 1000:.0f} km'
        ctk.CTkLabel(self, text=self.NOTES[mode].format(precision) + ' Arraste para mover o mapa e clique para marcar o ponto exato.', font=ctk.CTkFont(FONT, 12), text_color=COLORS['muted'], wraplength=self.WIDTH, justify='left', anchor='w').pack(fill='x', padx=24, pady=(18, 0))

        if recent:
            ctk.CTkLabel(self, text='LUGARES JÁ USADOS', height=18, font=ctk.CTkFont(FONT, 11, 'bold'), text_color=COLORS['muted'], anchor='w').pack(fill='x', padx=24, pady=(12, 4))

        for item in recent:
            ctk.CTkButton(self, text=item['label'] if len(item['label']) <= 60 else item['label'][:59] + '…', anchor='w', height=30, corner_radius=8, fg_color=COLORS['input'], hover_color=COLORS['hover'], text_color=COLORS['text'], font=ctk.CTkFont(FONT, 12), command=lambda item=item: self.choose(item)).pack(fill='x', padx=24, pady=2)

        self.canvas = tk.Canvas(self, width=self.WIDTH, height=self.HEIGHT, bg=COLORS['card'], highlightthickness=0, cursor='crosshair')
        self.canvas.pack(padx=24, pady=(14, 0))
        self.canvas.bind('<ButtonPress-1>', self.handlePress)
        self.canvas.bind('<B1-Motion>', self.handleDrag)
        self.canvas.bind('<ButtonRelease-1>', self.handleRelease)
        self.canvas.bind('<MouseWheel>', lambda event: self.handleZoom(1 if event.delta > 0 else -1))
        self.canvas.bind('<Button-4>', lambda event: self.handleZoom(1))
        self.canvas.bind('<Button-5>', lambda event: self.handleZoom(-1))

        zoom = ctk.CTkFrame(self, fg_color='transparent')
        zoom.place(in_=self.canvas, relx=1, rely=0, x=-12, y=12, anchor='ne')

        for text, step in (('+', 1), ('−', -1)):
            ctk.CTkButton(zoom, text=text, width=32, height=32, corner_radius=8, font=ctk.CTkFont(FONT, 16, 'bold'), fg_color=COLORS['input'], hover_color=COLORS['hover'], text_color=COLORS['text'], command=lambda step=step: self.handleZoom(step)).pack(pady=2)

        self.address = ctk.CTkLabel(self, text=self.place['label'], height=20, font=ctk.CTkFont(FONT, 13, 'bold'), text_color=COLORS['text'], anchor='w')
        self.address.pack(fill='x', padx=24, pady=(10, 0))
        ctk.CTkLabel(self, text='© contribuidores do OpenStreetMap · OpenTopoMap (CC-BY-SA) · OSM France', font=ctk.CTkFont(FONT, 10), text_color=COLORS['muted'], anchor='w').pack(fill='x', padx=24)

        buttons = ctk.CTkFrame(self, fg_color='transparent')
        buttons.pack(fill='x', padx=24, pady=(10, 16))
        ctk.CTkButton(buttons, text='Usar este ponto', height=38, corner_radius=10, font=ctk.CTkFont(FONT, 14, 'bold'), fg_color=COLORS['button'], hover_color=COLORS['pressed'], text_color='#FFFFFF', command=lambda: self.choose(self.place)).pack(side='right')
        ctk.CTkButton(buttons, text='Cancelar', width=110, height=38, corner_radius=10, font=ctk.CTkFont(FONT, 14), fg_color=COLORS['input'], hover_color=COLORS['hover'], text_color=COLORS['muted'], command=self.stop).pack(side='right', padx=(0, 10))

        # a altura sai do proprio conteudo, que varia com quantos lugares ja usados existem
        self.update_idletasks()
        width, height = self.WIDTH + 48, self.winfo_reqheight()
        self.geometry(f'{width}x{height}+{max(master.winfo_rootx() + (master.winfo_width() - width) // 2, 0)}+{max(master.winfo_rooty() + (master.winfo_height() - height) // 2, 0)}')
        self.protocol('WM_DELETE_WINDOW', self.stop)
        self.after(150, self.grab_set)
        self.draw()

    # PONTO INICIAL, ZOOM E MODO: A ESTIMATIVA; SE ELA E GROSSEIRA, O LUGAR USADO MAIS RECENTE DENTRO DO ERRO DELA; SEM ESTIMATIVA, O ULTIMO LUGAR USADO OU A REGIAO
    def getStart(self, estimate, recent):
        if estimate is None:
            return (recent[0], self.NEAR, 'lugar') if recent else (self.REGION, self.ZOOMS[-1][1], 'regiao')

        near = next((item for item in recent if getDistance(estimate, item) * 1000 <= estimate['accuracy']), None) if estimate['accuracy'] > api.STREET else None

        if near is not None:
            return near, self.NEAR, 'perto'

        return estimate, next(zoom for limit, zoom in self.ZOOMS if estimate['accuracy'] <= limit), 'estimativa'

    # PIXEL GLOBAL DE UMA COORDENADA NO NIVEL DE ZOOM (WEB MERCATOR, A PROJECAO DOS TILES)
    def getPixel(self, lat, lon, zoom):
        size = self.SIZE * 2 ** zoom
        rad  = math.radians(lat)
        return (lon + 180) / 360 * size, (1 - math.log(math.tan(rad) + 1 / math.cos(rad)) / math.pi) / 2 * size

    # COORDENADA DE VOLTA A PARTIR DO PIXEL GLOBAL
    def getCoords(self, px, py, zoom):
        size = self.SIZE * 2 ** zoom
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * py / size)))), px / size * 360 - 180

    # BAIXA UM TILE FORA DA THREAD DA JANELA, TENTANDO OS SERVIDORES EM ORDEM; A IMAGEM DO TK SO PODE NASCER NA THREAD DELA, ENTAO AQUI SO ANDA PIL
    def getTile(self, key):
        zoom, x, y = key

        for url in self.TILES:
            try:
                res = api.session.get(url.format(z=zoom, x=x, y=y), timeout=api.TIMEOUT)
                res.raise_for_status()
                # cinza invertido deixa o mapa escuro como o resto da janela, com as ruas claras
                return key, ImageOps.invert(ImageOps.grayscale(Image.open(BytesIO(res.content)))).convert('RGB')
            except (requests.RequestException, OSError):
                continue

        return key, None

    # MONTA O MAPA COM OS TILES JA BAIXADOS, PEDE OS QUE FALTAM E DESENHA O MARCADOR
    def draw(self):
        cx, cy     = self.getPixel(*self.center, self.zoom)
        left, top  = cx - self.WIDTH / 2, cy - self.HEIGHT / 2
        keys       = [(self.zoom, x, y) for x in range(int(left // self.SIZE), int((left + self.WIDTH) // self.SIZE) + 1) for y in range(int(top // self.SIZE), int((top + self.HEIGHT) // self.SIZE) + 1)]
        missing    = [key for key in keys if key not in self.tiles]
        image      = Image.new('RGB', (self.WIDTH, self.HEIGHT), COLORS['card'])

        for key in missing:
            self.tiles[key] = None                                          # marcado como pedido, para nao repetir o download
            self.master.submit(lambda key=key: self.getTile(key), self.showTile)

        for key in keys:
            tile = self.tiles.get(key)

            if tile is not None:
                image.paste(tile, (int(key[1] * self.SIZE - left), int(key[2] * self.SIZE - top)))

        self.photo = ImageTk.PhotoImage(image)
        x, y = [value - offset for value, offset in zip(self.getPixel(*self.marker, self.zoom), (left, top))]
        self.canvas.delete('all')
        self.canvas.create_image(0, 0, image=self.photo, anchor='nw')

        if any(self.tiles.get(key) is None for key in keys):
            self.canvas.create_text(self.WIDTH / 2, 24, text='carregando mapa…', fill=COLORS['muted'], font=(FONT, 11))

        self.canvas.create_line(x, y + 6, x, y + 22, fill=COLORS['price'], width=3)
        self.canvas.create_oval(x - 9, y - 12, x + 9, y + 6, outline=COLORS['price'], width=3, fill=COLORS['background'])

    # INICIO DE UM CLIQUE OU DE UM ARRASTE: GUARDA ONDE O CURSOR E O MAPA ESTAVAM
    def handlePress(self, event):
        self.drag = (event.x, event.y, *self.getPixel(*self.center, self.zoom), False)

    # ARRASTE LEVA O MAPA JUNTO COM O CURSOR; MOVIMENTO MENOR QUE DRAG AINDA CONTA COMO CLIQUE
    def handleDrag(self, event):
        if self.drag is None:
            return

        x, y, cx, cy, moved = self.drag

        if not moved and math.hypot(event.x - x, event.y - y) < self.DRAG:
            return

        self.drag   = (x, y, cx, cy, True)
        self.center = self.getCoords(cx - (event.x - x), cy - (event.y - y), self.zoom)
        self.draw()

    # SOLTAR O BOTAO SEM TER ARRASTADO MARCA O PONTO; DEPOIS DE UM ARRASTE, OS TILES QUE FALHARAM PODEM SER PEDIDOS DE NOVO
    def handleRelease(self, event):
        drag, self.drag = self.drag, None

        if drag is None:
            return

        if not drag[4]:
            return self.handleClick(event)

        self.retry()

    # CLIQUE MARCA O PONTO NA HORA, COM A COORDENADA COMO ROTULO ATE O ENDERECO CHEGAR, PARA "USAR ESTE PONTO" NUNCA PEGAR O PONTO ANTERIOR
    def handleClick(self, event):
        cx, cy      = self.getPixel(*self.center, self.zoom)
        self.marker = self.getCoords(cx - self.WIDTH / 2 + event.x, cy - self.HEIGHT / 2 + event.y, self.zoom)
        self.place  = {'label': f'{self.marker[0]:.5f}, {self.marker[1]:.5f}', 'lat': self.marker[0], 'lon': self.marker[1]}
        self.draw()
        self.showAddress()

    # ZOOM EM TORNO DO CENTRO DA VISTA, DENTRO DOS NIVEIS QUE O SERVIDOR DE TILES RENDERIZA
    def handleZoom(self, step):
        self.zoom = min(max(self.zoom + step, self.LIMITS[0]), self.LIMITS[1])
        self.retry()

    # TILES QUE FALHARAM SAEM DO CACHE E O MAPA E REDESENHADO, O QUE OS PEDE DE NOVO UMA VEZ POR ACAO DO USUARIO
    def retry(self):
        for key in self.failed:
            self.tiles.pop(key, None)

        self.failed.clear()
        self.draw()

    # ENDERECO DO PONTO MARCADO, DEPOIS DE UMA PAUSA, PARA NAO CONSULTAR A CADA CLIQUE
    def showAddress(self):
        if self.job is not None:
            self.after_cancel(self.job)

        self.address.configure(text='buscando endereço…', text_color=COLORS['muted'])
        self.job = self.after(self.DELAY, lambda: self.master.submit(lambda: api.reverse(*self.marker), self.showPlace))

    # ENDERECO QUE VOLTOU DO PHOTON; RESPOSTA DE UM PONTO QUE JA NAO E O MARCADO E DESCARTADA
    def showPlace(self, place):
        if not self.winfo_exists() or (place['lat'], place['lon']) != self.marker:
            return

        self.place = place
        self.address.configure(text=place['label'], text_color=COLORS['text'])

    # TILE QUE ACABOU DE CHEGAR: ENTRA NO CACHE E O MAPA E REDESENHADO, ENTAO ELE APARECE AOS POUCOS
    def showTile(self, result):
        key, image = result

        if not self.winfo_exists():
            return

        if image is None:
            return self.failed.add(key)

        self.tiles[key] = image
        self.draw()

    def choose(self, place):
        self.cb(place)
        self.stop()

    def stop(self):
        if self.job is not None:
            self.after_cancel(self.job)

        self.grab_release()
        self.destroy()
