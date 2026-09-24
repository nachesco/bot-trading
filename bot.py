import os
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import ccxt
import pandas as pd
import requests
from datetime import datetime

# =========================================================
# CONFIGURACIÓN Y CREDENCIALES DE TELEGRAM
# =========================================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8628860776:AAEQHlVjzM1fXFhBPuTPjrhzyoDKBamaKII")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "402919772")

def enviar_telegram(mensaje: str):
    """Envía notificaciones a Telegram en formato HTML."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensaje,
        "parse_mode": "HTML"
    }
    try:
        respuesta = requests.post(url, json=payload, timeout=10)
        if respuesta.status_code != 200:
            print(f"[ERROR TELEGRAM]: Respuesta {respuesta.status_code} - {respuesta.text}")
    except Exception as e:
        print(f"[ERROR TELEGRAM]: Error de conexión: {e}")

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
    print(f"[SERVIDOR] Servidor HTTP de Render listo en el puerto {port}")
    server.serve_forever()

# =========================================================
# 2. ESTADO Y PARÁMETROS DE PAPER TRADING (SIMULACIÓN)
# =========================================================
STOP_LOSS_PCT = 0.02   # Protección: Máximo 2% de pérdida
TAKE_PROFIT_PCT = 0.04  # Objetivo: 4% de ganancia

# Cartera simulada inicial
saldo_usdt = 1000.0
btc_poseido = 0.0
precio_entrada = 0.0
en_posicion = False

exchange = ccxt.binance({'enableRateLimit': True})

def obtener_datos(simbolo='BTC/USDT'):
    """Obtiene velas de 1 hora y calcula Medias Móviles y RSI."""
    ohlcv = exchange.fetch_ohlcv(simbolo, timeframe='1h', limit=50)
    df = pd.DataFrame(ohlcv, columns=['tiempo', 'open', 'high', 'low', 'close', 'volume'])
    
    # Medias Móviles
    df['sma_rapida'] = df['close'].rolling(5).mean()
    df['sma_lenta'] = df['close'].rolling(20).mean()
    
    # Cálculo del indicador RSI (14 periodos)
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(window=14).mean()
    avg_loss = loss.rolling(window=14).mean()
    rs = avg_gain / avg_loss
    df['rsi'] = 100 - (100 / (1 + rs))
    
    return df

def ejecutar_estrategia():
    global saldo_usdt, btc_poseido, precio_entrada, en_posicion
    
    simbolo = 'BTC/USDT'
    df = obtener_datos(simbolo)
    
    actual = df.iloc[-1]
    anterior = df.iloc[-2]
    precio_actual = actual['close']
    rsi_actual = actual['rsi']
    
    fecha_hora = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    print(f"[{fecha_hora}] {simbolo} | Precio: ${precio_actual:,.2f} | RSI: {rsi_actual:.1f} | Saldo Simulado: ${saldo_usdt:,.2f}")

    # ---------------------------------------------------------
    # CASO A: TENEMOS UNA POSICIÓN ABIERTA (Gestionar Riesgo)
    # ---------------------------------------------------------
    if en_posicion:
        rendimiento = (precio_actual - precio_entrada) / precio_entrada
        var_pct = rendimiento * 100
        
        # 1. STOP LOSS ALCANZADO (-2%)
        if rendimiento <= -STOP_LOSS_PCT:
            saldo_usdt = btc_poseido * precio_actual
            msg = (
                f"🛡️ <b>[STOP LOSS EJECUTADO]</b>\n\n"
                f"• <b>Activo:</b> {simbolo}\n"
                f"• <b>Precio Venta:</b> ${precio_actual:,.2f}\n"
                f"• <b>Pérdida recortada:</b> {var_pct:.2f}%\n"
                f"💰 <b>Nuevo Saldo Total:</b> ${saldo_usdt:,.2f} USDT"
            )
            print(f"[VENTA - STOP LOSS] {msg}")
            enviar_telegram(msg)
            en_posicion = False
            btc_poseido = 0.0

        # 2. TAKE PROFIT ALCANZADO (+4%)
        elif rendimiento >= TAKE_PROFIT_PCT:
            saldo_usdt = btc_poseido * precio_actual
            msg = (
                f"🎯 <b>[TAKE PROFIT ALCANZADO]</b>\n\n"
                f"• <b>Activo:</b> {simbolo}\n"
                f"• <b>Precio Venta:</b> ${precio_actual:,.2f}\n"
                f"• <b>Ganancia Asegurada:</b> +{var_pct:.2f}%\n"
                f"💰 <b>Nuevo Saldo Total:</b> ${saldo_usdt:,.2f} USDT"
            )
            print(f"[VENTA - TAKE PROFIT] {msg}")
            enviar_telegram(msg)
            en_posicion = False
            btc_poseido = 0.0

        # 3. VENTA TÉCNICA (Cruce Bajista de Medias)
        elif anterior['sma_rapida'] >= anterior['sma_lenta'] and actual['sma_rapida'] < actual['sma_lenta']:
            saldo_usdt = btc_poseido * precio_actual
            msg = (
                f"🔴 <b>[VENTA TÉCNICA - CRUCE BAJISTA]</b>\n\n"
                f"• <b>Activo:</b> {simbolo}\n"
                f"• <b>Precio Venta:</b> ${precio_actual:,.2f}\n"
                f"• <b>Resultado Operación:</b> {var_pct:+.2f}%\n"
                f"💰 <b>Nuevo Saldo Total:</b> ${saldo_usdt:,.2f} USDT"
            )
            print(f"[VENTA TÉCNICA] {msg}")
            enviar_telegram(msg)
            en_posicion = False
            btc_poseido = 0.0

    # ---------------------------------------------------------
    # CASO B: NO TENEMOS POSICIÓN (Buscar Oportunidad de Compra)
    # ---------------------------------------------------------
    else:
        cruce_alcista = (anterior['sma_rapida'] <= anterior['sma_lenta']) and (actual['sma_rapida'] > actual['sma_lenta'])
        rsi_favorable = rsi_actual < 60  # Evita comprar sobrecalentado
        
        if cruce_alcista and rsi_favorable:
            btc_poseido = saldo_usdt / precio_actual
            precio_entrada = precio_actual
            
            sl_precio = precio_entrada * (1 - STOP_LOSS_PCT)
            tp_precio = precio_entrada * (1 + TAKE_PROFIT_PCT)
            
            msg = (
                f"🟢 <b>[COMPRA SIMULADA EJECUTADA]</b>\n\n"
                f"• <b>Activo:</b> {simbolo}\n"
                f"• <b>Precio Compra:</b> ${precio_actual:,.2f}\n"
                f"• <b>Cantidad:</b> {btc_poseido:.6f} BTC\n"
                f"• <b>RSI Actual:</b> {rsi_actual:.1f}\n"
                f"-----------------------------------\n"
                f"🛡️ <b>Stop Loss (-2%):</b> ${sl_precio:,.2f}\n"
                f"🎯 <b>Take Profit (+4%):</b> ${tp_precio:,.2f}"
            )
            print(f"[COMPRA] {msg}")
            enviar_telegram(msg)
            en_posicion = True
            saldo_usdt = 0.0

# =========================================================
# 3. BUCLE DE EJECUCIÓN
# =========================================================
if __name__ == '__main__':
    print("=== INICIANDO MOTOR DE TRADING CUANTITATIVO CON PAPER TRADING ===")
    
    # 1. Iniciar servidor de Render
    server_thread = threading.Thread(target=run_dummy_server, daemon=True)
    server_thread.start()

    # 2. Notificación inicial en Telegram
    enviar_telegram(
        "🧠 <b>[MODO PAPER TRADING ACTIVADO]</b>\n\n"
        "• <b>Capital Ficticio:</b> $1,000.00 USDT\n"
        "• <b>Stop Loss:</b> 2%\n"
        "• <b>Take Profit:</b> 4%\n"
        "• <b>Filtro RSI:</b> Sí (< 60)\n\n"
        "<i>El bot analizará el mercado en silencio y te notificará únicamente al ejecutar operaciones.</i>"
    )
    
    # 3. Bucle de monitoreo continuo (revisa cada 5 minutos)
    while True:
        try:
            ejecutar_estrategia()
            time.sleep(300)  # Revisa el mercado cada 5 minutos (300 segundos)
        except Exception as e:
            print(f"[ERROR EN BUCLE]: {e}")
            time.sleep(30)