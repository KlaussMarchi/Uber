import os


BASE_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR   = os.path.join(BASE_DIR, 'data')
DB_PATH    = os.path.join(DATA_DIR, 'surge.db')
MODEL_PATH = os.path.join(DATA_DIR, 'model.json')
LOG_PATH   = os.path.join(DATA_DIR, 'app.log')

TZ      = 'America/Sao_Paulo'    # fuso da regiao de referencia; cada rota guarda o seu, vindo da coordenada de origem
STEP    = 600          # resolucao da serie e do oraculo (10 min)
HORIZON = 12 * 3600    # a serie e sempre calculada inteira; a janela da tela so recorta o que aparece

# rua grafada "Avarez Parada" no OSM; o teatro fica na Av. Amazonas, junto ao terminal rodoviario
ORIGIN      = {'label': 'Rua Professor Antônio Álvares 92, Parque Aeroporto, Macaé - RJ', 'lat': -22.3413711, 'lon': -41.7556714}
DESTINATION = {'label': 'Teatro Popular de Rio das Ostras - RJ', 'lat': -22.527063, 'lon': -41.9458}

# series validadas (banda de luminancia, daltonismo e contraste) contra o fundo dos cartoes
FONT   = 'Inter'
COLORS = {
    'background': '#0B0D12',
    'card':       '#141821',
    'input':      '#1B2029',
    'hover':      '#262C38',
    'border':     '#2A303C',
    'grid':       '#232833',
    'text':       '#E8EAED',
    'secondary':  '#C3C2B7',
    'muted':      '#8B93A1',
    'price':      '#3987E5',
    'traffic':    '#D95926',
    'rain':       '#199E70',
    'button':     '#256ABF',
    'pressed':    '#2A78D6',
    'success':    '#3DD68C',
    'error':      '#FF6B6B',
}
