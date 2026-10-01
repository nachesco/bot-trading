import os
import sys
import time
import json
import threading
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
from pymongo import MongoClient, errors

# ==========================================
# CONFIGURACIÓN GENERAL Y CONEXIONES
# ==========================================
MONGO_URI = os.environ.get("MONGO_URI", "mongodb+srv://admin:password@cluster.mongodb.net/test?retryWrites=true&w=width")
DB_NAME = "trading_bot_db"
COLLECTION_NAME = "bot_state"

try:
    mongo_client = MongoClient(
        MONGO_URI,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
        socketTimeoutMS=5000
    )
    db = mongo_client[DB_NAME]
    state_col = db[COLLECTION_NAME]
    print("Conexión exitosa a MongoDB Atlas.")
except Exception as e:
    print(f"Error inicial al conectar a MongoDB: {e}")

# Mapeo unificado de pares (DRY) y límites mínimos de volumen por activo en Kraken
PARES = ["BTC/USD", "ETH/USD", "SOL/USD"]
MAPA_PARES = {"BTC/USD": "XXBTZUSD", "ETH/USD": "XETHZUSD", "SOL/USD": "SOLUSD"}
MINIMOS_KRAKEN = {"BTC/USD": 0.0001, "ETH/USD": 0.01, "SOL/USD": 0.1}

# ==========================================
# ESTADO INICIAL Y PERSISTENCIA
# ==========================================
ESTADO_DEFAULT = {
    "saldo_usd": 1000.0, # Testeable, cuando pases a real aquí se leerá de la API
    "en_posicion": False,
    "par_activo": None,
    "precio_compra": 0.0,
    "cantidad_activa": 0.0,
    "stop_dinamico": 0.0,
    "take_profit": 0.0,
    "inversion_bruta": 0.0,
    "trades_ganadores": 0,
    "trades_perdedores": 0,
    "balance_history": [1000.0],
    "historial": [],
    "mercado_actual": {},
    "ultimo_timestamp_analizado": 0
}

def obtener_hora_local():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def obtener_estado():
    try:
        doc = state_col.find_one({"_id": "main_state"})
        if doc:
            data = doc["data"]
            for key, val in ESTADO_DEFAULT.items():
                if key not in data: data[key] = val
            return data
    except Exception as e:
        print(f"Error al obtener estado: {e}")
    return ESTADO_DEFAULT.copy()

def guardar_estado(estado):
    try:
        state_col.update_one(
            {"_id": "main_state"},
            {"$set": {"data": estado, "updated_at": datetime.utcnow()}},
            upsert=True
        )
    except Exception as e:
        print(f"Error al guardar estado: {e}")

def registrar_evento_en_estado(estado, mensaje):
    timestamp = obtener_hora_local()
    linea = f"[{timestamp}] {mensaje}"
    if "historial" not in estado or not isinstance(estado["historial"], list):
        estado["historial"] = []
    estado["historial"].insert(0, linea)
    estado["historial"] = estado["historial"][:100]

# ==========================================
# INDICADORES TÉCNICOS (MATEMÁTICA REAL)
# ==========================================
def rma(series, period):
    """Media Móvil Suavizada (Running Moving Average) usada por Wilder"""
    if len(series) < period:
        return []
    rmas = [sum(series[:period]) / period]
    for val in series[period:]:
        rmas.append((rmas[-1] * (period - 1) + val) / period)
    return rmas

def calcular_adx(velas, periodo=14):
    """Cálculo real del ADX de J. Welles Wilder (+DI, -DI, DX)"""
    if len(velas) < periodo * 2:
        return 20.0
    
    trs, pDMs, nDMs = [], [], []
    for i in range(1, len(velas)):
        high, low = velas[i]['high'], velas[i]['low']
        prev_high, prev_low, prev_close = velas[i-1]['high'], velas[i-1]['low'], velas[i-1]['close']
        
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        
        up_move = high - prev_high
        down_move = prev_low - low
        
        pDMs.append(up_move if up_move > down_move and up_move > 0 else 0)
        nDMs.append(down_move if down_move > up_move and down_move > 0 else 0)
        
    smooth_tr = rma(trs, periodo)
    smooth_pdm = rma(pDMs, periodo)
    smooth_ndm = rma(nDMs, periodo)
    
    if not smooth_tr: return 20.0
    
    dxs = []
    for i in range(len(smooth_tr)):
        tr = smooth_tr[i]
        if tr == 0: 
            dxs.append(0)
            continue
        pdi = 100 * (smooth_pdm[i] / tr)
        ndi = 100 * (smooth_ndm[i] / tr)
        sum_di = pdi + ndi
        dxs.append(100 * abs(pdi - ndi) / sum_di if sum_di != 0 else 0)
        
    adx_rma = rma(dxs, periodo)
    return round(adx_rma[-1], 2) if adx_rma else 20.0

