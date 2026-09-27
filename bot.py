import ccxt
import pandas as pd
import pandas_ta as ta  # NUEVA: Librería para calcular el ADX y otros indicadores
import time
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pymongo import MongoClient
from datetime import datetime

# --- CONFIGURACIÓN DE RIESGO Y ESTRATEGIA (V5) ---
TRAILING_STOP_PCT = 2.0  # El stop de protección perseguirá al precio a un 2% de distancia
TAKE_PROFIT_PCT = 6.0    # TP más amplio para dejar correr las ganancias
ADX_UMBRAL = 25          # Filtro de ruido: Solo entramos si el mercado tiene fuerza real

# --- CONEXIÓN A MONGODB ---
MONGO_URI = os.environ.get("MONGO_URI")
if MONGO_URI:
    cliente_mongo = MongoClient(MONGO_URI)
    db = cliente_mongo['trading_bot']
    coleccion_estado = db['estado']
else:
    print("⚠️ ADVERTENCIA: No se encontró MONGO_URI en las variables de Render.", flush=True)

def obtener_estado():
    estado = coleccion_estado.find_one({"_id": "estado_actual"})
    if not estado:
        estado = {
            "_id": "estado_actual",
            "saldo_usd": 1000.0,
            "btc_poseidos": 0.0,
            "precio_compra": 0.0,
            "precio_max_alcanzado": 0.0, # NUEVO: Memoria para el Trailing Stop
            "en_posicion": False,
            "historial": []
        }
        coleccion_estado.insert_one(estado)
    
    # Parche de seguridad para actualizar tu base de datos antigua a la nueva versión
    if "precio_max_alcanzado" not in estado:
        estado["precio_max_alcanzado"] = estado.get("precio_compra", 0.0)
        
    return estado

def guardar_estado(estado):
    coleccion_estado.update_one({"_id": "estado_actual"}, {"$set": estado})

