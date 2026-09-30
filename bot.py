import os
import time
import json
import threading
import pandas as pd
import ccxt
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pymongo import MongoClient
from pymongo.errors import PyMongoError

# --- CONFIGURACIÓN DE RIESGO Y ESTRATEGIA ---
PARES_OPERABLES = ['BTC/USD', 'ETH/USD', 'SOL/USD']  # Pares a escanear
STOP_LOSS_PCT = 2.0
TAKE_PROFIT_PCT = 4.0
TRAILING_STOP_PCT = 1.5  # Distancia en % desde el máximo alcanzado
FEE_PCT = 0.26           # Comisión estimada por operación en Kraken

# --- PARÁMETROS DE FILTROS ---
RSI_PERIODO = 14
RSI_MIN = 50.0  # Mínimo impulso comprador
RSI_MAX = 70.0  # Evita entrar sobrecomprado
SMA_15M_PERIODO = 20
SMA_1H_PERIODO = 20

# --- CONEXIÓN A MONGODB CON FALLBACK SEGURO ---
MONGO_URI = os.environ.get("MONGO_URI")
coleccion_estado = None

if MONGO_URI:
    try:
        cliente_mongo = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        cliente_mongo.admin.command('ping')
        db = cliente_mongo['trading_bot']
        coleccion_estado = db['estado']
        print("✓ Conexión exitosa a MongoDB Atlas.", flush=True)
    except Exception as e:
        print(f"⚠️ Error conectando a MongoDB: {e}. Se usará memoria RAM.", flush=True)
        coleccion_estado = None
else:
    print("⚠️ MONGO_URI no configurado. Operando en modo memoria RAM.", flush=True)

# Estado por defecto estructurado para Multipar
estado_ram = {
    "_id": "estado_actual",
    "saldo_usd": 1000.0,
    "cantidad_activa": 0.0,     # Cantidad de la moneda comprada (ej. tokens de SOL o BTC)
    "par_activo": "",           # Almacena en qué moneda estamos invertidos
    "capital_invertido_usd": 0.0,
    "precio_compra": 0.0,
    "en_posicion": False,
    "historial": [],
    "balance_history": [1000.0],
    "trades_ganadores": 0,
    "trades_perdedores": 0,
    "gross_profit": 0.0,
    "gross_loss": 0.0,
    "max_balance": 1000.0,
    "precio_max_alcanzado": 0.0,
    "stop_dinamico": 0.0,
    "mercado_actual": {par: {"precio": 0.0, "rsi": 0.0, "sma15": 0.0} for par in PARES_OPERABLES}
}

# Instancia global única de CCXT
exchange_kraken = ccxt.kraken({
    'enableRateLimit': True,
    'timeout': 15000
})


# --- FUNCIONES AUXILIARES ---
def calcular_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -1 * delta.clip(upper=0)
    ema_gain = gain.ewm(com=period - 1, adjust=False).mean()
    ema_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = ema_gain / ema_loss
    return 100 - (100 / (1 + rs))

def obtener_hora_local():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def registrar_evento(mensaje):
    print(f"[{obtener_hora_local()}] {mensaje}", flush=True)
    estado = obtener_estado()
    estado['historial'].insert(0, f"[{obtener_hora_local()}] {mensaje}")
    estado['historial'] = estado['historial'][:30]
    guardar_estado(estado)


# --- FUNCIONES DE BASE DE DATOS ---
def obtener_estado():
    global estado_ram
    if coleccion_estado is not None:
        try:
            doc = coleccion_estado.find_one({"_id": "estado_actual"})
            if doc:
                # Migración de estados antiguos (si venimos del bot mono-par)
                if "btc_poseidos" in doc:
                    doc["cantidad_activa"] = doc.pop("btc_poseidos", 0)
                    if doc.get("en_posicion", False) and not doc.get("par_activo"):
                        doc["par_activo"] = "BTC/USD"
                
                # Migrar mercado_actual si no existe
                if "mercado_actual" not in doc:
                    doc["mercado_actual"] = {par: {"precio": 0.0, "rsi": 0.0, "sma15": 0.0} for par in PARES_OPERABLES}
                return doc
            else:
                coleccion_estado.insert_one(estado_ram)
                return estado_ram.copy()
        except PyMongoError as e:
            print(f"⚠️ Fallo al leer MongoDB: {e}. Usando RAM.", flush=True)
            return estado_ram
    return estado_ram

