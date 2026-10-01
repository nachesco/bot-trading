import os
import time
import threading
import pandas as pd
import ccxt
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pymongo import MongoClient
from pymongo.errors import PyMongoError

# --- CONFIGURACIÓN DE RIESGO Y ESTRATEGIA PRO ---
PARES_OPERABLES = ['BTC/USD', 'ETH/USD', 'SOL/USD']
FEE_PCT = 0.26                 # Comisión en Kraken
PORCENTAJE_CAPITAL = 0.25      # Invertir el 25% del saldo disponible por trade

# Gestión de Riesgo por Volatilidad (ATR)
ATR_MULTIPLIER_STOP = 2.5      # El Stop Loss se coloca a 2.5 veces la volatilidad media
ATR_MULTIPLIER_TRAILING = 1.5  # El bot persigue el precio a 1.5 veces la volatilidad
TAKE_PROFIT_PCT = 5.0          # Take profit de emergencia para picos repentinos

# Parámetros de Indicadores
RSI_PERIODO = 14
RSI_MIN = 50.0  
RSI_MAX = 70.0  
SMA_15M_PERIODO = 20
SMA_1H_PERIODO = 20
ADX_PERIODO = 14
ADX_MIN = 25.0                 # Solo opera si hay tendencia fuerte
VWAP_PERIODOS = 96             # VWAP rodante de 24 horas (96 velas de 15m)

# --- CONEXIÓN A MONGODB (Con Timeout de Socket contra Congelamientos) ---
MONGO_URI = os.environ.get("MONGO_URI")
coleccion_estado = None

if MONGO_URI:
    try:
        cliente_mongo = MongoClient(
            MONGO_URI, 
            serverSelectionTimeoutMS=5000,
            socketTimeoutMS=10000,      # Cancela peticiones colgadas a los 10s
            connectTimeoutMS=5000
        )
        cliente_mongo.admin.command('ping')
        db = cliente_mongo['trading_bot']
        coleccion_estado = db['estado']
        print("✓ Conexión exitosa a MongoDB Atlas.", flush=True)
    except Exception as e:
        print(f"⚠️ Error conectando a MongoDB: {e}. Se usará memoria RAM.", flush=True)
        coleccion_estado = None

# Estado por defecto
estado_ram = {
    "_id": "estado_actual",
    "saldo_usd": 1000.0,
    "cantidad_activa": 0.0,
    "par_activo": "",
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
    "mercado_actual": {par: {"precio": 0.0, "rsi": 0.0, "sma15": 0.0, "adx": 0.0, "vwap": 0.0, "atr": 0.0} for par in PARES_OPERABLES}
}

exchange_kraken = ccxt.kraken({
    'enableRateLimit': True,
    'timeout': 15000
})


# --- FUNCIONES MATEMÁTICAS / INDICADORES ---
def calcular_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -1 * delta.clip(upper=0)
    ema_gain = gain.ewm(com=period - 1, adjust=False).mean()
    ema_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = ema_gain / ema_loss
    return 100 - (100 / (1 + rs))

def calcular_adx_atr(df, period=14):
    up = df['high'] - df['high'].shift(1)
    down = df['low'].shift(1) - df['low']
    
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    
    tr1 = df['high'] - df['low']
    tr2 = (df['high'] - df['close'].shift(1)).abs()
    tr3 = (df['low'] - df['close'].shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    atr = tr.ewm(alpha=1/period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1/period, adjust=False).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(alpha=1/period, adjust=False).mean() / atr)
    
    dx = 100 * (abs(plus_di - minus_di) / (plus_di + minus_di))
    adx = dx.ewm(alpha=1/period, adjust=False).mean()
    return adx, atr

def calcular_vwap(df, window=96):
    typical_price = (df['high'] + df['low'] + df['close']) / 3
    vwap = (typical_price * df['volume']).rolling(window=window).sum() / df['volume'].rolling(window=window).sum()
    return vwap

def obtener_hora_local():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def registrar_evento_en_estado(estado, mensaje):
    """Añade un evento al historial sin volver a consultar la BD (Evita corrupción de estado)."""
    print(f"[{obtener_hora_local()}] {mensaje}", flush=True)
    if 'historial' not in estado:
        estado['historial'] = []
    estado['historial'].insert(0, f"[{obtener_hora_local()}] {mensaje}")
    estado['historial'] = estado['historial'][:40]