def calcular_rsi(precios, periodo=14):
    if len(precios) < periodo + 1: return 50.0
    ganancias, perdidas = 0.0, 0.0
    for i in range(1, periodo + 1):
        diff = precios[-i] - precios[-(i + 1)]
        if diff >= 0: ganancias += diff
        else: perdidas -= diff
    avg_gain, avg_loss = ganancias / periodo, perdidas / periodo
    if avg_loss == 0: return 100.0
    return 100.0 - (100.0 / (1.0 + (avg_gain / avg_loss)))

def calcular_atr(velas, periodo=14):
    if len(velas) < periodo + 1: return 10.0
    trs = [max(v['high'] - v['low'], abs(v['high'] - velas[i-1]['close']), abs(v['low'] - velas[i-1]['close'])) 
           for i, v in enumerate(velas) if i > 0]
    return sum(trs[-periodo:]) / periodo if trs else 10.0

def calcular_vwap(velas):
    if not velas: return 0.0
    vol_total = sum(v['volume'] for v in velas)
    if vol_total == 0: return velas[-1]['close']
    return sum(((v['high'] + v['low'] + v['close']) / 3.0) * v['volume'] for v in velas) / vol_total

# ==========================================
# PLANTILLA HTML DEL DASHBOARD
# ==========================================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Quant Bot | Pro Analytics</title>
    <meta http-equiv="refresh" content="30">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {{ --bg-dark: #0b0e14; --bg-card: #151a23; --text-main: #f0f4f8; --text-muted: #8b9bb4; --accent: #3b82f6; --success: #10b981; --danger: #ef4444; --warning: #f59e0b; --border: #2a2e39; }}
        body {{ font-family: 'Inter', sans-serif; background: var(--bg-dark); color: var(--text-main); margin: 0; padding: 20px; }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        .header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 20px; margin-bottom: 20px; flex-wrap: wrap; gap: 15px;}}
        .header h1 {{ margin: 0; font-size: 1.8rem; font-weight: 800; display: flex; align-items: center; gap: 12px; }}
        .status-dot {{ height: 14px; width: 14px; border-radius: 50%; display: inline-block; box-shadow: 0 0 10px currentColor; }}
        .dot-active {{ color: var(--success); background: var(--success); }}
        .dot-waiting {{ color: var(--warning); background: var(--warning); }}
        .grid-5 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 15px; margin-bottom: 20px; }}
        .grid-4 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 15px; margin-bottom: 20px; }}
        .grid-3 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 15px; margin-bottom: 20px; }}
        .card {{ background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; padding: 20px; transition: transform 0.2s; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}
        .card-title {{ font-size: 0.85rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 10px; font-weight: 600; }}
        .card-value {{ font-size: 1.8rem; font-weight: 800; margin: 0; }}
        .text-success {{ color: var(--success); }} .text-danger {{ color: var(--danger); }} .text-warning {{ color: var(--warning); }}
        .active-trade {{ background: linear-gradient(145deg, rgba(16,185,129,0.08) 0%, rgba(21,26,35,1) 100%); border: 1px solid var(--success); }}
        .market-card {{ display: flex; flex-direction: column; gap: 10px; }}
        .market-row {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px dashed var(--border); padding-bottom: 8px; font-size: 0.9rem; }}
        .market-row:last-child {{ border-bottom: none; padding-bottom: 0; }}
        .badge {{ padding: 4px 10px; border-radius: 6px; font-size: 0.75rem; font-weight: 800; letter-spacing: 0.5px; text-transform: uppercase; }}
        .section-header {{ display: flex; justify-content: space-between; align-items: center; margin: 35px 0 15px 0; border-bottom: 1px solid var(--border); padding-bottom: 10px; }}
        .btn-clean {{ background: var(--bg-card); color: var(--text-muted); border: 1px solid var(--border); padding: 6px 14px; border-radius: 8px; text-decoration: none; font-size: 0.8rem; font-weight: 600; transition: all 0.2s ease; }}
        .btn-clean:hover {{ color: var(--danger); border-color: var(--danger); }}
        .logs-container {{ background: #000; border: 1px solid var(--border); border-radius: 12px; padding: 15px; height: 300px; overflow-y: auto; font-family: 'Consolas', 'Courier New', monospace; font-size: 0.85rem; color: #a9b1d6; box-shadow: inset 0 2px 10px rgba(0,0,0,0.5); }}
        .log-line {{ margin-bottom: 8px; padding-bottom: 8px; border-bottom: 1px solid #1a1b26; line-height: 1.4; }}
        .chart-container {{ position: relative; height: 260px; width: 100%; margin-top: 10px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1><span class="status-dot {dot_class}"></span> Panel Cuantitativo Avanzado</h1>
            <div style="text-align: right; color: var(--text-muted); font-size: 0.85rem; font-family: monospace;">Última actualización: {hora_actual}</div>
        </div>
        <div class="grid-4">
            <div class="card"><div class="card-title">Estado Operativo</div><div class="card-value {text_status_class}" style="font-size: 1.3rem; margin-top: 10px;">{estado_str}</div></div>
            <div class="card"><div class="card-title">Saldo Líquido</div><div class="card-value">${saldo_usd}</div></div>
            <div class="card"><div class="card-title">Equidad Total</div><div class="card-value">${equidad_estimada}</div></div>
            <div class="card"><div class="card-title">Rendimiento (B/P)</div><div class="card-value">{win_rate}% <span style="font-size:1rem; color:var(--text-muted); font-weight:600;">({trades_ganadores}W / {trades_perdedores}L)</span></div></div>
        </div>
        {posicion_html}
        <div class="card" style="margin-bottom: 20px;">
            <div class="card-title">📈 Curva de Equidad (Evolución de Capital)</div>
            <div class="chart-container"><canvas id="equityChart"></canvas></div>
        </div>
        <div class="section-header"><h2 class="section-title" style="margin:0; font-size: 1.2rem;">📡 Matriz de Análisis (1H)</h2></div>
        <div class="grid-3">{mercado_html}</div>
        <div class="section-header"><h2 class="section-title" style="margin:0; font-size: 1.2rem;">📝 Terminal de Eventos</h2><a href="/limpiar" class="btn-clean">🗑️ Limpiar Terminal</a></div>
        <div class="logs-container">{historial_html}</div>
    </div>
    <script>
        const ctx = document.getElementById('equityChart').getContext('2d');
        const gradient = ctx.createLinearGradient(0, 0, 0, 260);
        gradient.addColorStop(0, 'rgba(59, 130, 246, 0.35)'); gradient.addColorStop(1, 'rgba(59, 130, 246, 0.0)');
        new Chart(ctx, {{
            type: 'line',
            data: {{ labels: {balance_labels_json}, datasets: [{{ label: 'Saldo Total ($)', data: {balance_data_json}, borderColor: '#3b82f6', borderWidth: 2.5, backgroundColor: gradient, fill: true, tension: 0.3, pointRadius: 4, pointBackgroundColor: '#10b981' }}] }},
            options: {{ responsive: true, maintainAspectRatio: false, plugins: {{ legend: {{ display: false }} }}, scales: {{ x: {{ grid: {{ color: '#1a1b26' }}, ticks: {{ color: '#8b9bb4' }} }}, y: {{ grid: {{ color: '#2a2e39' }}, ticks: {{ color: '#8b9bb4', callback: v => '$'+v }} }} }} }}
        }});
    </script>
</body>
</html>
"""

# ==========================================
# SERVIDOR WEB Y CONTROLADOR HTTP
# ==========================================
class WebDashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args): pass
    def do_GET(self):
        if self.path == '/limpiar':
            estado = obtener_estado()
            estado['historial'] = []
            guardar_estado(estado)
            self.send_response(302)
            self.send_header('Location', '/')
            self.end_headers()
            return
            
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()

        estado = obtener_estado()
        total_trades = estado.get('trades_ganadores', 0) + estado.get('trades_perdedores', 0)
        win_rate = (estado.get('trades_ganadores', 0) / total_trades * 100) if total_trades > 0 else 0.0
        history = estado.get('balance_history', [1000.0])

        if estado.get('en_posicion'):
            estado_str, dot_class, text_status_class = "EN POSICIÓN", "dot-active", "text-success"
            par = estado.get('par_activo', 'BTC/USD')
            precio_actual = estado.get('mercado_actual', {}).get(par, {}).get('precio', estado['precio_compra'])
            valor_actual = estado['cantidad_activa'] * precio_actual
            equidad = estado['saldo_usd'] + valor_actual
            inversion_inicial = estado.get("inversion_bruta", estado['precio_compra'] * estado['cantidad_activa'])
            pnl = ((valor_actual - inversion_inicial) / inversion_inicial * 100) if inversion_inicial > 0 else 0.0
            
            pos_html = f"""
            <div class="card active-trade" style="margin-bottom: 20px;">
                <div class="card-title" style="color: var(--success); font-size: 1rem;">🟢 OPERACIÓN ACTIVA: {par}</div>
                <div class="grid-5" style="margin-bottom: 0;">
                    <div><span style="color: var(--text-muted); font-size: 0.85rem;">Precio Entrada</span><br><strong>${estado['precio_compra']:,.2f}</strong></div>
                    <div><span style="color: var(--text-muted); font-size: 0.85rem;">Precio Actual</span><br><strong>${precio_actual:,.2f}</strong></div>
                    <div><span style="color: var(--text-muted); font-size: 0.85rem;">P&L Neto Abierto</span><br><strong class="{'text-success' if pnl>=0 else 'text-danger'}">{pnl:+.2f}%</strong></div>
                    <div><span style="color: var(--text-muted); font-size: 0.85rem;">Stop Loss</span><br><strong class="text-danger">${estado['stop_dinamico']:,.2f}</strong></div>
                    <div><span style="color: var(--text-muted); font-size: 0.85rem;">Take Profit</span><br><strong class="text-success">${estado['take_profit']:,.2f}</strong></div>
                </div>
            </div>"""
        else:
            estado_str, dot_class, text_status_class = "ESCANEO ACTIVO", "dot-waiting", "text-warning"
            equidad, pos_html = estado['saldo_usd'], ""

        mercado_bloques = ""
        for par, datos in estado.get('mercado_actual', {}).items():
            is_active = (par == estado.get('par_activo'))
            adx_color = "var(--success)" if datos.get('adx', 0) > 25 else "#2a2e39"
            vwap_color = "var(--success)" if datos.get('precio', 0) > datos.get('vwap', 0) else "var(--danger)"
            
            mercado_bloques += f"""
            <div class="card market-card" style="{'border-color: var(--success);' if is_active else ''}">
                <div style="font-size: 1.2rem; font-weight: 800; color: {'var(--success)' if is_active else 'var(--text-main)'}; display:flex; justify-content:space-between;">
                    <span>{par}</span><span style="font-size:0.9rem; color:var(--text-muted); font-weight:400;">ATR: ${datos.get('atr', 0):.2f}</span>
                </div>
                <div class="market-row"><span style="color: var(--text-muted);">Cotización:</span><strong>${datos.get('precio', 0):,.2f}</strong></div>
                <div class="market-row"><span style="color: var(--text-muted);">ADX (Wilder):</span><span class="badge" style="background: {adx_color}; color: #fff;">{datos.get('adx', 0):.1f}</span></div>
                <div class="market-row"><span style="color: var(--text-muted);">RSI:</span><strong>{datos.get('rsi', 0):.1f}</strong></div>
                <div class="market-row"><span style="color: var(--text-muted);">Tendencia (VWAP):</span><span class="badge" style="background: {vwap_color}; color: #fff;">{'Alcista' if datos.get('precio',0) > datos.get('vwap',0) else 'Bajista'}</span></div>
            </div>"""

        historial = "".join([f"<div class='log-line'>{l}</div>" for l in estado.get('historial', [])]) or "<div class='log-line'>Analizando mercados...</div>"

        self.wfile.write(HTML_TEMPLATE.format(
            hora_actual=obtener_hora_local(), dot_class=dot_class, estado_str=estado_str, text_status_class=text_status_class,
            saldo_usd=f"{estado['saldo_usd']:,.2f}", equidad_estimada=f"{equidad:,.2f}", win_rate=f"{win_rate:.1f}",
            trades_ganadores=estado['trades_ganadores'], trades_perdedores=estado['trades_perdedores'],
            posicion_html=pos_html, mercado_html=mercado_bloques, historial_html=historial,
            balance_data_json=json.dumps(history), balance_labels_json=json.dumps([f"T{i}" if i>0 else "Inicio" for i in range(len(history))])
        ).encode('utf-8'))

# ==========================================
# MOTOR CUANTITATIVO Y BUCLE DE TRADING
# ==========================================
def obtener_datos_kraken(pair_symbol):
    symbol = MAPA_PARES.get(pair_symbol, "XXBTZUSD")
    try:
        url = f"https://api.kraken.com/0/public/OHLC?pair={symbol}&interval=60"
        res = requests.get(url, timeout=5).json()
        if res.get("error"): return None
        raw_candles = res["result"][list(res["result"].keys())[0]]
        return [{ 'time': c[0], 'open': float(c[1]), 'high': float(c[2]), 'low': float(c[3]), 'close': float(c[4]), 'vwap': float(c[5]), 'volume': float(c[6]) } for c in raw_candles[-60:]]
    except Exception: return None

def ejecutar_bucle_quant():
    print("=== INICIANDO QUANT ENGINE V3.1 (OPTIMIZADO I/O & VOLUMEN KRAKEN) ===")
    
    estado = obtener_estado()
    if "balance_history" not in estado: estado["balance_history"] = [estado.get("saldo_usd", 1000.0)]
    guardar_estado(estado)

    COMISION_KRAKEN = 0.0026
    SLIPPAGE_PCT = 0.0005
    RIESGO_POR_TRADE = 0.02

    while True:
        try:
            estado = obtener_estado()
            mercado = {}
            estado_cambiado = False  # OPTIMIZACIÓN 1: Solo guardaremos en BBDD si hay cambios reales
            
            # 1. ACTUALIZAR PRECIOS Y EVALUAR SALIDAS
            for par in PARES:
                velas = obtener_datos_kraken(par)
                if not velas: continue
                
                precio_actual = velas[-1]['close']
                velas_cerradas = velas[:-1]
                timestamp_actual_vela_cerrada = velas_cerradas[-1]['time']
                
                mercado[par] = {
                    "precio": precio_actual,
                    "rsi": calcular_rsi([v['close'] for v in velas_cerradas]),
                    "atr": calcular_atr(velas_cerradas),
                    "adx": calcular_adx(velas_cerradas),
                    "vwap": calcular_vwap(velas_cerradas),
                    "time": timestamp_actual_vela_cerrada
                }

                # GESTIÓN DE POSICIÓN ACTIVA
                if estado.get("en_posicion") and estado.get("par_activo") == par:
                    nuevo_stop = max(estado["stop_dinamico"], precio_actual - (1.5 * mercado[par]["atr"]))
                    if nuevo_stop != estado["stop_dinamico"]:
                        estado["stop_dinamico"] = nuevo_stop
                        estado_cambiado = True
                    
                    if precio_actual <= estado["stop_dinamico"] or precio_actual >= estado["take_profit"]:
                        razon = "TAKE PROFIT" if precio_actual >= estado["take_profit"] else "STOP/TRAILING"
                        
                        precio_ejecucion = precio_actual * (1 - SLIPPAGE_PCT)
                        monto_bruto = estado["cantidad_activa"] * precio_ejecucion
                        monto_recuperado = monto_bruto * (1 - COMISION_KRAKEN)
                        
                        pnl_usd = monto_recuperado - estado["inversion_bruta"]
                        estado["saldo_usd"] += monto_recuperado
                        estado["en_posicion"], estado["par_activo"] = False, None
                        
                        if pnl_usd >= 0: estado["trades_ganadores"] += 1
                        else: estado["trades_perdedores"] += 1
                        estado["balance_history"].append(round(estado["saldo_usd"], 2))
                        
                        msg = f"🔴 VENTA ({razon}) [{par}] Ejecución: ${precio_ejecucion:,.2f} | PnL: ${pnl_usd:+.2f} | Saldo: ${estado['saldo_usd']:,.2f}"
                        registrar_evento_en_estado(estado, msg)
                        print(f"[{obtener_hora_local()}] {msg}")
                        estado_cambiado = True

            estado["mercado_actual"] = mercado
            
            # 2. EVALUAR ENTRADAS AL CIERRE DE VELA
            # OPTIMIZACIÓN 2: Evitar ceguera de timestamp obteniendo el máximo disponible de la red entera
            timestamp_red = max([d.get("time", 0) for d in mercado.values()], default=0)
            
            if not estado.get("en_posicion") and timestamp_red != estado.get("ultimo_timestamp_analizado"):
                estado_cambiado = True # Garantiza guardar al menos una vez por hora el nuevo timestamp
                
                for par in PARES:
                    datos = mercado.get(par)
                    if not datos: continue
                    
                    if datos["precio"] > datos["vwap"] and datos["adx"] > 25.0 and datos["rsi"] >= 50.0:
                        precio_ejecucion = datos["precio"] * (1 + SLIPPAGE_PCT)
                        
                        distancia_sl_precio = 1.5 * datos["atr"]
                        riesgo_maximo_usd = estado["saldo_usd"] * RIESGO_POR_TRADE
                        
                        cantidad_a_comprar = riesgo_maximo_usd / distancia_sl_precio
                        capital_requerido = cantidad_a_comprar * precio_ejecucion
                        
                        capital_limite = estado["saldo_usd"] * 0.95
                        if capital_requerido > capital_limite:
                            capital_requerido = capital_limite
                            cantidad_a_comprar = capital_requerido / precio_ejecucion
                            
                        # OPTIMIZACIÓN 3: Verificación oficial de volúmenes mínimos en Kraken
                        minimo_requerido = MINIMOS_KRAKEN.get(par, 0.0)
                        capital_efectivo = capital_requerido * (1 - COMISION_KRAKEN)
                        cantidad_recibida = capital_efectivo / precio_ejecucion
                        
                        if cantidad_recibida >= minimo_requerido:
                            estado["saldo_usd"] -= capital_requerido
                            estado["en_posicion"], estado["par_activo"] = True, par
                            estado["precio_compra"] = precio_ejecucion
                            estado["cantidad_activa"] = cantidad_recibida
                            estado["inversion_bruta"] = capital_requerido
                            estado["stop_dinamico"] = precio_ejecucion - distancia_sl_precio
                            estado["take_profit"] = precio_ejecucion + (3.0 * datos["atr"])

                            msg = f"🟢 COMPRA [{par}] Ejecución: ${precio_ejecucion:,.2f} | Riesgo 2%: ${riesgo_maximo_usd:,.2f} | Inv: ${capital_requerido:,.2f}"
                            registrar_evento_en_estado(estado, msg)
                            print(f"[{obtener_hora_local()}] {msg}")
                            estado_cambiado = True
                            break 
                        else:
                            msg_rechazo = f"⚠️ OMITIDO [{par}]: Volumen {cantidad_recibida:.5f} no supera mínimo Kraken de {minimo_requerido}"
                            print(f"[{obtener_hora_local()}] {msg_rechazo}")
                
                estado["ultimo_timestamp_analizado"] = timestamp_red

            # OPTIMIZACIÓN 4: Escritura eficiente en MongoDB Atlas
            if estado_cambiado:
                guardar_estado(estado)
            
        except Exception as e:
            print(f"[{obtener_hora_local()}] Error en el bucle: {e}")

        time.sleep(30)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), WebDashboardHandler)
    t_server = threading.Thread(target=server.serve_forever, daemon=True)
    t_server.start()
    print(f"✓ Matriz PRO iniciada en puerto {port}")
    ejecutar_bucle_quant()