def registrar_evento(estado, texto):
    fecha_hora = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    linea = f"[{fecha_hora}] {texto}"
    historial = estado.get('historial', [])
    historial.insert(0, linea)
    estado['historial'] = historial[:15] # Guardamos los últimos 15 eventos
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
            <title>Panel del Bot de Trading V5</title>
            <meta http-equiv="refresh" content="30">
            <style>
                body {{ font-family: monospace; padding: 20px; background-color: #121212; color: #00ff66; }}
                h2 {{ color: #ffffff; border-bottom: 1px solid #333; padding-bottom: 10px; }}
                .box {{ background: #1e1e1e; padding: 15px; border-radius: 8px; margin-bottom: 20px; border: 1px solid #333; }}
                ul {{ list-style-type: none; padding: 0; margin: 0; }}
                li {{ padding: 8px 0; border-bottom: 1px solid #2a2a2a; color: #cccccc; font-size: 14px; }}
                .stat {{ margin: 6px 0; font-size: 16px; }}
                .highlight {{ color: #ffeb3b; font-weight: bold; }}
            </style>
        </head>
        <body>
            <h2>🧠 Panel de Control - Estrategia V5 (ADX + Trailing Stop)</h2>
            <div class="box">
                <div class="stat"><strong>Saldo Libre:</strong> ${estado['saldo_usd']:,.2f} USDT</div>
                <div class="stat"><strong>BTC Poseídos:</strong> {estado['btc_poseidos']:.6f} BTC</div>
                <div class="stat"><strong>Estado:</strong> {'🟢 EN POSICIÓN' if estado['en_posicion'] else '🔴 LIQUIDEZ (USDT)'}</div>
                {'<div class="stat"><strong>Precio Entrada:</strong> $' + f"{estado['precio_compra']:,.2f}" + '</div>' if estado['en_posicion'] else ''}
                {'<div class="stat highlight"><strong>Precio Max Alcanzado (Para Stop):</strong> $' + f"{estado['precio_max_alcanzado']:,.2f}" + '</div>' if estado['en_posicion'] else ''}
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
    print(f"🌐 [HILO WEB] Arrancando panel V5 en el puerto {port}...", flush=True)
    server = HTTPServer(('0.0.0.0', port), WebHandler)
    server.serve_forever()

# --- CEREBRO: LÓGICA DE TRADING ---
def analizar_y_operar():
    estado = obtener_estado()
    exchange = ccxt.kraken()
    
    # Pedimos 100 velas para que el ADX tenga histórico suficiente para calcularse
    ohlcv = exchange.fetch_ohlcv('BTC/USDT', timeframe='15m', limit=100)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    
    # CALCULAMOS INDICADORES
    df['sma_20'] = df['close'].rolling(20).mean()
    adx_df = df.ta.adx(length=14) # Genera el indicador ADX
    df = pd.concat([df, adx_df], axis=1) # Unimos el ADX a nuestra tabla principal
    
    precio_actual = df['close'].iloc[-1]
    media_actual = df['sma_20'].iloc[-1]
    adx_actual = df['ADX_14'].iloc[-1]

    if estado['en_posicion']:
        # 1. ACTUALIZAR MEMORIA DEL TRAILING STOP
        if precio_actual > estado.get('precio_max_alcanzado', 0):
            estado['precio_max_alcanzado'] = precio_actual
            
        # 2. CALCULAR DÓNDE ESTÁ EL STOP DINÁMICO HOY
        precio_stop_dinamico = estado['precio_max_alcanzado'] * (1 - (TRAILING_STOP_PCT / 100))
        
        porcentaje_variacion = ((precio_actual - estado['precio_compra']) / estado['precio_compra']) * 100
        saldo_obtenido = estado['btc_poseidos'] * precio_actual
        ganancia_usd = saldo_obtenido - (estado['btc_poseidos'] * estado['precio_compra'])

        # TAKE PROFIT FIJO
        if porcentaje_variacion >= TAKE_PROFIT_PCT:
            estado['saldo_usd'] = saldo_obtenido
            estado['btc_poseidos'] = 0.0
            estado['en_posicion'] = False
            estado['precio_max_alcanzado'] = 0.0
            registrar_evento(estado, f"🎯 TAKE PROFIT (+{TAKE_PROFIT_PCT}%) | Venta: ${precio_actual:,.2f} | Ganancia: +${ganancia_usd:,.2f} USDT")

        # TRAILING STOP (Protección de ganancias o limitación de pérdidas)
        elif precio_actual <= precio_stop_dinamico:
            estado['saldo_usd'] = saldo_obtenido
            estado['btc_poseidos'] = 0.0
            estado['en_posicion'] = False
            estado['precio_max_alcanzado'] = 0.0
            tipo_stop = "GANANCIA ASEGURADA" if ganancia_usd > 0 else "STOP LOSS"
            registrar_evento(estado, f"🛡️ TRAILING STOP ({tipo_stop}) | Venta: ${precio_actual:,.2f} | Resultado: ${ganancia_usd:+,.2f} USDT")

        # VENTA ESTRATÉGICA (Cruce bajista)
        elif precio_actual < media_actual:
            estado['saldo_usd'] = saldo_obtenido
            estado['btc_poseidos'] = 0.0
            estado['en_posicion'] = False
            estado['precio_max_alcanzado'] = 0.0
            registrar_evento(estado, f"🔴 VENTA ESTRATÉGICA | Venta: ${precio_actual:,.2f} | Resultado: ${ganancia_usd:+,.2f} USDT")
        
        else:
            distancia_al_stop = ((precio_actual - precio_stop_dinamico) / precio_actual) * 100
            registrar_evento(estado, f"📦 Activo | PnL: {porcentaje_variacion:+.2f}% | Distancia Stop: {distancia_al_stop:.2f}% | Max: ${estado['precio_max_alcanzado']:,.0f}")

    else:
        # COMPRA: El precio supera la media Y el ADX demuestra que hay una tendencia fuerte
        if precio_actual > media_actual and adx_actual > ADX_UMBRAL:
            estado['btc_poseidos'] = estado['saldo_usd'] / precio_actual
            estado['precio_compra'] = precio_actual
            estado['precio_max_alcanzado'] = precio_actual
            estado['en_posicion'] = True
            estado['saldo_usd'] = 0.0
            registrar_evento(estado, f"🟢 COMPRA | BTC: ${precio_actual:,.2f} | ADX: {adx_actual:.2f} (Tendencia Fuerte)")
        else:
            razon = f"ADX bajo ({adx_actual:.2f})" if precio_actual > media_actual else "Bajo SMA"
            registrar_evento(estado, f"💤 Esperando ({razon}) | BTC: ${precio_actual:,.2f}")

    guardar_estado(estado)

def bucle_trading():
    print("🚀 [HILO BOT V5] Analizando mercado con ADX y Trailing Stop...", flush=True)
    time.sleep(5) 
    while True:
        try:
            analizar_y_operar()
        except Exception as e:
            print(f"❌ Error en bucle principal: {e}", flush=True)
        time.sleep(300)

if __name__ == '__main__':
    if not MONGO_URI:
        print("❌ Error crítico: Falta MONGO_URI en Render.", flush=True)
    else:
        print("=== BOT V5 INICIADO ===", flush=True)
        hilo_bot = threading.Thread(target=bucle_trading)
        hilo_bot.daemon = True
        hilo_bot.start()
        run_server()