# --- BASE DE DATOS ---
def obtener_estado():
    global estado_ram
    if coleccion_estado is not None:
        try:
            doc = coleccion_estado.find_one({"_id": "estado_actual"})
            if doc:
                if "mercado_actual" not in doc:
                    doc["mercado_actual"] = {}
                for par in PARES_OPERABLES:
                    if par not in doc["mercado_actual"]:
                        doc["mercado_actual"][par] = {"precio": 0.0, "rsi": 0.0, "sma15": 0.0, "adx": 0.0, "vwap": 0.0, "atr": 0.0}
                return doc
            else:
                coleccion_estado.insert_one(estado_ram)
                return estado_ram.copy()
        except PyMongoError as e:
            print(f"⚠️ Error de lectura en Mongo: {e}", flush=True)
            return estado_ram
    return estado_ram

def guardar_estado(estado):
    global estado_ram
    estado_ram = estado.copy()
    if coleccion_estado is not None:
        try:
            coleccion_estado.update_one({"_id": "estado_actual"}, {"$set": estado}, upsert=True)
        except PyMongoError as e:
            print(f"⚠️ Error de escritura en Mongo: {e}", flush=True)


# --- LÓGICA DE MERCADO ---
def obtener_datos_mercado(par):
    try:
        ohlcv_15m = exchange_kraken.fetch_ohlcv(par, '15m', limit=150)
        df_15m = pd.DataFrame(ohlcv_15m, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df_15m['sma_15m'] = df_15m['close'].rolling(window=SMA_15M_PERIODO).mean()
        df_15m['rsi'] = calcular_rsi(df_15m['close'], RSI_PERIODO)
        df_15m['vwap'] = calcular_vwap(df_15m, VWAP_PERIODOS)
        
        adx, atr = calcular_adx_atr(df_15m, ADX_PERIODO)
        df_15m['adx'] = adx
        df_15m['atr'] = atr

        ohlcv_1h = exchange_kraken.fetch_ohlcv(par, '1h', limit=50)
        df_1h = pd.DataFrame(ohlcv_1h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df_1h['sma_1h'] = df_1h['close'].rolling(window=SMA_1H_PERIODO).mean()

        if len(df_15m) < VWAP_PERIODOS or df_15m.isna().iloc[-1].any():
            return None

        return {
            'precio_actual': float(df_15m.iloc[-1]['close']),
            'sma_15m': float(df_15m.iloc[-1]['sma_15m']),
            'rsi': float(df_15m.iloc[-1]['rsi']),
            'adx': float(df_15m.iloc[-1]['adx']),
            'vwap': float(df_15m.iloc[-1]['vwap']),
            'atr': float(df_15m.iloc[-1]['atr']),
            'sma_1h': float(df_1h.iloc[-1]['sma_1h'])
        }
    except Exception as e:
        print(f"⚠️ Error al obtener datos de {par}: {e}", flush=True)
        return None


def simular_operacion(tipo, par, precio, atr_actual=0.0):
    estado = obtener_estado()
    
    if tipo == 'COMPRA':
        monto_inversion = estado['saldo_usd'] * PORCENTAJE_CAPITAL
        
        if monto_inversion < 10.0:
            registrar_evento_en_estado(estado, f"⚠️ Capital insuficiente para comprar {par} (Mínimo: 10 USD)")
            guardar_estado(estado)
            return

        fee = monto_inversion * (FEE_PCT / 100.0)
        cantidad_moneda = (monto_inversion - fee) / precio
        
        estado['saldo_usd'] -= monto_inversion
        estado['en_posicion'] = True
        estado['par_activo'] = par
        estado['cantidad_activa'] = cantidad_moneda
        estado['capital_invertido_usd'] = monto_inversion
        estado['precio_compra'] = precio
        
        estado['precio_max_alcanzado'] = precio
        estado['stop_dinamico'] = precio - (atr_actual * ATR_MULTIPLIER_STOP)
        
        registrar_evento_en_estado(
            estado, 
            f"🟢 COMPRA {par} | P: {precio:.2f} | Inversión: {monto_inversion:.2f} USD (25%) | Stop: {estado['stop_dinamico']:.2f}"
        )
        
    elif tipo in ['VENTA_STOP', 'VENTA_PROFIT', 'VENTA_TRAILING']:
        # 🛑 GUARDIA DE SEGURIDAD: Evita procesar ventas con datos nulos o corruptos
        if estado['cantidad_activa'] <= 0 or estado['capital_invertido_usd'] <= 0:
            registrar_evento_en_estado(estado, f"🚨 Venta nula bloqueada en {par}. Reseteando posición sin modificar saldo.")
            estado['en_posicion'] = False
            estado['par_activo'] = ""
            estado['cantidad_activa'] = 0.0
            estado['capital_invertido_usd'] = 0.0
            estado['precio_compra'] = 0.0
            estado['stop_dinamico'] = 0.0
            estado['precio_max_alcanzado'] = 0.0
            guardar_estado(estado)
            return

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
            
        registrar_evento_en_estado(
            estado, 
            f"{icono} {par} | Venta: {precio:.2f} | B/P: {beneficio_trade:+.2f} USD | Saldo: ${estado['saldo_usd']:.2f}"
        )
        
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
    
    if estado['en_posicion']:
        par = estado['par_activo']
        datos = obtener_datos_mercado(par)
        if not datos: return
        
        precio = datos['precio_actual']
        atr = datos['atr']
        
        estado['mercado_actual'][par] = {
            "precio": precio, "rsi": datos['rsi'], "sma15": datos['sma_15m'], 
            "adx": datos['adx'], "vwap": datos['vwap'], "atr": atr
        }
        guardar_estado(estado)

        # Trailing Stop ajustado por Volatilidad (ATR)
        if precio > estado['precio_max_alcanzado']:
            estado['precio_max_alcanzado'] = precio
            nuevo_stop = precio - (atr * ATR_MULTIPLIER_TRAILING)
            if nuevo_stop > estado['stop_dinamico']:
                estado['stop_dinamico'] = nuevo_stop
                registrar_evento_en_estado(estado, f"🔒 Trailing ATR ajustado en {par}: {nuevo_stop:.2f}")
                guardar_estado(estado)

        if precio <= estado['stop_dinamico']:
            simular_operacion('VENTA_TRAILING' if estado['stop_dinamico'] > estado['precio_compra'] else 'VENTA_STOP', par, precio)
        elif precio >= estado['precio_compra'] * (1.0 + TAKE_PROFIT_PCT/100.0):
            simular_operacion('VENTA_PROFIT', par, precio)
             
    else:
        for par in PARES_OPERABLES:
            datos = obtener_datos_mercado(par)
            if not datos: continue
            
            precio = datos['precio_actual']
            sma_15m = datos['sma_15m']
            sma_1h = datos['sma_1h']
            rsi = datos['rsi']
            adx = datos['adx']
            vwap = datos['vwap']
            atr = datos['atr']
            
            estado['mercado_actual'][par] = {
                "precio": precio, "rsi": rsi, "sma15": sma_15m, 
                "adx": adx, "vwap": vwap, "atr": atr
            }
            guardar_estado(estado)
            
            # FILTROS PRO DE ENTRADA
            tendencia_15m = precio > sma_15m
            tendencia_1h = precio > sma_1h
            impulso_rsi = RSI_MIN < rsi < RSI_MAX
            fuerza_tendencia = adx > ADX_MIN
            confirmacion_volumen = precio > vwap
            
            if tendencia_15m and tendencia_1h and impulso_rsi and fuerza_tendencia and confirmacion_volumen:
                simular_operacion('COMPRA', par, precio, atr)
                break 


# --- SERVIDOR WEB ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Quant Bot | Pro Analytics</title>
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
        .market-row {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px dashed var(--border); padding-bottom: 8px; font-size: 0.9rem; }}
        .market-row:last-child {{ border-bottom: none; padding-bottom: 0; }}
        .badge {{ padding: 4px 10px; border-radius: 6px; font-size: 0.75rem; font-weight: 800; letter-spacing: 0.5px; text-transform: uppercase; }}
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
                Panel Cuantitativo Avanzado
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

        <h2 class="section-title">📡 Matriz de Análisis</h2>
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
    def log_message(self, format, *args): pass 

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
            pnl = ((precio_actual - estado['precio_compra']) / estado['precio_compra']) * 100 if estado['precio_compra'] > 0 else 0.0
            pnl_class = "text-success" if pnl >= 0 else "text-danger"
            
            pos_html = f"""
            <div class="card active-trade" style="margin-bottom: 20px;">
                <div class="card-title" style="color: var(--success); font-size: 1rem;">🟢 OPERACIÓN ACTIVA: {estado['par_activo']}</div>
                <div class="grid-4" style="margin-bottom: 0;">
                    <div><span style="color: var(--text-muted); font-size: 0.85rem;">Precio de Compra</span><br><strong>${estado['precio_compra']:,.2f}</strong></div>
                    <div><span style="color: var(--text-muted); font-size: 0.85rem;">Precio Actual</span><br><strong>${precio_actual:,.2f}</strong></div>
                    <div><span style="color: var(--text-muted); font-size: 0.85rem;">P&L Abierto</span><br><strong class="{pnl_class}">{pnl:+.2f}%</strong></div>
                    <div><span style="color: var(--text-muted); font-size: 0.85rem;">Stop ATR Dinámico</span><br><strong class="text-danger">${estado['stop_dinamico']:,.2f}</strong></div>
                </div>
            </div>
            """
        else:
            estado_str = "ESCANEO ACTIVO"
            dot_class = "dot-waiting"
            text_status_class = "text-warning"
            equidad = estado['saldo_usd']
            pos_html = ""

        mercado_bloques = ""
        for par, datos in estado['mercado_actual'].items():
            is_active = (par == estado.get('par_activo'))
            active_style = "border-color: var(--success);" if is_active else ""
            
            rsi = datos.get('rsi', 0)
            if rsi > 70: rsi_color, rsi_text = "var(--danger)", "#fff"
            elif rsi > 50: rsi_color, rsi_text = "var(--success)", "#fff"
            else: rsi_color, rsi_text = "#2a2e39", "var(--text-main)"
            
            adx = datos.get('adx', 0)
            if adx > 25: adx_color, adx_label = "var(--success)", "Fuerte"
            else: adx_color, adx_label = "#2a2e39", "Débil/Lateral"
            
            vwap = datos.get('vwap', 0)
            precio = datos.get('precio', 0)
            if precio > vwap: vwap_color, vwap_label = "var(--success)", "Soporte (Alcista)"
            else: vwap_color, vwap_label = "var(--danger)", "Resistencia (Bajista)"

            mercado_bloques += f"""
            <div class="card market-card" style="{active_style}">
                <div style="font-size: 1.2rem; font-weight: 800; margin-bottom: 5px; color: {'var(--success)' if is_active else 'var(--text-main)'}; display:flex; justify-content:space-between;">
                    <span>{par}</span>
                    <span style="font-size:0.9rem; color:var(--text-muted); font-weight:400;">Vol. ATR: ${datos.get('atr', 0):.2f}</span>
                </div>
                
                <div class="market-row">
                    <span style="color: var(--text-muted);">Cotización:</span>
                    <strong>${precio:,.2f}</strong>
                </div>
                <div class="market-row">
                    <span style="color: var(--text-muted);">Fuerza Tendencial (ADX):</span>
                    <span class="badge" style="background: {adx_color}; color: #fff;">{adx:.1f} - {adx_label}</span>
                </div>
                <div class="market-row">
                    <span style="color: var(--text-muted);">Momentum (RSI):</span>
                    <span class="badge" style="background: {rsi_color}; color: {rsi_text};">{rsi:.1f}</span>
                </div>
                <div class="market-row">
                    <span style="color: var(--text-muted);">Estructura Volumen (VWAP):</span>
                    <span class="badge" style="background: {vwap_color}; color: #fff;">{vwap_label}</span>
                </div>
            </div>
            """

        historial = "".join([f"<div class='log-line'>{linea}</div>" for linea in estado.get('historial', [])])
        if not historial: historial = "<div class='log-line'>Sistema inicializado. Analizando mercados...</div>"

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
    print(f"✓ Matriz PRO iniciada en puerto {port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    print(f"=== INICIANDO QUANT ENGINE V2.1 (FIXED) ===", flush=True)
    threading.Thread(target=iniciar_servidor_web, daemon=True).start()

    while True:
        try:
            analizar_y_operar()
        except Exception as e:
            print(f"[{obtener_hora_local()}] ❌ Error crítico: {e}", flush=True)
            time.sleep(10)
        time.sleep(120)