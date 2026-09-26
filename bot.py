import os
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import ccxt
import pandas as pd
from datetime import datetime

# =========================================================
# 1. ESTADO GLOBAL (Para mostrarlo en la web)
# =========================================================
registro_actividad = "🤖 Bot iniciado. Esperando el primer análisis de mercado..."

# =========================================================
# 2. SERVIDOR HTTP (Mantiene Render vivo y te muestra datos)
# =========================================================
class WebHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        
        # Esta es la página web que verás al entrar a tu enlace de Render
        html = f"""
        <html>
        <head>
            <title>Mi Bot de Trading</title>
            <meta http-equiv="refresh" content="30"> <!-- Se actualiza solo cada 30 seg -->
        </head>
        <body style="font-family: monospace; padding: 20px; background-color: #1e1e1e; color: #00ff00;">
            <h2>📊 Panel de Paper Trading</h2>
            <hr>
            <pre style="font-size: 16px;">{registro_actividad}</pre>
        </body>
        </html>
        """
        self.wfile.write(html.encode('utf-8'))

    def log_message(self, format, *args):
        pass # Silenciamos los logs del servidor para no manchar la consola

def run_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), WebHandler)
    print(f"[SERVIDOR] Panel web listo en el puerto {port}")
    server.serve_forever()

# =========================================================
# 3. PARÁMETROS DE PAPER TRADING
# =========================================================
STOP_LOSS_PCT = 0.02
TAKE_PROFIT_PCT = 0.04

saldo_usdt = 1000.0
btc_poseido = 0.0
precio_entrada = 0.0
en_posicion = False

exchange = ccxt.binance({'enableRateLimit': True})

def obtener_datos(simbolo='BTC/USDT'):
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

def ejecutar_estrategia():
    global saldo_usdt, btc_poseido, precio_entrada, en_posicion, registro_actividad
    
    simbolo = 'BTC/USDT'
    df = obtener_datos(simbolo)
    
    actual = df.iloc[-1]
    anterior = df.iloc[-2]
    precio_actual = actual['close']
    rsi_actual = actual['rsi']
    
    fecha_hora = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    
    # Actualizamos lo que se ve en la consola y en la web
    estado_actual = f"[{fecha_hora}] {simbolo} | Precio: ${precio_actual:,.2f} | RSI: {rsi_actual:.1f} | Saldo: ${saldo_usdt:,.2f}"
    print(estado_actual)
    registro_actividad = estado_actual + "\n\nÚLTIMA ACCIÓN:\n"

    if en_posicion:
        rendimiento = (precio_actual - precio_entrada) / precio_entrada
        var_pct = rendimiento * 100
        
        if rendimiento <= -STOP_LOSS_PCT:
            saldo_usdt = btc_poseido * precio_actual
            msg = f"🛑 [STOP LOSS] Venta a ${precio_actual:,.2f} ({var_pct:.2f}%). Saldo: ${saldo_usdt:,.2f}"
            print(msg)
            registro_actividad += msg
            en_posicion = False
            btc_poseido = 0.0

        elif rendimiento >= TAKE_PROFIT_PCT:
            saldo_usdt = btc_poseido * precio_actual
            msg = f"🎯 [TAKE PROFIT] Venta a ${precio_actual:,.2f} (+{var_pct:.2f}%). Saldo: ${saldo_usdt:,.2f}"
            print(msg)
            registro_actividad += msg
            en_posicion = False
            btc_poseido = 0.0

        elif anterior['sma_rapida'] >= anterior['sma_lenta'] and actual['sma_rapida'] < actual['sma_lenta']:
            saldo_usdt = btc_poseido * precio_actual
            msg = f"📉 [VENTA TÉCNICA] Venta a ${precio_actual:,.2f} ({var_pct:+.2f}%). Saldo: ${saldo_usdt:,.2f}"
            print(msg)
            registro_actividad += msg
            en_posicion = False
            btc_poseido = 0.0
        else:
            registro_actividad += f"Manteniendo posición. Rendimiento actual: {var_pct:+.2f}%"

    else:
        cruce_alcista = (anterior['sma_rapida'] <= anterior['sma_lenta']) and (actual['sma_rapida'] > actual['sma_lenta'])
        rsi_favorable = rsi_actual < 60
        
        if cruce_alcista and rsi_favorable:
            btc_poseido = saldo_usdt / precio_actual
            precio_entrada = precio_actual
            
            msg = f"🟢 [COMPRA] {btc_poseido:.4f} BTC a ${precio_actual:,.2f}. SL: -2%, TP: +4%"
            print(msg)
            registro_actividad += msg
            en_posicion = True
            saldo_usdt = 0.0
        else:
            registro_actividad += "Buscando oportunidad de compra (Esperando cruce alcista y RSI < 60)..."

# =========================================================
# 4. BUCLE PRINCIPAL
# =========================================================
if __name__ == '__main__':
    print("=== MODO PAPER TRADING INICIADO (SIN TELEGRAM) ===")
    
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    while True:
        try:
            ejecutar_estrategia()
            time.sleep(300) # Analiza cada 5 minutos
        except Exception as e:
            error_msg = f"[ERROR]: {e}"
            print(error_msg)
            registro_actividad = error_msg
            time.sleep(30)