def guardar_estado(estado):
    global estado_ram
    estado_ram = estado.copy()
    if coleccion_estado is not None:
        try:
            coleccion_estado.update_one(
                {"_id": "estado_actual"},
                {"$set": estado},
                upsert=True
            )
        except PyMongoError as e:
            print(f"⚠️ Fallo al guardar en MongoDB: {e}", flush=True)


# --- LÓGICA DE MERCADO ---
def obtener_datos_mercado(par):
    try:
        # Obtener datos de 15 minutos
        ohlcv_15m = exchange_kraken.fetch_ohlcv(par, '15m', limit=100)
        df_15m = pd.DataFrame(ohlcv_15m, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df_15m['sma_15m'] = df_15m['close'].rolling(window=SMA_15M_PERIODO).mean()
        df_15m['rsi'] = calcular_rsi(df_15m['close'], RSI_PERIODO)

        # Obtener datos de 1 hora
        ohlcv_1h = exchange_kraken.fetch_ohlcv(par, '1h', limit=50)
        df_1h = pd.DataFrame(ohlcv_1h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df_1h['sma_1h'] = df_1h['close'].rolling(window=SMA_1H_PERIODO).mean()

        if len(df_15m) < SMA_15M_PERIODO or len(df_1h) < SMA_1H_PERIODO:
            return None

        return {
            'precio_actual': float(df_15m.iloc[-1]['close']),
            'sma_15m': float(df_15m.iloc[-1]['sma_15m']),
            'rsi': float(df_15m.iloc[-1]['rsi']),
            'sma_1h': float(df_1h.iloc[-1]['sma_1h'])
        }
    except Exception as e:
        print(f"⚠️ Error al obtener datos de {par}: {e}", flush=True)
        return None


def simular_operacion(tipo, par, precio, cantidad_usd):
    estado = obtener_estado()
    fee = cantidad_usd * (FEE_PCT / 100.0)
    
    if tipo == 'COMPRA':
        estado['saldo_usd'] -= cantidad_usd
        cantidad_moneda = (cantidad_usd - fee) / precio
        
        estado['en_posicion'] = True
        estado['par_activo'] = par
        estado['cantidad_activa'] = cantidad_moneda
        estado['capital_invertido_usd'] = cantidad_usd
        estado['precio_compra'] = precio
        
        estado['precio_max_alcanzado'] = precio
        estado['stop_dinamico'] = precio * (1.0 - STOP_LOSS_PCT/100.0)
        
        registrar_evento(f"🟢 COMPRA {par} | Precio: {precio:.2f} USD | Inversión: {cantidad_usd:.2f} USD")
        
    elif tipo in ['VENTA_STOP', 'VENTA_PROFIT', 'VENTA_TRAILING']:
        valor_bruto_venta = estado['cantidad_activa'] * precio
        fee_venta = valor_bruto_venta * (FEE_PCT / 100.0)
        retorno_neto = valor_bruto_venta - fee_venta
        
        estado['saldo_usd'] += retorno_neto
        estado['balance_history'].append(estado['saldo_usd'])
        
        if estado['saldo_usd'] > estado['max_balance']:
            estado['max_balance'] = estado['saldo_usd']
            
        beneficio_trade = retorno_neto - estado['capital_invertido_usd']
        
        if beneficio_trade > 0:
            estado['trades_ganadores'] += 1
            estado['gross_profit'] += beneficio_trade
            icono = "🎯 TAKE PROFIT" if tipo == 'VENTA_PROFIT' else "🛡️ TRAILING STOP"
        else:
            estado['trades_perdedores'] += 1
            estado['gross_loss'] += abs(beneficio_trade)
            icono = "🛑 STOP LOSS" if tipo == 'VENTA_STOP' else "🛡️ TRAILING STOP"
            
        registrar_evento(f"{icono} {par} | Precio: {precio:.2f} USD | Retorno: {retorno_neto:.2f} USD | B/P: {beneficio_trade:.2f} USD")
        
        # Reset variables
        estado['en_posicion'] = False
        estado['par_activo'] = ""
        estado['cantidad_activa'] = 0.0
        estado['capital_invertido_usd'] = 0.0
        estado['precio_compra'] = 0.0
        estado['stop_dinamico'] = 0.0
        estado['precio_max_alcanzado'] = 0.0
        
    guardar_estado(estado)


def analizar_y_operar():
    print(f"[{obtener_hora_local()}] Iniciando ciclo de análisis...", flush=True)
    estado = obtener_estado()
    
    # --- RUTA 1: ESTAMOS EN POSICIÓN (Vigilamos solo el par comprado) ---
    if estado['en_posicion']:
        par = estado['par_activo']
        datos = obtener_datos_mercado(par)
        if not datos: return
        
        precio = datos['precio_actual']
        
        # Actualizar datos para el dashboard web
        estado['mercado_actual'][par] = {
            "precio": precio, "rsi": datos['rsi'], "sma15": datos['sma_15m']
        }
        guardar_estado(estado)

        # Actualizar Trailing Stop
        if precio > estado['precio_max_alcanzado']:
            estado['precio_max_alcanzado'] = precio
            nuevo_stop = precio * (1.0 - TRAILING_STOP_PCT/100.0)
            if nuevo_stop > estado['stop_dinamico']:
                estado['stop_dinamico'] = nuevo_stop
                registrar_evento(f"🔒 Trailing Stop ajustado al alza en {par}: {nuevo_stop:.2f} USD")
                guardar_estado(estado)

        # Condiciones de Venta
        if precio <= estado['stop_dinamico']:
            simular_operacion('VENTA_TRAILING' if estado['stop_dinamico'] > estado['precio_compra'] else 'VENTA_STOP', par, precio, 0)
        elif precio >= estado['precio_compra'] * (1.0 + TAKE_PROFIT_PCT/100.0):
            simular_operacion('VENTA_PROFIT', par, precio, 0)
        elif precio < datos['sma_15m'] and precio > estado['precio_compra']:
             registrar_evento(f"⚠️ Pérdida de SMA 15m en ganancias. Ajustando stop por seguridad.")
             # Lógica opcional para estrechar el stop si pierde la media estando en positivo
             
    # --- RUTA 2: ESTAMOS LÍQUIDOS (Escaneamos todos los pares en busca de entradas) ---
    else:
        for par in PARES_OPERABLES:
            datos = obtener_datos_mercado(par)
            if not datos: continue
            
            precio = datos['precio_actual']
            sma_15m = datos['sma_15m']
            sma_1h = datos['sma_1h']
            rsi = datos['rsi']
            
            # Actualizar datos del panel web
            estado['mercado_actual'][par] = {
                "precio": precio, "rsi": rsi, "sma15": sma_15m
            }
            guardar_estado(estado)
            
            tendencia_15m = precio > sma_15m
            tendencia_1h = precio > sma_1h
            impulso_rsi = RSI_MIN < rsi < RSI_MAX
            
            if tendencia_15m and tendencia_1h and impulso_rsi:
                # ¡Señal encontrada! Compramos y bloqueamos el escáner al estar en posición
                simular_operacion('COMPRA', par, precio, estado['saldo_usd'])
                break # Rompe el for para no comprar otros pares en el mismo ciclo
            else:
                print(f"[{obtener_hora_local()}] {par} -> No hay señal (P:{precio:.1f} | 15m:{tendencia_15m} | 1h:{tendencia_1h} | RSI:{rsi:.1f})")
                

# --- SERVIDOR WEB ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Multi-Pair Quant Bot</title>
    <meta http-equiv="refresh" content="10">
    <style>
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #121212; color: #e0e0e0; margin: 20px; }}
        .card {{ background-color: #1e1e1e; padding: 20px; border-radius: 8px; margin-bottom: 20px; border-left: 4px solid #bb86fc; }}
        .header {{ display: flex; justify-content: space-between; align-items: center; }}
        .positive {{ color: #4caf50; font-weight: bold; }}
        .negative {{ color: #f44336; font-weight: bold; }}
        .neutral {{ color: #03a9f4; font-weight: bold; }}
        h1, h2 {{ margin-top: 0; }}
        ul {{ list-style-type: none; padding-left: 0; }}
        li {{ background-color: #2c2c2c; margin: 5px 0; padding: 10px; border-radius: 4px; font-family: monospace; font-size: 0.9em; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>🚀 Quant Bot (Multi-Pair)</h1>
        <p>Actualizado: {hora_actual}</p>
    </div>

    <div class="card">
        <h2>📊 Resumen de Cuenta</h2>
        <div class="grid">
            <div>
                <p>Estado Operativo:</p>
                <h3 class="{estado_class}">{estado_str}</h3>
            </div>
            <div>
                <p>Saldo Disponible:</p>
                <h3>{saldo_usd} USD</h3>
            </div>
            <div>
                <p>Equidad Total Estimada:</p>
                <h3>{equidad_estimada} USD</h3>
            </div>
            <div>
                <p>Win Rate:</p>
                <h3>{win_rate}%</h3>
            </div>
        </div>
    </div>
    
    <div class="card">
        <h2>📡 Monitoreo de Mercado</h2>
        <div class="grid">
            {mercado_html}
        </div>
    </div>

    {posicion_html}

    <div class="card">
        <h2>📝 Historial de Eventos</h2>
        <ul>
            {historial_html}
        </ul>
    </div>
</body>
</html>
"""

class WebDashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        
        estado = obtener_estado()
        
        total_trades = estado['trades_ganadores'] + estado['trades_perdedores']
        win_rate = (estado['trades_ganadores'] / total_trades * 100) if total_trades > 0 else 0.0

        if estado['en_posicion']:
            estado_str = f"🟢 EN POSICIÓN ({estado['par_activo']})"
            estado_class = "positive"
            precio_actual = estado['mercado_actual'].get(estado['par_activo'], {}).get('precio', estado['precio_compra'])
            
            valor_actual_posicion = estado['cantidad_activa'] * precio_actual
            equidad = estado['saldo_usd'] + valor_actual_posicion
            pnl = ((precio_actual - estado['precio_compra']) / estado['precio_compra']) * 100
            pnl_class = "positive" if pnl >= 0 else "negative"
            
            pos_html = f"""
            <div class="card" style="border-left-color: #4caf50;">
                <h2>Trade Activo: {estado['par_activo']}</h2>
                <div class="grid">
                    <div><p>Precio Compra:</p><h3>{estado['precio_compra']:.2f} USD</h3></div>
                    <div><p>Precio Actual:</p><h3>{precio_actual:.2f} USD</h3></div>
                    <div><p>Rendimiento (P&L):</p><h3 class="{pnl_class}">{pnl:.2f}%</h3></div>
                    <div><p>Stop Dinámico Mínimo:</p><h3 class="negative">{estado['stop_dinamico']:.2f} USD</h3></div>
                </div>
            </div>
            """
        else:
            estado_str = "🔴 LÍQUIDO (Buscando oportunidades)"
            estado_class = "neutral"
            equidad = estado['saldo_usd']
            pos_html = ""

        # Construir bloques de mercado
        mercado_bloques = ""
        for par, datos in estado['mercado_actual'].items():
            if par == estado.get('par_activo'):
                borde = "border: 1px solid #4caf50;"
            else:
                borde = ""
            
            mercado_bloques += f"""
            <div style="background-color: #2c2c2c; padding: 10px; border-radius: 4px; {borde}">
                <strong>{par}</strong><br>
                Precio: {datos['precio']:.2f}<br>
                RSI: {datos['rsi']:.1f}<br>
                SMA15: {datos['sma15']:.2f}
            </div>
            """

        historial = "".join([f"<li>{linea}</li>" for linea in estado['historial']])
        if not historial: historial = "<li>Sin eventos recientes.</li>"

        html_final = HTML_TEMPLATE.format(
            hora_actual=obtener_hora_local(),
            estado_str=estado_str,
            estado_class=estado_class,
            saldo_usd=f"{estado['saldo_usd']:.2f}",
            equidad_estimada=f"{equidad:.2f}",
            win_rate=f"{win_rate:.1f}",
            mercado_html=mercado_bloques,
            posicion_html=pos_html,
            historial_html=historial
        )
        self.wfile.write(html_final.encode('utf-8'))

def iniciar_servidor_web():
    port = int(os.environ.get("PORT", 8080))
    server = ThreadingHTTPServer(('0.0.0.0', port), WebDashboardHandler)
    print(f"✓ Panel web iniciado en el puerto {port}", flush=True)
    server.serve_forever()


# --- ARRANQUE PRINCIPAL ---
if __name__ == "__main__":
    print(f"=== INICIANDO BOT QUANT MULTI-PAIR ===", flush=True)
    print(f"Pares objetivo: {PARES_OPERABLES}", flush=True)
    
    # Iniciar servidor web en hilo separado
    threading.Thread(target=iniciar_servidor_web, daemon=True).start()

    # Bucle principal de ejecución (ciclo cada 2 minutos)
    while True:
        try:
            analizar_y_operar()
        except Exception as e:
            print(f"[{obtener_hora_local()}] ❌ Error crítico en el ciclo principal: {e}", flush=True)
            time.sleep(10)
        
        # Pausa antes del siguiente escaneo (120 segundos)
        time.sleep(120)