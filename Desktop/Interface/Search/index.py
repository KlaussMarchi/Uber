import customtkinter as ctk
from Api.index import api
from Utils.variables import COLORS, FONT


# CAMPO DE ENDERECO COM AUTOCOMPLETE E BOTAO DE LOCALIZACAO; SO CONTA COMO VALIDADO QUANDO UMA SUGESTAO E ESCOLHIDA
class Search(ctk.CTkFrame):
    DELAY     = 350    # ms de pausa na digitacao antes de consultar
    MIN_CHARS = 3
    LIMIT     = 6
    WIDTH     = 44     # caracteres visiveis por sugestao

    def __init__(self, master, title, placeholder, cb, action):
        super().__init__(master, fg_color='transparent')
        self.cb       = cb
        self.selected = None
        self.results  = []
        self.index    = -1
        self.query    = ''
        self.job      = None

        ctk.CTkLabel(self, text=title, height=18, font=ctk.CTkFont(FONT, 12, 'bold'), text_color=COLORS['muted'], anchor='w').pack(fill='x')
        row = ctk.CTkFrame(self, fg_color='transparent')
        row.pack(fill='x', pady=(6, 0))
        row.grid_columnconfigure(0, weight=1)

        self.entry = ctk.CTkEntry(row, height=44, corner_radius=10, border_width=1, font=ctk.CTkFont(FONT, 14), fg_color=COLORS['input'], border_color=COLORS['border'], text_color=COLORS['text'], placeholder_text=placeholder, placeholder_text_color=COLORS['muted'])
        self.entry.grid(row=0, column=0, sticky='ew')
        self.button = ctk.CTkButton(row, text='◎', width=44, height=44, corner_radius=10, border_width=1, font=ctk.CTkFont(FONT, 20), fg_color=COLORS['input'], hover_color=COLORS['hover'], border_color=COLORS['border'], text_color=COLORS['text'], text_color_disabled=COLORS['muted'], command=action)
        self.button.grid(row=0, column=1, padx=(8, 0))

        # a lista pertence a janela para sobrepor os outros cartoes, mas se posiciona sob o campo
        self.menu = ctk.CTkFrame(self.winfo_toplevel(), fg_color=COLORS['input'], border_width=1, border_color=COLORS['border'], corner_radius=10)
        self.rows = [ctk.CTkButton(self.menu, text='', anchor='w', height=34, corner_radius=8, fg_color='transparent', hover_color=COLORS['hover'], text_color=COLORS['text'], text_color_disabled=COLORS['muted'], font=ctk.CTkFont(FONT, 13), command=lambda i=i: self.choose(i)) for i in range(self.LIMIT)]

        self.entry.bind('<KeyRelease>', self.handleKey)
        self.entry.bind('<Return>', self.handleReturn)
        self.entry.bind('<Down>', lambda event: self.move(1))
        self.entry.bind('<Up>', lambda event: self.move(-1))
        self.entry.bind('<Escape>', lambda event: self.hide())
        self.winfo_toplevel().bind('<Button-1>', self.handleClick, add='+')

    def get(self):
        return self.selected or {'label': self.entry.get().strip()}

    def set(self, place):
        self.entry.delete(0, 'end')
        self.entry.insert(0, place['label'])
        self.query    = place['label']
        self.selected = place
        self.entry.configure(border_color=COLORS['success'])

    # DIGITACAO: SO QUANDO O TEXTO MUDA INVALIDA A ESCOLHA ANTERIOR E AGENDA A BUSCA PARA A PAUSA DO USUARIO
    def handleKey(self, event):
        text = self.entry.get().strip()

        if text == self.query:
            return

        self.query    = text
        self.selected = None
        self.entry.configure(border_color=COLORS['border'])

        if self.job is not None:
            self.after_cancel(self.job)
            self.job = None

        if len(text) < self.MIN_CHARS:
            return self.hide()

        self.job = self.after(self.DELAY, self.handleSearch)

    # MOSTRA O AVISO DE CARREGANDO E CONSULTA AS SUGESTOES FORA DA THREAD DA JANELA
    def handleSearch(self):
        text = self.entry.get().strip()
        self.results = []
        self.showRows(['Buscando endereços…'], False)
        self.winfo_toplevel().submit(lambda: api.search(text, self.LIMIT), lambda places: self.show(text, places))

    # ENTER ESCOLHE A SUGESTAO DESTACADA; SEM DESTAQUE, FECHA A LISTA E DISPARA O CALCULO
    def handleReturn(self, event):
        if self.menu.winfo_ismapped() and self.index >= 0:
            return self.choose(self.index)

        self.hide()
        self.cb()

    # CLIQUE FORA DO CAMPO E DA LISTA FECHA AS SUGESTOES
    def handleClick(self, event):
        path = str(event.widget) + '.'

        if not path.startswith((str(self.menu) + '.', str(self.entry) + '.')):
            self.hide()

    def show(self, text, places):
        if text != self.entry.get().strip() or self.selected is not None:
            return

        self.results = places or []
        self.index   = -1
        message = 'Sem conexão com o serviço de endereços' if places is None else 'Nenhum endereço encontrado'
        self.showRows([place['label'] for place in self.results] or [message], bool(self.results))

    # PREENCHE E ABRE A LISTA SOB O CAMPO, COM AS LINHAS CLICAVEIS SO QUANDO SAO ENDERECOS DE VERDADE
    def showRows(self, labels, enabled):
        for row in self.rows:
            row.pack_forget()

        for row, label in zip(self.rows, labels):
            row.configure(text=label if len(label) <= self.WIDTH else label[:self.WIDTH - 1] + '…', fg_color='transparent', state='normal' if enabled else 'disabled')
            row.pack(fill='x', padx=6, pady=3)

        self.menu.place(in_=self.entry, x=0, rely=1, y=6, relwidth=1)
        self.menu.lift()

    # SETAS PERCORREM AS SUGESTOES DESTACANDO A ATUAL
    def move(self, step):
        if not self.results or not self.menu.winfo_ismapped():
            return

        self.index = (self.index + step) % len(self.results)

        for i, row in enumerate(self.rows[:len(self.results)]):
            row.configure(fg_color=COLORS['hover'] if i == self.index else 'transparent')

    # FIXA A SUGESTAO ESCOLHIDA COMO LUGAR VALIDADO
    def choose(self, i):
        self.set(self.results[i])
        self.hide()

    # FECHA A LISTA SEM MEXER NO CAMPO
    def hide(self):
        self.index = -1
        self.menu.place_forget()
