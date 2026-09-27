import ccxt
import pandas as pd

print("📊 Descargando histórico de Bitcoin de Binance (Velas de 4 HORAS)...")
exchange = ccxt.binance()
velas = exchange.fetch_ohlcv('BTC/USDT', timeframe='4h', limit=1000)

df = pd.DataFrame(velas, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

print("🧮 Calculando indicadores matemáticos...")
# EMAs y SMA 200
df['EMA_9'] = df['close'].ewm(span=9, adjust=False).mean()
df['EMA_21'] = df['close'].ewm(span=21, adjust=False).mean()
df['SMA_200'] = df['close'].rolling(window=200).mean()

# RSI
delta = df['close'].diff()
gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
rs = gain / loss
df['RSI'] = 100 - (100 / (1 + rs))

# ADX (14 periodos)
df['prev_close'] = df['close'].shift(1)
df['tr1'] = df['high'] - df['low']
df['tr2'] = (df['high'] - df['prev_close']).abs()
df['tr3'] = (df['low'] - df['prev_close']).abs()
df['tr'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)

df['up_move'] = df['high'] - df['high'].shift(1)
df['down_move'] = df['low'].shift(1) - df['low']

df['+dm'] = 0.0
df['-dm'] = 0.0
df.loc[(df['up_move'] > df['down_move']) & (df['up_move'] > 0), '+dm'] = df['up_move']
df.loc[(df['down_move'] > df['up_move']) & (df['down_move'] > 0), '-dm'] = df['down_move']

df['tr_14'] = df['tr'].ewm(alpha=1/14, adjust=False).mean()
df['+dm_14'] = df['+dm'].ewm(alpha=1/14, adjust=False).mean()
df['-dm_14'] = df['-dm'].ewm(alpha=1/14, adjust=False).mean()

df['+di'] = 100 * (df['+dm_14'] / df['tr_14'])
df['-di'] = 100 * (df['-dm_14'] / df['tr_14'])
df['dx'] = 100 * (df['+di'] - df['-di']).abs() / (df['+di'] + df['-di'])
df['ADX'] = df['dx'].ewm(alpha=1/14, adjust=False).mean()

df = df.dropna().reset_index(drop=True)

# --- PARÁMETROS DE RIESGO Y ESTRATEGIA ---
TAKE_PROFIT_PCT = 5.0          # Venta automática al alcanzar +5% de beneficio
STOP_LOSS_INICIAL_PCT = 3.5    # Pérdida máxima inicial permitida (-3.5%)
TRAILING_STOP_DIST_PCT = 2.0   # Distancia del Trailing Stop desde el máximo alcanzado (2%)
ADX_MINIMO = 22.0              # Umbral de tendencia activa

saldo_usdt = 1000.0
btc_comprado = 0.0
en_posicion = False
precio_compra = 0.0
max_precio_alcanzado = 0.0
operaciones = 0
comision = 0.001 # 0.1% de comisión por operación en Binance

print(f"🚀 Iniciando simulación V5 (EMA + ADX > {ADX_MINIMO} + Trailing Stop {TRAILING_STOP_DIST_PCT}% + TP {TAKE_PROFIT_PCT}%)...")
print("-" * 70)

for i in range(1, len(df)):
    fila_actual = df.iloc[i]
    fila_anterior = df.iloc[i-1]
    
    if not en_posicion:
        cruce_alcista = (fila_anterior['EMA_9'] <= fila_anterior['EMA_21']) and (fila_actual['EMA_9'] > fila_actual['EMA_21'])
        tendencia_alcista = fila_actual['close'] > fila_actual['SMA_200']
        
        if cruce_alcista and fila_actual['RSI'] < 65 and tendencia_alcista and fila_actual['ADX'] > ADX_MINIMO:
            capital_tras_comision = saldo_usdt * (1 - comision)
            precio_compra = fila_actual['close']
            btc_comprado = capital_tras_comision / precio_compra
            max_precio_alcanzado = fila_actual['high']
            saldo_usdt = 0.0
            en_posicion = True
            operaciones += 1
            print(f"🟢 COMPRA: {precio_compra:.2f} USDT | Fecha: {fila_actual['timestamp']} | ADX: {fila_actual['ADX']:.1f}")

    elif en_posicion:
        # Actualizar el precio pico alcanzado durante la vela
        if fila_actual['high'] > max_precio_alcanzado:
            max_precio_alcanzado = fila_actual['high']
            
        precio_actual = fila_actual['close']
        
        # Calcular umbrales de salida
        precio_take_profit = precio_compra * (1 + TAKE_PROFIT_PCT / 100.0)
        precio_stop_inicial = precio_compra * (1 - STOP_LOSS_INICIAL_PCT / 100.0)
        precio_trailing_stop = max_precio_alcanzado * (1 - TRAILING_STOP_DIST_PCT / 100.0)
        
        # El Stop Loss dinámico sube con el precio, pero nunca baja del stop inicial
        stop_loss_efectivo = max(precio_stop_inicial, precio_trailing_stop)
        
        cruce_bajista = (fila_anterior['EMA_9'] >= fila_anterior['EMA_21']) and (fila_actual['EMA_9'] < fila_actual['EMA_21'])
        
        # Verificación de condiciones
        alcanzo_tp = fila_actual['high'] >= precio_take_profit
        alcanzo_sl = fila_actual['low'] <= stop_loss_efectivo
        
        vender = False
        motivo = ""
        precio_salida = precio_actual
        
        if alcanzo_tp:
            vender = True
            motivo = f"Take Profit (+{TAKE_PROFIT_PCT}%)"
            precio_salida = precio_take_profit
        elif alcanzo_sl:
            vender = True
            es_trailing = stop_loss_efectivo > precio_stop_inicial
            motivo = f"Trailing Stop (-{TRAILING_STOP_DIST_PCT}% del máximo)" if es_trailing else f"Stop Loss Inicial (-{STOP_LOSS_INICIAL_PCT}%)"
            precio_salida = stop_loss_efectivo
        elif cruce_bajista:
            vender = True
            motivo = "Cruce Bajista EMA"
            precio_salida = precio_actual
            
        if vender:
            capital_obtenido = btc_comprado * precio_salida
            saldo_usdt = capital_obtenido * (1 - comision)
            
            inversion_inicial = precio_compra * btc_comprado / (1 - comision)
            rentabilidad_neta_operacion = ((saldo_usdt - inversion_inicial) / inversion_inicial) * 100
            
            btc_comprado = 0.0
            en_posicion = False
            print(f"🔴 VENTA ({motivo}): {precio_salida:.2f} USDT | Beneficio Real: {rentabilidad_neta_operacion:.2f}% | Saldo: {saldo_usdt:.2f}")

if en_posicion:
    saldo_usdt = btc_comprado * df.iloc[-1]['close'] * (1 - comision)
    print(f"⚪ POSICIÓN ABIERTA AL FINALIZAR: {df.iloc[-1]['close']:.2f} USDT")

print("-" * 70)
print(f"🏁 RESULTADO FINAL (Con Trailing Stop, Take Profit y Comisiones)")
print(f"Operaciones realizadas: {operaciones}")
print(f"Saldo final: {saldo_usdt:.2f} USDT")
print(f"Rentabilidad neta: {((saldo_usdt - 1000) / 1000) * 100:.2f}%")