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
    "cantidad_activa": 0.0,     # Cantidad de la moneda comprada
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
    estado['historial'] = estado['historial'][:40]
    guardar_estado(estado)


# --- FUNCIONES DE BASE DE DATOS ---
def obtener_estado():
    global estado_ram
    if coleccion_estado is not None:
        try:
            doc = coleccion_estado.find_one({"_id": "estado_actual"})
            if doc:
                # Migración de estados antiguos si venimos del bot mono-par
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
            print(f"⚠️️ Fallo al guardar en MongoDB: {e}", flush=True)


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
                # ¡Señal encontrada! Compramos y bloqueamos el escáner
                simular_operacion('COMPRA', par, precio, estado['saldo_usd'])
                break # Rompe el for para no comprar otros pares en el mismo ciclo
            else:
                print(f"[{obtener_hora_local()}] {par} -> No hay señal (P:{precio:.1f} | 15m:{tendencia_15m} | 1h:{tendencia_1h} | RSI:{rsi:.1f})")


# --- SERVIDOR WEB ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Quant Bot | Dashboard Pro</title>
    <meta http-equiv="refresh" content="15">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-dark: #0b0e14;
            --bg-card: #151a23;
            --text-main: #f0f4f8;
            --text-muted: #8b9bb4;
            --accent: #3b82f6;
            --success: #10b981;
            --danger: #ef4444;
            --warning: #f59e0b;
            --border: #2a2e39;
        }}
        body {{ font-family: 'Inter', sans-serif; background: var(--bg-dark); color: var(--text-main); margin: 0; padding: 20px; }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        .header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 20px; margin-bottom: 20px; flex-wrap: wrap; gap: 15px;}}
        .header h1 {{ margin: 0; font-size: 1.8rem; font-weight: 800; display: flex; align-items: center; gap: 12px; }}
        .status-dot {{ height: 14px; width: 14px; border-radius: 50%; display: inline-block; box-shadow: 0 0 10px currentColor; }}
        .dot-active {{ color: var(--success); background: var(--success); }}
        .dot-waiting {{ color: var(--warning); background: var(--warning); }}
        .grid-4 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 15px; margin-bottom: 20px; }}
        .grid-3 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 15px; margin-bottom: 20px; }}
        .card {{ background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; padding: 20px; transition: transform 0.2s; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}
        .card:hover {{ border-color: var(--text-muted); }}
        .card-title {{ font-size: 0.85rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 10px; font-weight: 600; }}
        .card-value {{ font-size: 1.8rem; font-weight: 800; margin: 0; }}
        .text-success {{ color: var(--success); }}
        .text-danger {{ color: var(--danger); }}
        .text-warning {{ color: var(--warning); }}
        .active-trade {{ background: linear-gradient(145deg, rgba(16,185,129,0.08) 0%, rgba(21,26,35,1) 100%); border: 1px solid var(--success); }}
        .market-card {{ display: flex; flex-direction: column; gap: 10px; }}
        .market-row {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px dashed var(--border); padding-bottom: 8px; }}
        .market-row:last-child {{ border-bottom: none; padding-bottom: 0; }}
        .badge {{ padding: 4px 10px; border-radius: 6px; font-size: 0.75rem; font-weight: 800; letter-spacing: 0.5px;}}
        .section-title {{ font-size: 1.2rem; border-bottom: 1px solid var(--border); padding-bottom: 10px; margin: 35px 0 15px 0; display: flex; align-items: center; gap: 8px;}}
        .logs-container {{ background: #000; border: 1px solid var(--border); border-radius: 12px; padding: 15px; height: 300px; overflow-y: auto; font-family: 'Consolas', 'Courier New', monospace; font-size: 0.85rem; color: #a9b1d6; box-shadow: inset 0 2px 10px rgba(0,0,0,0.5);}}
        .log-line {{ margin-bottom: 8px; padding-bottom: 8px; border-bottom: 1px solid #1a1b26; line-height: 1.4; }}
        .log-line:last-child {{ border: none; }}
        ::-webkit-scrollbar {{ width: 8px; }}
        ::-webkit-scrollbar-track {{ background: var(--bg-dark); }}
        ::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 4px; }}
        ::-webkit-scrollbar-thumb:hover {{ background: var(--text-muted); }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>
                <span class="status-dot {dot_class}"></span>
                Panel Cuantitativo
            </h1>
            <div style="text-align: right; color: var(--text-muted); font-size: 0.85rem; font-family: monospace;">
                Última actualización: {hora_actual}
            </div>
        </div>

        <div class="grid-4">
            <div class="card">
                <div class="card-title">Estado Operativo</div>
                <div class="card-value {text_status_class}" style="font-size: 1.3rem; margin-top: 10px;">{estado_str}</div>
            </div>
            <div class="card">
                <div class="card-title">Saldo Líquido</div>
                <div class="card-value">${saldo_usd}</div>
            </div>
            <div class="card">
                <div class="card-title">Equidad Total</div>
                <div class="card-value">${equidad_estimada}</div>
            </div>
            <div class="card">
                <div class="card-title">Rendimiento (B/P)</div>
                <div class="card-value">{win_rate}% <span style="font-size:1rem; color:var(--text-muted); font-weight:600;">({trades_ganadores}W / {trades_perdedores}L)</span></div>
            </div>
        </div>

        {posicion_html}

        <h2 class="section-title">📡 Escáner de Mercado</h2>
        <div class="grid-3">
            {mercado_html}
        </div>

        <h2 class="section-title">📝 Terminal de Eventos</h2>
        <div class="logs-container">
            {historial_html}
        </div>
    </div>
</body>
</html>
"""

class WebDashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass # Suprimir logs de peticiones HTTP en la consola para no ensuciar

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        
        estado = obtener_estado()
        
        total_trades = estado['trades_ganadores'] + estado['trades_perdedores']
        win_rate = (estado['trades_ganadores'] / total_trades * 100) if total_trades > 0 else 0.0

        if estado['en_posicion']:
            estado_str = "EN POSICIÓN"
            dot_class = "dot-active"
            text_status_class = "text-success"
            precio_actual = estado['mercado_actual'].get(estado['par_activo'], {}).get('precio', estado['precio_compra'])
            
            valor_actual_posicion = estado['cantidad_activa'] * precio_actual
            equidad = estado['saldo_usd'] + valor_actual_posicion
            pnl = ((precio_actual - estado['precio_compra']) / estado['precio_compra']) * 100
            pnl_class = "text-success" if pnl >= 0 else "text-danger"
            
            pos_html = f"""
            <div class="card active-trade" style="margin-bottom: 20px;">
                <div class="card-title" style="color: var(--success); font-size: 1rem;">🟢 OPERACIÓN ACTIVA: {estado['par_activo']}</div>
                <div class="grid-4" style="margin-bottom: 0;">
                    <div><span style="color: var(--text-muted); font-size: 0.85rem;">Precio de Compra</span><br><strong>${estado['precio_compra']:,.2f}</strong></div>
                    <div><span style="color: var(--text-muted); font-size: 0.85rem;">Precio Actual</span><br><strong>${precio_actual:,.2f}</strong></div>
                    <div><span style="color: var(--text-muted); font-size: 0.85rem;">P&L Abierto</span><br><strong class="{pnl_class}">{pnl:+.2f}%</strong></div>
                    <div><span style="color: var(--text-muted); font-size: 0.85rem;">Stop Dinámico Actual</span><br><strong class="text-danger">${estado['stop_dinamico']:,.2f}</strong></div>
                </div>
            </div>
            """
        else:
            estado_str = "LÍQUIDO / ESPERA"
            dot_class = "dot-waiting"
            text_status_class = "text-warning"
            equidad = estado['saldo_usd']
            pos_html = ""

        # Construir bloques de mercado con inteligencia de colores
        mercado_bloques = ""
        for par, datos in estado['mercado_actual'].items():
            is_active = (par == estado.get('par_activo'))
            active_style = "border-color: var(--success);" if is_active else ""
            
            # Lógica de color RSI
            rsi = datos['rsi']
            if rsi > 65: rsi_color, rsi_text = "var(--danger)", "#fff"
            elif rsi > 50: rsi_color, rsi_text = "var(--success)", "#fff"
            else: rsi_color, rsi_text = "#2a2e39", "var(--text-main)"
            
            # Lógica de color Tendencia
            tendencia = "ALCISTA" if datos['precio'] > datos['sma15'] else "BAJISTA"
            tendencia_color = "var(--success)" if tendencia == "ALCISTA" else "var(--danger)"

            mercado_bloques += f"""
            <div class="card market-card" style="{active_style}">
                <div style="font-size: 1.2rem; font-weight: 800; margin-bottom: 5px; color: {'var(--success)' if is_active else 'var(--text-main)'};">
                    {par} { '🎯' if is_active else ''}
                </div>
                
                <div class="market-row">
                    <span style="color: var(--text-muted);">Precio Actual:</span>
                    <strong>${datos['precio']:,.2f}</strong>
                </div>
                <div class="market-row">
                    <span style="color: var(--text-muted);">Fuerza (RSI 14):</span>
                    <span class="badge" style="background: {rsi_color}; color: {rsi_text};">{rsi:.1f}</span>
                </div>
                <div class="market-row">
                    <span style="color: var(--text-muted);">Tendencia Corta:</span>
                    <span class="badge" style="background: {tendencia_color}; color: #fff;">{tendencia}</span>
                </div>
            </div>
            """

        historial = "".join([f"<div class='log-line'>{linea}</div>" for linea in estado['historial']])
        if not historial: historial = "<div class='log-line'>Sin eventos recientes... Esperando iniciar ciclo.</div>"

        html_final = HTML_TEMPLATE.format(
            hora_actual=obtener_hora_local(),
            dot_class=dot_class,
            estado_str=estado_str,
            text_status_class=text_status_class,
            saldo_usd=f"{estado['saldo_usd']:,.2f}",
            equidad_estimada=f"{equidad:,.2f}",
            win_rate=f"{win_rate:.1f}",
            trades_ganadores=estado['trades_ganadores'],
            trades_perdedores=estado['trades_perdedores'],
            mercado_html=mercado_bloques,
            posicion_html=pos_html,
            historial_html=historial
        )
        self.wfile.write(html_final.encode('utf-8'))

def iniciar_servidor_web():
    port = int(os.environ.get("PORT", 8080))
    server = ThreadingHTTPServer(('0.0.0.0', port), WebDashboardHandler)
    print(f"✓ Panel web PRO iniciado en el puerto {port}", flush=True)
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