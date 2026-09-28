import os
import time
import threading
from flask import Flask
import ccxt
import pandas as pd
import numpy as np
from datetime import datetime
from pymongo import MongoClient

# =========================================================
# 1. BASE DE DATOS (MongoDB) CON RESPALDO LOCAL
# =========================================================
MONGO_URI = os.environ.get("MONGO_URI")

# Estado temporal por si haces pruebas en tu ordenador
estado_local = {
    "_id": "estado_actual",
    "saldo_usd": 1000.0,
    "btc_poseidos": 0.0,
    "precio_compra": 0.0,
    "precio_max_alcanzado": 0.0,
    "en_posicion": False,
    "registro_actividad": "🤖 Bot iniciado..."
}

# El bot decide automáticamente qué memoria usar
if MONGO_URI:
    cliente_mongo = MongoClient(MONGO_URI)
    db = cliente_mongo['trading_bot']
    coleccion_estado = db['estado']
    usar_mongo = True
    print("✅ Conectado a MongoDB (Nube)", flush=True)
else:
    usar_mongo = False
    print("⚠️ AVISO: Ejecutando en tu PC sin MongoDB. En Render sí usará la base de datos.", flush=True)

def obtener_estado():
    if usar_mongo:
        estado = coleccion_estado.find_one({"_id": "estado_actual"})
        if not estado:
            coleccion_estado.insert_one(estado_local)
            return estado_local
        if "precio_max_alcanzado" not in estado:
            estado["precio_max_alcanzado"] = estado.get("precio_compra", 0.0)
        return estado
    else:
        return estado_local

def guardar_estado(estado_actualizado):
    if usar_mongo:
        coleccion_estado.update_one({"_id": "estado_actual"}, {"$set": estado_actualizado})
    else:
        global estado_local
        estado_local = estado_actualizado

def registrar(estado, mensaje):
    fecha_hora = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    log = f"[{fecha_hora}] {mensaje}"
    print(log, flush=True)
    estado["registro_actividad"] = log + "\n" + estado.get("registro_actividad", "")[:1000]

# =========================================================
# 2. SERVIDOR WEB FLASK (Para mantener Render vivo)
# =========================================================
app = Flask(__name__)

@app.route('/')
def index():
    estado = obtener_estado()
    html = f"""
    <html>
    <head>
        <meta http-equiv="refresh" content="30">
        <title>Panel de Trading</title>
    </head>
    <body style="font-family: monospace; background: #121212; color: #00ff66; padding: 20px;">
        <h2>📊 Panel de Trading Bot</h2>
        <p><strong>Saldo Libre:</strong> ${estado['saldo_usd']:,.2f} USDT</p>
        <p><strong>BTC Poseídos:</strong> {estado['btc_poseidos']:.6f} BTC</p>
        <p><strong>En Posición:</strong> {'🟢 SÍ' if estado['en_posicion'] else '🔴 NO'}</p>
        <hr>
        <pre style="font-size: 14px; color: #e0e0e0;">{estado['registro_actividad']}</pre>
    </body>
    </html>
    """
    return html, 200

def run_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, use_reloader=False)

# =========================================================
# 3. INDICADORES MATEMÁTICOS NATIVOS (Sin dependencias)
# =========================================================
def calcular_adx(df, length=14):
    high = df['high']
    low = df['low']
    close = df['close']
    
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low
    
    pos_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    neg_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    
    pos_dm = pd.Series(pos_dm, index=df.index)
    neg_dm = pd.Series(neg_dm, index=df.index)
    
    alpha = 1.0 / length
    tr_smooth = tr.ewm(alpha=alpha, adjust=False).mean()
    pos_di = 100 * (pos_dm.ewm(alpha=alpha, adjust=False).mean() / tr_smooth)
    neg_di = 100 * (neg_dm.ewm(alpha=alpha, adjust=False).mean() / tr_smooth)
    
    dx = 100 * (pos_di - neg_di).abs() / (pos_di + neg_di)
    return dx.ewm(alpha=alpha, adjust=False).mean()

