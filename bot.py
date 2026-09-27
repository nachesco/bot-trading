import ccxt
import pandas as pd
import time
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pymongo import MongoClient
from datetime import datetime

# --- CONFIGURACIÓN DE RIESGO Y ESTRATEGIA ---
STOP_LOSS_PCT = 2.0
TAKE_PROFIT_PCT = 4.0

# --- CONEXIÓN A MONGODB ---
MONGO_URI = os.environ.get("MONGO_URI")
if MONGO_URI:
    cliente_mongo = MongoClient(MONGO_URI)
    db = cliente_mongo['trading_bot']
    coleccion_estado = db['estado']
else:
    print("⚠️ ADVERTENCIA: No se encontró MONGO_URI en las variables de Render.", flush=True)

def obtener_estado():
    """Lee el estado de la base de datos o lo crea si es la primera ejecución."""
    estado = coleccion_estado.find_one({"_id": "estado_actual"})
    if not estado:
        estado = {
            "_id": "estado_actual",
            "saldo_usd": 1000.0,
            "btc_poseidos": 0.0,
            "precio_compra": 0.0,
            "en_posicion": False,
            "historial": []
        }
        coleccion_estado.insert_one(estado)
    return estado

def guardar_estado(estado):
    """Guarda los cambios en la base de datos en la nube."""
    coleccion_estado.update_one({"_id": "estado_actual"}, {"$set": estado})

def registrar_evento(estado, texto):
    """Añade un registro al historial y lo imprime en los logs de Render."""
    fecha_hora = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    linea = f"[{fecha_hora}] {texto}"
    
    historial = estado.get('historial', [])
    historial.insert(0, linea)
    estado['historial'] = historial[:10]
    
    # EL SECRETO PARA RENDER: flush=True fuerza a imprimir en el log al instante
    print(linea, flush=True)

# --- PANEL WEB INTERACTIVO ---
class WebHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        
        estado = obtener_estado()
        historial_items = "".join([f"<li>{item}</li>" for item in estado.get('historial', [])])
        
        html = f"""
        <html>
        <head>
            <title>Panel del Bot de Trading</title>
            <meta http-equiv="refresh" content="30">
            <style>
                body {{ font-family: monospace; padding: 20px; background-color: #121212; color: #00ff66; }}
                h2 {{ color: #ffffff; border-bottom: 1px solid #333; padding-bottom: 10px; }}
                .box {{ background: #1e1e1e; padding: 15px; border-radius: 8px; margin-bottom: 20px; border: 1px solid #333; }}
                ul {{ list-style-type: none; padding: 0; margin: 0; }}
                li {{ padding: 8px 0; border-bottom: 1px solid #2a2a2a; color: #cccccc; font-size: 14px; }}
                .stat {{ margin: 6px 0; font-size: 16px; }}
            </style>
        </head>
        <body>
            <h2>📈 Panel de Control (Simulador BTC)</h2>
            <div class="box">
                <div class="stat"><strong>Saldo Libre:</strong> ${estado['saldo_usd']:,.2f} USDT</div>
                <div class="stat"><strong>BTC Poseídos:</strong> {estado['btc_poseidos']:.6f} BTC</div>
                <div class="stat"><strong>Estado:</strong> {'🟢 EN POSICIÓN' if estado['en_posicion'] else '🔴 LIQUIDEZ (USDT)'}</div>
                {'<div class="stat"><strong>Precio Entrada:</strong> $' + f"{estado['precio_compra']:,.2f}" + '</div>' if estado['en_posicion'] else ''}
            </div>
            <h3>📋 Última Actividad (Auto-refresco 30s)</h3>
            <div class="box">
                <ul>{historial_items if historial_items else '<li>Sin actividad registrada aún.</li>'}</ul>
            </div>
        </body>
        </html>
        """
        self.wfile.write(html.encode('utf-8'))

    def log_message(self, format, *args):
        pass

def run_server():
    port = int(os.environ.get("PORT", 10000))
    print(f"🌐 [HILO WEB] Arrancando panel de control en el puerto {port}...", flush=True)
    server = HTTPServer(('0.0.0.0', port), WebHandler)
    server.serve_forever()

