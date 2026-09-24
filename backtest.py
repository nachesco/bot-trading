import ccxt
import pandas as pd

exchange = ccxt.binance()

print("📊 Descargando datos históricos de BTC/USDT (últimas 1,000 horas)...")

# Descarga de datos de mercado
ohlcv = exchange.fetch_ohlcv('BTC/USDT', timeframe='1h', limit=1000)
df = pd.DataFrame(ohlcv, columns=['tiempo', 'apertura', 'maximo', 'minimo', 'cierre', 'volumen'])
df['tiempo'] = pd.to_datetime(df['tiempo'], unit='ms')

# 1. Medias Móviles (Cruce)
df['media_rapida'] = df['cierre'].rolling(window=5).mean()
df['media_lenta'] = df['cierre'].rolling(window=20).mean()

# 2. Indicador RSI (Filtro de sobrecompra)
delta = df['cierre'].diff()
gain = delta.where(delta > 0, 0)
loss = -delta.where(delta < 0, 0)
avg_gain = gain.rolling(window=14).mean()
avg_loss = loss.rolling(window=14).mean()
rs = avg_gain / avg_loss
df['rsi'] = 100 - (100 / (1 + rs))

# Parámetros del Simulador y Gestión de Riesgo
capital_inicial = 1000.0
capital = capital_inicial
btc = 0.0
operaciones = 0
ganadoras = 0
perdedoras = 0
precio_compra = 0.0

STOP_LOSS_PCT = 0.02   # 2% de pérdida máxima permitida
TAKE_PROFIT_PCT = 0.04  # 4% de beneficio objetivo

# Simulación histórica
for i in range(21, len(df)):
    actual = df.iloc[i]
    anterior = df.iloc[i-1]
    precio = actual['cierre']

    # GESTIÓN DE POSICIÓN ABIERTA (Si tenemos BTC)
    if btc > 0:
        rendimiento = (precio - precio_compra) / precio_compra

        # Regla 1: Activar Stop Loss (-2%)
        if rendimiento <= -STOP_LOSS_PCT:
            capital = btc * precio
            btc = 0
            perdedoras += 1

        # Regla 2: Activar Take Profit (+4%)
        elif rendimiento >= TAKE_PROFIT_PCT:
            capital = btc * precio
            btc = 0
            ganadoras += 1

        # Regla 3: Cruce bajista de medias (Venta por estrategia)
        elif anterior['media_rapida'] >= anterior['media_lenta'] and actual['media_rapida'] < actual['media_lenta']:
            capital = btc * precio
            if precio > precio_compra:
                ganadoras += 1
            else:
                perdedoras += 1
            btc = 0

    # EVALUACIÓN DE COMPRA (Si tenemos dinero USDT)
    elif btc == 0 and capital > 0:
        cruce_alcista = (anterior['media_rapida'] <= anterior['media_lenta']) and (actual['media_rapida'] > actual['media_lenta'])
        rsi_saludable = actual['rsi'] < 65  # Solo compra si el RSI es menor a 65 (evita comprar caro)

        if cruce_alcista and rsi_saludable:
            btc = capital / precio
            precio_compra = precio
            capital = 0
            operaciones += 1

# Cálculo de resultado final
precio_final = df['cierre'].iloc[-1]
capital_final = capital if capital > 0 else (btc * precio_final)
ganancia_pct = ((capital_final - capital_inicial) / capital_inicial) * 100

print("\n================ RESULTADOS CON GESTIÓN DE RIESGO ================")
print(f"Capital Inicial: ${capital_inicial:,.2f} USDT")
print(f"Capital Final:   ${capital_final:,.2f} USDT ({ganancia_pct:+.2f}%)")
print(f"Total Operaciones: {operaciones}")
print(f"Operaciones Ganadoras: {ganadoras} | Perdedoras: {perdedoras}")
if operaciones > 0:
    win_rate = (ganadoras / operaciones) * 100
    print(f"Tasa de Acierto (Win Rate): {win_rate:.1f}%")
print("==================================================================")