# =========================================================
# 4. LÓGICA DE TRADING
# =========================================================
TRAILING_STOP_PCT = 2.0
TAKE_PROFIT_PCT = 6.0
ADX_UMBRAL = 25

def analizar_y_operar():
    estado = obtener_estado()
    exchange = ccxt.kraken()
    
    ohlcv = exchange.fetch_ohlcv('BTC/USDT', timeframe='15m', limit=100)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    
    df['sma_20'] = df['close'].rolling(20).mean()
    df['ADX_14'] = calcular_adx(df, length=14)
    
    precio_actual = df['close'].iloc[-1]
    media_actual = df['sma_20'].iloc[-1]
    adx_actual = df['ADX_14'].iloc[-1]

    if estado['en_posicion']:
        # Actualizar máximo alcanzado para el Trailing Stop
        if precio_actual > estado.get('precio_max_alcanzado', 0):
            estado['precio_max_alcanzado'] = precio_actual
            
        precio_stop_dinamico = estado['precio_max_alcanzado'] * (1 - (TRAILING_STOP_PCT / 100))
        porcentaje_variacion = ((precio_actual - estado['precio_compra']) / estado['precio_compra']) * 100
        saldo_obtenido = estado['btc_poseidos'] * precio_actual

        if porcentaje_variacion >= TAKE_PROFIT_PCT:
            estado['saldo_usd'] = saldo_obtenido
            estado['btc_poseidos'] = 0.0
            estado['en_posicion'] = False
            estado['precio_max_alcanzado'] = 0.0
            registrar(estado, f"🎯 TAKE PROFIT (+{TAKE_PROFIT_PCT}%) | Venta: ${precio_actual:,.2f}")

        elif precio_actual <= precio_stop_dinamico:
            estado['saldo_usd'] = saldo_obtenido
            estado['btc_poseidos'] = 0.0
            estado['en_posicion'] = False
            estado['precio_max_alcanzado'] = 0.0
            registrar(estado, f"🛡️ TRAILING STOP | Venta a ${precio_actual:,.2f}")

        elif precio_actual < media_actual:
            estado['saldo_usd'] = saldo_obtenido
            estado['btc_poseidos'] = 0.0
            estado['en_posicion'] = False
            estado['precio_max_alcanzado'] = 0.0
            registrar(estado, f"🔴 VENTA TÉCNICA (Bajo Media) | Venta: ${precio_actual:,.2f}")
        else:
            registrar(estado, f"📦 Activo | PnL: {porcentaje_variacion:+.2f}% | Max: ${estado['precio_max_alcanzado']:,.0f}")

    else:
        # Condiciones de Compra: Precio por encima de la media Y tendencia fuerte (ADX > 25)
        if precio_actual > media_actual and adx_actual > ADX_UMBRAL:
            estado['btc_poseidos'] = estado['saldo_usd'] / precio_actual
            estado['precio_compra'] = precio_actual
            estado['precio_max_alcanzado'] = precio_actual
            estado['en_posicion'] = True
            estado['saldo_usd'] = 0.0
            registrar(estado, f"🟢 COMPRA | BTC: ${precio_actual:,.2f} | ADX: {adx_actual:.2f}")
        else:
            registrar(estado, f"💤 Esperando oportunidad | BTC: ${precio_actual:,.2f}")

    guardar_estado(estado)

def bucle_trading():
    print("🚀 [HILO BOT] Analizando mercado...", flush=True)
    time.sleep(3) # Pequeña pausa para que levante Flask primero
    while True:
        try:
            analizar_y_operar()
        except Exception as e:
            print(f"❌ Error en el análisis: {e}", flush=True)
        time.sleep(300) # Revisa el mercado cada 5 minutos

if __name__ == '__main__':
    hilo_bot = threading.Thread(target=bucle_trading)
    hilo_bot.daemon = True
    hilo_bot.start()
    run_server()