# --- LÓGICA DE TRADING ---
def analizar_y_operar():
    estado = obtener_estado()
    exchange = ccxt.binance()
    
    ohlcv = exchange.fetch_ohlcv('BTC/USDT', timeframe='15m', limit=50)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['sma_20'] = df['close'].rolling(20).mean()
    
    precio_actual = df['close'].iloc[-1]
    media_actual = df['sma_20'].iloc[-1]

    if estado['en_posicion']:
        porcentaje_variacion = ((precio_actual - estado['precio_compra']) / estado['precio_compra']) * 100
        saldo_obtenido = estado['btc_poseidos'] * precio_actual
        ganancia_usd = saldo_obtenido - (estado['btc_poseidos'] * estado['precio_compra'])

        # TAKE PROFIT (+4%)
        if porcentaje_variacion >= TAKE_PROFIT_PCT:
            estado['saldo_usd'] = saldo_obtenido
            estado['btc_poseidos'] = 0.0
            estado['en_posicion'] = False
            registrar_evento(estado, f"🎯 TAKE PROFIT (+{TAKE_PROFIT_PCT}%) | Venta: ${precio_actual:,.2f} | Ganancia: +${ganancia_usd:,.2f} USDT")

        # STOP LOSS (-2%)
        elif porcentaje_variacion <= -STOP_LOSS_PCT:
            estado['saldo_usd'] = saldo_obtenido
            estado['btc_poseidos'] = 0.0
            estado['en_posicion'] = False
            registrar_evento(estado, f"🛑 STOP LOSS (-{STOP_LOSS_PCT}%) | Venta: ${precio_actual:,.2f} | Pérdida: -${abs(ganancia_usd):,.2f} USDT")

        # VENTA POR ESTRATEGIA (Cruce bajista SMA 20)
        elif precio_actual < media_actual:
            estado['saldo_usd'] = saldo_obtenido
            estado['btc_poseidos'] = 0.0
            estado['en_posicion'] = False
            registrar_evento(estado, f"🔴 VENTA ESTRATÉGICA (Bajo SMA 20) | Venta: ${precio_actual:,.2f} | Resultado: ${ganancia_usd:+,.2f} USDT")
        
        else:
            registrar_evento(estado, f"📦 Posición Activa | BTC: ${precio_actual:,.2f} | Entrada: ${estado['precio_compra']:,.2f} | PnL: {porcentaje_variacion:+.2f}%")

    else:
        # COMPRA POR ESTRATEGIA (Cruce alcista SMA 20)
        if precio_actual > media_actual:
            estado['btc_poseidos'] = estado['saldo_usd'] / precio_actual
            estado['precio_compra'] = precio_actual
            estado['en_posicion'] = True
            estado['saldo_usd'] = 0.0
            registrar_evento(estado, f"🟢 COMPRA EJECUTADA | Entrada: ${precio_actual:,.2f} | SMA 20: ${media_actual:,.2f}")
        else:
            registrar_evento(estado, f"💤 En Espera | BTC: ${precio_actual:,.2f} | SMA 20: ${media_actual:,.2f}")

    guardar_estado(estado)

def bucle_trading():
    """Esta función mantiene vivo al bot en segundo plano"""
    print("🚀 [HILO BOT] Iniciando comprobaciones de mercado...", flush=True)
    time.sleep(5) # Espera 5 segundos para asegurar que el panel web se enciende antes
    while True:
        try:
            analizar_y_operar()
        except Exception as e:
            print(f"❌ Error en bucle principal: {e}", flush=True)
        # Espera 5 minutos (300 segundos) entre consultas a Binance
        time.sleep(300)

if __name__ == '__main__':
    if not MONGO_URI:
        print("❌ Error crítico: Falta MONGO_URI en Render.", flush=True)
    else:
        print("=== BOT INICIADO ===", flush=True)
        
        # 1. Metemos tu bot en un hilo secundario fantasma (daemon)
        hilo_bot = threading.Thread(target=bucle_trading)
        hilo_bot.daemon = True
        hilo_bot.start()
        
        # 2. Dejamos el panel web (HTTPServer) en el hilo principal
        run_server()