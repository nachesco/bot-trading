import ccxt
import pandas as pd
import requests
import time
import os
import csv
from datetime import datetime
from flask import Flask
from threading import Thread

# =========================================================
# CREDENCIALES ACTUALIZADAS
# =========================================================
TELEGRAM_TOKEN = '88628860776:AAE5I29ZKaNQHdxbNwynFXfWiB3HoN9XtAo'
TELEGRAM_CHAT_ID = '402919772'

STOP_LOSS_PCT = 0.02   # 2% de pérdida máxima
TAKE_PROFIT_PCT = 0.04  # 4% de ganancia objetivo

capital_usdt = 1000.0
btc_poseido = 0.0
precio_compra = 0.0

ARCHIVO_CSV = "operaciones.csv"

# --- SERVIDOR FLASK (Mantiene vivo Render) ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot de trading operativo las 24h", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

exchange = ccxt.binance()

def guardar_en_csv(accion, precio, btc, saldo, ganancia_pct=0.0):
    """Guarda un historial de cada operación en un archivo CSV."""
    file_exists = os.path.isfile(ARCHIVO_CSV)
    with open(ARCHIVO_CSV, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["Fecha_Hora", "Accion", "Precio_BTC", "BTC_Cantidad", "Saldo_USDT", "Resultado_Pct"])
        fecha_actual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        writer.writerow([fecha_actual, accion, round(precio, 2), round(btc, 6), round(saldo, 2), f"{ganancia_pct:+.2f}%"])

def enviar_telegram(mensaje):
    """Envía alertas instantáneas a Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": mensaje, "parse_mode": "Markdown"}
    try:
        requests.post(url, data=payload, timeout=5)
    except Exception as e:
        print(f"⚠️ Error al enviar a Telegram: {e}")

def obtener_datos(simbolo='BTC/USDT'):
    """Descarga datos y calcula SMA 5, SMA 20 y RSI."""
    ohlcv = exchange.fetch_ohlcv(simbolo, timeframe='1h', limit=50)
    df = pd.DataFrame(ohlcv, columns=['tiempo', 'apertura', 'maximo', 'minimo', 'cierre', 'volumen'])
    
    df['media_rapida'] = df['cierre'].rolling(window=5).mean()
    df['media_lenta'] = df['cierre'].rolling(window=20).mean()
    
    delta = df['cierre'].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(window=14).mean()
    avg_loss = loss.rolling(window=14).mean()
    rs = avg_gain / (avg_loss + 1e-9)
    df['rsi'] = 100 - (100 / (1 + rs))
    
    return df

def analizar_y_operar():
    global capital_usdt, btc_poseido, precio_compra
    
    df = obtener_datos('BTC/USDT')
    actual = df.iloc[-1]
    anterior = df.iloc[-2]
    precio = actual['cierre']
    rsi_actual = actual['rsi']
    
    hora = datetime.now().strftime("%H:%M:%S")
    print(f"\n================ [{hora}] ================")
    print(f"Precio BTC: ${precio:,.2f} | RSI: {rsi_actual:.1f}")
    
    # GESTIÓN DE POSICIÓN ABIERTA
    if btc_poseido > 0:
        rendimiento = (precio - precio_compra) / precio_compra
        var_pct = rendimiento * 100
        
        # 🛡️ STOP LOSS (-2%)
        if rendimiento <= -STOP_LOSS_PCT:
            capital_usdt = btc_poseido * precio
            msg = (f"🛡️ *[STOP LOSS EJECUTADO]*\n"
                   f"• Precio Venta: ${precio:,.2f}\n"
                   f"• Perdedor: {var_pct:+.2f}%\n"
                   f"• Nuevo Saldo: ${capital_usdt:,.2f} USDT")
            print(msg)
            enviar_telegram(msg)
            guardar_en_csv("VENTA_STOP_LOSS", precio, btc_poseido, capital_usdt, var_pct)
            btc_poseido, precio_compra = 0.0, 0.0

        # 🎯 TAKE PROFIT (+4%)
        elif rendimiento >= TAKE_PROFIT_PCT:
            capital_usdt = btc_poseido * precio
            msg = (f"🎯 *[TAKE PROFIT ALCANZADO]*\n"
                   f"• Precio Venta: ${precio:,.2f}\n"
                   f"• Ganancia: {var_pct:+.2f}%\n"
                   f"• Nuevo Saldo: ${capital_usdt:,.2f} USDT")
            print(msg)
            enviar_telegram(msg)
            guardar_en_csv("VENTA_TAKE_PROFIT", precio, btc_poseido, capital_usdt, var_pct)
            btc_poseido, precio_compra = 0.0, 0.0

        # 🔴 CRUCE BAJISTA DE MEDIAS
        elif anterior['media_rapida'] >= anterior['media_lenta'] and actual['media_rapida'] < actual['media_lenta']:
            capital_usdt = btc_poseido * precio
            msg = (f"🔴 *[VENTA POR ESTRATEGIA]*\n"
                   f"• Precio Venta: ${precio:,.2f}\n"
                   f"• Resultado: {var_pct:+.2f}%\n"
                   f"• Nuevo Saldo: ${capital_usdt:,.2f} USDT")
            print(msg)
            enviar_telegram(msg)
            guardar_en_csv("VENTA_CRUCE", precio, btc_poseido, capital_usdt, var_pct)
            btc_poseido, precio_compra = 0.0, 0.0

    # EVALUACIÓN DE COMPRA
    elif btc_poseido == 0 and capital_usdt > 0:
        cruce_alcista = (anterior['media_rapida'] <= anterior['media_lenta']) and (actual['media_rapida'] > actual['media_lenta'])
        rsi_saludable = rsi_actual < 65
        
        if cruce_alcista and rsi_saludable:
            btc_poseido = capital_usdt / precio
            precio_compra = precio
            capital_usdt = 0.0
            
            sl_precio = precio_compra * (1 - STOP_LOSS_PCT)
            tp_precio = precio_compra * (1 + TAKE_PROFIT_PCT)
            
            msg = (f"🟢 *[COMPRA SIMULADA EJECUTADA]*\n\n"
                   f"• Entrada: ${precio:,.2f}\n"
                   f"• RSI: {rsi_actual:.1f}\n"
                   f"🛡️ Stop Loss: ${sl_precio:,.2f} (-2%)\n"
                   f"🎯 Take Profit: ${tp_precio:,.2f} (+4%)")
            print(msg)
            enviar_telegram(msg)
            guardar_en_csv("COMPRA", precio, btc_poseido, 0.0, 0.0)

# --- INICIO ---
if __name__ == "__main__":
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()

    enviar_telegram("🤖 *Bot listo con nuevo token.* Registro CSV activo, Stop Loss (2%) y Take Profit (4%) configurados.")

    while True:
        try:
            analizar_y_operar()
            time.sleep(300)
        except Exception as e:
            print(f"⚠️ Error de red: {e}. Reintentando en 15s...")
            time.sleep(15)