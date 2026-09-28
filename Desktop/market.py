import os, json, logging
import numpy as np
import pandas as pd
from Market.index import getFrame, getRoutes, getTable
from Utils.variables import MARKET_PATH, ROUTES_PATH


logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

if not os.path.exists(ROUTES_PATH):
    getRoutes().to_csv(ROUTES_PATH, index=False)

df            = getFrame(ROUTES_PATH)
market, check = getTable(df)

with open(MARKET_PATH, 'w', encoding='utf-8') as file:
    json.dump(market, file, ensure_ascii=False)

old   = np.log((1 + np.maximum(7.5, 2.5 + 1.2076 * df['km'] + 0.22 * df['min'])) / df['uberx'])
table = pd.DataFrame({'tabela antiga': old, 'rota nova numa cidade conhecida': check['rota']['price'], 'cidade nova (so o estado)': check['cidade']['price']})
print(f"{len(df)} trechos reais de {df['src_key'].nunique()} cidades em {df['uf'].nunique()} estados; tarifa sem dinamica {market['tariff']}")
print('erro absoluto mediano:', (np.exp(table.abs()) - 1).median().round(4).to_dict())
print('vies mediano:', (np.exp(table) - 1).median().round(4).to_dict())
print('ritmo real sobre o osrm, erro mediano:', round(float((np.exp(np.abs(check['rota']['pace'][df['match']])) - 1).median()), 4), 'incerteza', market['sigma'])
