import os
import time
import threading
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
import ccxt
import pandas as pd
import requests
from datetime import datetime

# Las claves ahora se leen de forma segura desde Render
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8890611356:AAFjh56u6yPL6xyfIdIzxMTH1TyCRepRlUk")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "402919772")

# =========================================================
# 1. SERVIDOR HTTP PARA MANTENER RENDER ACTIVO
# =========================================================
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write("🤖 Bot de Trading cuantitativo activo en Render!".encode('utf-8'))
    def log_message(self, format, *args):
        return

def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), DummyHandler)
    print(f"[SERVIDOR] Servidor HTTP listo en el puerto {port}")
    server.serve_forever()

def enviar_telegram(mensaje: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[ERROR] Falta configurar TELEGRAM_TOKEN o TELEGRAM_CHAT_ID en Render")
        return
        
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": mensaje, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"[ERROR TELEGRAM]: {e}")

# =========================================================
# 2. SISTEMA DE GUARDADO (Para no perder el saldo si Render se reinicia)
# =========================================================
ARCHIVO_ESTADO = "estado_trading.json"
STOP_LOSS_PCT = 0.02
TAKE_PROFIT_PCT = 0.04

saldo_usdt = 1000.0
btc_poseido = 0.0
precio_entrada = 0.0
en_posicion = False

def cargar_estado():
    global saldo_usdt, btc_poseido, precio_entrada, en_posicion
    if os.path.exists(ARCHIVO_ESTADO):
        try:
            with open(ARCHIVO_ESTADO, "r") as f:
                estado = json.load(f)
                saldo_usdt = estado.get("saldo_usdt", 1000.0)
                btc_poseido = estado.get("btc_poseido", 0.0)
                precio_entrada = estado.get("precio_entrada", 0.0)
                en_posicion = estado.get("en_posicion", False)
        except:
            pass

def guardar_estado():
    estado = {
        "saldo_usdt": saldo_usdt,
        "btc_poseido": btc_poseido,
        "precio_entrada": precio_entrada,
        "en_posicion": en_posicion
    }
    with open(ARCHIVO_ESTADO, "w") as f:
        json.dump(estado, f)

exchange = ccxt.binance({'enableRateLimit': True})

def obtener_datos(simbolo='BTC/USDT'):
    try:
        ohlcv = exchange.fetch_ohlcv(simbolo, timeframe='1h', limit=50)
        df = pd.DataFrame(ohlcv, columns=['tiempo', 'open', 'high', 'low', 'close', 'volume'])
        df['sma_rapida'] = df['close'].rolling(5).mean()
        df['sma_lenta'] = df['close'].rolling(20).mean()
        
        delta = df['close'].diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        avg_gain = gain.rolling(window=14).mean()
        avg_loss = loss.rolling(window=14).mean()
        rs = avg_gain / avg_loss
        df['rsi'] = 100 - (100 / (1 + rs))
        return df
    except Exception as e:
        print(f"[ERROR BINANCE]: {e}")
        return None

# =========================================================
# 3. ESCUCHADOR DE COMANDOS
# =========================================================
def escuchar_comandos_telegram():
    offset = None
    while True:
        try:
            if not TELEGRAM_TOKEN:
                time.sleep(10)
                continue
                
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
            params = {"timeout": 20, "offset": offset}
            res = requests.get(url, params=params, timeout=25).json()
            
            if "result" in res:
                for update in res["result"]:
                    offset = update["update_id"] + 1
                    message = update.get("message", {})
                    texto = message.get("text", "").strip().lower()
                    chat_id_remitente = str(message.get("chat", {}).get("id", ""))

                    if chat_id_remitente != str(TELEGRAM_CHAT_ID):
                        continue

                    df = obtener_datos('BTC/USDT')
                    if df is None: continue
                    
                    precio_actual = df.iloc[-1]['close']
                    rsi_actual = df.iloc[-1]['rsi']

                    if texto.startswith("/saldo"):
                        valor_btc = btc_poseido * precio_actual
                        valor_total = saldo_usdt + valor_btc
                        pnl = valor_total - 1000.0
                        enviar_telegram(
                            f"💼 <b>ESTADO DE TU CARTERA</b>\n\n"
                            f"💵 <b>USDT:</b> ${saldo_usdt:,.2f}\n"
                            f"🪙 <b>BTC:</b> {btc_poseido:.6f}\n"
                            f"📊 <b>Total Estimado:</b> ${valor_total:,.2f}\n"
                            f"📈 <b>P/L:</b> ${pnl:+,.2f}"
                        )
                    elif texto.startswith("/estado"):
                        if en_posicion:
                            pnl_pct = ((precio_actual - precio_entrada) / precio_entrada) * 100
                            pos = f"🟢 <b>COMPRADO</b> | Rto: {pnl_pct:+.2f}%"
                        else:
                            pos = "⚪ <b>EN ESPERA</b>"

                        enviar_telegram(
                            f"🤖 <b>MERCADO BTC/USDT</b>\n\n"
                            f"💰 <b>Precio:</b> ${precio_actual:,.2f}\n"
                            f"📊 <b>RSI:</b> {rsi_actual:.1f}\n"
                            f"📌 <b>Estado:</b> {pos}"
                        )
                    elif texto.startswith("/start") or texto.startswith("/ayuda"):
                        enviar_telegram("👋 Comandos: <b>/saldo</b> o <b>/estado</b>")
        except Exception:
            time.sleep(5)
        time.sleep(1)

# =========================================================
# 4. ESTRATEGIA (Corregida para usar velas cerradas)
# =========================================================
def ejecutar_estrategia():
    global saldo_usdt, btc_poseido, precio_entrada, en_posicion
    simbolo = 'BTC/USDT'
    
    df = obtener_datos(simbolo)
    if df is None: return
    
    precio_actual = df.iloc[-1]['close']  # Precio en tiempo real
    
    # Usamos las velas cerradas para calcular cruces y no tener señales falsas
    vela_cerrada_actual = df.iloc[-2]
    vela_cerrada_anterior = df.iloc[-3]
    
    if en_posicion:
        rendimiento = (precio_actual - precio_entrada) / precio_entrada
        var_pct = rendimiento * 100
        
        # Stop Loss
        if rendimiento <= -STOP_LOSS_PCT:
            saldo_usdt = btc_poseido * precio_actual
            enviar_telegram(f"🛡️ <b>STOP LOSS EJECUTADO</b>\nPrecio: ${precio_actual:,.2f}\nPérdida: {var_pct:.2f}%\nSaldo: ${saldo_usdt:,.2f}")
            en_posicion = False
            btc_poseido = 0.0
            guardar_estado()
            
        # Take Profit
        elif rendimiento >= TAKE_PROFIT_PCT:
            saldo_usdt = btc_poseido * precio_actual
            enviar_telegram(f"🎯 <b>TAKE PROFIT ALCANZADO</b>\nPrecio: ${precio_actual:,.2f}\nGanancia: +{var_pct:.2f}%\nSaldo: ${saldo_usdt:,.2f}")
            en_posicion = False
            btc_poseido = 0.0
            guardar_estado()
            
        # Venta Técnica
        elif vela_cerrada_anterior['sma_rapida'] >= vela_cerrada_anterior['sma_lenta'] and vela_cerrada_actual['sma_rapida'] < vela_cerrada_actual['sma_lenta']:
            saldo_usdt = btc_poseido * precio_actual
            enviar_telegram(f"🔴 <b>VENTA TÉCNICA (Cruce Bajista)</b>\nPrecio: ${precio_actual:,.2f}\nResultado: {var_pct:+.2f}%\nSaldo: ${saldo_usdt:,.2f}")
            en_posicion = False
            btc_poseido = 0.0
            guardar_estado()

    else:
        cruce_alcista = (vela_cerrada_anterior['sma_rapida'] <= vela_cerrada_anterior['sma_lenta']) and (vela_cerrada_actual['sma_rapida'] > vela_cerrada_actual['sma_lenta'])
        rsi_favorable = vela_cerrada_actual['rsi'] < 60
        
        if cruce_alcista and rsi_favorable:
            btc_poseido = saldo_usdt / precio_actual
            precio_entrada = precio_actual
            
            enviar_telegram(f"🟢 <b>COMPRA SIMULADA</b>\nPrecio: ${precio_actual:,.2f}\nCantidad: {btc_poseido:.6f} BTC\nRSI: {vela_cerrada_actual['rsi']:.1f}")
            en_posicion = True
            saldo_usdt = 0.0
            guardar_estado()

# =========================================================
# 5. ARRANQUE PRINCIPAL
# =========================================================
if __name__ == '__main__':
    cargar_estado()
    threading.Thread(target=run_dummy_server, daemon=True).start()
    threading.Thread(target=escuchar_comandos_telegram, daemon=True).start()
    
    enviar_telegram("🧠 <b>BOT ACTUALIZADO Y SEGURO INICIADO</b>\n\nEscribe <b>/saldo</b> o <b>/estado</b> para comprobar la conexión.")
    
    while True:
        try:
            ejecutar_estrategia()
        except Exception as e:
            print(f"[ERROR]: {e}")
        time.sleep(300)
