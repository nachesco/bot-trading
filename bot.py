import os
import sys
import time
import json
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
from pymongo import MongoClient, errors

# ==========================================
# CONFIGURACIÓN GENERAL Y CONEXIONES
# ==========================================
MONGO_URI = os.environ.get("MONGO_URI", "mongodb+srv://admin:password@cluster.mongodb.net/test?retryWrites=true&w=width")
DB_NAME = "trading_bot_db"
COLLECTION_NAME = "bot_state"

# Timeouts en MongoDB para evitar que la aplicación se congele por microcortes
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

PARES = ["BTC/USD", "ETH/USD", "SOL/USD"]

# ==========================================
# ESTADO INICIAL Y PERSISTENCIA
# ==========================================
ESTADO_DEFAULT = {
    "saldo_usd": 1000.0,
    "en_posicion": False,
    "par_activo": None,
    "precio_compra": 0.0,
    "cantidad_activa": 0.0,
    "stop_dinamico": 0.0,
    "trades_ganadores": 0,
    "trades_perdedores": 0,
    "balance_history": [1000.0],
    "historial": [],
    "mercado_actual": {}
}

def obtener_hora_local():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def obtener_estado():
    try:
        doc = state_col.find_one({"_id": "main_state"})
        if doc:
            return doc["data"]
    except Exception as e:
        print(f"Error al obtener estado de MongoDB: {e}")
    return ESTADO_DEFAULT.copy()

def guardar_estado(estado):
    try:
        state_col.update_one(
            {"_id": "main_state"},
            {"$set": {"data": estado, "updated_at": datetime.utcnow()}},
            upsert=True
        )
    except Exception as e:
        print(f"Error al guardar estado en MongoDB: {e}")

def registrar_evento_en_estado(estado, mensaje):
    timestamp = obtener_hora_local()
    linea = f"[{timestamp}] {mensaje}"
    if "historial" not in estado or not isinstance(estado["historial"], list):
        estado["historial"] = []
    
    # Insertar al inicio para mostrar los eventos más recientes primero
    estado["historial"].insert(0, linea)
    # Limitar el historial cargado en DB a un máximo de 100 eventos
    estado["historial"] = estado["historial"][:100]

# ==========================================
# INDICADORES TÉCNICOS (CALCULADORA MOTOR)
# ==========================================
def calcular_rsi(precios, periodo=14):
    if len(precios) < periodo + 1:
        return 50.0
    ganancias, perdidas = 0.0, 0.0
    for i in range(1, periodo + 1):
        diff = precios[-i] - precios[-(i + 1)]
        if diff >= 0:
            ganancias += diff
        else:
            perdidas -= diff
    avg_gain = ganancias / periodo
    avg_loss = perdidas / periodo
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def calcular_atr(velas, periodo=14):
    if len(velas) < periodo + 1:
        return 10.0
    trs = []
    for i in range(1, len(velas)):
        high = velas[i]['high']
        low = velas[i]['low']
        prev_close = velas[i-1]['close']
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    return sum(trs[-periodo:]) / periodo if trs else 10.0

def calcular_adx(velas, periodo=14):
    # Cálculo simplificado de la fuerza tendencial ADX
    if len(velas) < periodo + 1:
        return 20.0
    subidas = 0
    totales = 0
    for i in range(1, len(velas)):
        diff = abs(velas[i]['close'] - velas[i-1]['close'])
        totales += diff
        if velas[i]['close'] > velas[i-1]['close']:
            subidas += diff
    fuerza = (subidas / totales * 100) if totales > 0 else 50
    return round(min(max(fuerza, 15.0), 45.0), 1)

def calcular_vwap(velas):
    if not velas:
        return 0.0
    vol_total = sum(v['volume'] for v in velas)
    if vol_total == 0:
        return velas[-1]['close']
    pv_total = sum(((v['high'] + v['low'] + v['close']) / 3.0) * v['volume'] for v in velas)
    return pv_total / vol_total

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
    <meta http-equiv="refresh" content="15">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
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
        .section-header {{ display: flex; justify-content: space-between; align-items: center; margin: 35px 0 15px 0; border-bottom: 1px solid var(--border); padding-bottom: 10px; }}
        .section-title {{ font-size: 1.2rem; margin: 0; display: flex; align-items: center; gap: 8px; }}
        .btn-clean {{ background: var(--bg-card); color: var(--text-muted); border: 1px solid var(--border); padding: 6px 14px; border-radius: 8px; text-decoration: none; font-size: 0.8rem; font-weight: 600; transition: all 0.2s ease; }}
        .btn-clean:hover {{ color: var(--danger); border-color: var(--danger); }}
        .logs-container {{ background: #000; border: 1px solid var(--border); border-radius: 12px; padding: 15px; height: 300px; overflow-y: auto; font-family: 'Consolas', 'Courier New', monospace; font-size: 0.85rem; color: #a9b1d6; box-shadow: inset 0 2px 10px rgba(0,0,0,0.5); }}
        .log-line {{ margin-bottom: 8px; padding-bottom: 8px; border-bottom: 1px solid #1a1b26; line-height: 1.4; }}
        .log-line:last-child {{ border: none; }}
        .chart-container {{ position: relative; height: 260px; width: 100%; margin-top: 10px; }}
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

        <div class="card" style="margin-bottom: 20px;">
            <div class="card-title">📈 Curva de Equidad (Evolución de Capital)</div>
            <div class="chart-container">
                <canvas id="equityChart"></canvas>
            </div>
        </div>

        <div class="section-header">
            <h2 class="section-title">📡 Matriz de Análisis</h2>
        </div>
        <div class="grid-3">
            {mercado_html}
        </div>

        <div class="section-header">
            <h2 class="section-title">📝 Terminal de Eventos</h2>
            <a href="/limpiar" class="btn-clean">🗑️ Limpiar Terminal</a>
        </div>
        <div class="logs-container">
            {historial_html}
        </div>
    </div>

    <script>
        const balanceData = {balance_data_json};
        const balanceLabels = {balance_labels_json};

        const ctx = document.getElementById('equityChart').getContext('2d');
        const gradient = ctx.createLinearGradient(0, 0, 0, 260);
        gradient.addColorStop(0, 'rgba(59, 130, 246, 0.35)');
        gradient.addColorStop(1, 'rgba(59, 130, 246, 0.0)');

        new Chart(ctx, {{
            type: 'line',
            data: {{
                labels: balanceLabels,
                datasets: [{{
                    label: 'Saldo Total ($)',
                    data: balanceData,
                    borderColor: '#3b82f6',
                    borderWidth: 2.5,
                    backgroundColor: gradient,
                    fill: true,
                    tension: 0.3,
                    pointRadius: 4,
                    pointHoverRadius: 6,
                    pointBackgroundColor: '#10b981'
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{ display: false }},
                    tooltip: {{
                        callbacks: {{
                            label: function(context) {{
                                return 'Saldo: $' + context.parsed.y.toFixed(2);
                            }}
                        }}
                    }}
                }},
                scales: {{
                    x: {{
                        grid: {{ color: '#1a1b26', drawBorder: false }},
                        ticks: {{ color: '#8b9bb4', font: {{ family: 'Inter' }} }}
                    }},
                    y: {{
                        grid: {{ color: '#2a2e39', drawBorder: false }},
                        ticks: {{ 
                            color: '#8b9bb4', 
                            font: {{ family: 'Inter' }},
                            callback: function(value) {{ return '$' + value; }}
                        }}
                    }}
                }}
            }}
        }});
    </script>
</body>
</html>
"""

# ==========================================
# SERVIDOR WEB Y CONTROLADOR HTTP
# ==========================================
class WebDashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == '/limpiar':
            estado = obtener_estado()
            estado['historial'] = []
            registrar_evento_en_estado(estado, "🧹 Terminal de eventos limpiada correctamente.")
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
        balance_data_json = json.dumps(history)
        balance_labels_json = json.dumps([f"Trade {i}" if i > 0 else "Inicio" for i in range(len(history))])

        if estado.get('en_posicion'):
            estado_str = "EN POSICIÓN"
            dot_class = "dot-active"
            text_status_class = "text-success"
            par = estado.get('par_activo', 'BTC/USD')
            precio_compra = estado.get('precio_compra', 0.0)
            precio_actual = estado.get('mercado_actual', {}).get(par, {}).get('precio', precio_compra)

            valor_actual_posicion = estado.get('cantidad_activa', 0.0) * precio_actual
            equidad = estado.get('saldo_usd', 0.0) + valor_actual_posicion
            pnl = ((precio_actual - precio_compra) / precio_compra) * 100 if precio_compra > 0 else 0.0
            pnl_class = "text-success" if pnl >= 0 else "text-danger"

            pos_html = f"""
            <div class="card active-trade" style="margin-bottom: 20px;">
                <div class="card-title" style="color: var(--success); font-size: 1rem;">🟢 OPERACIÓN ACTIVA: {par}</div>
                <div class="grid-4" style="margin-bottom: 0;">
                    <div><span style="color: var(--text-muted); font-size: 0.85rem;">Precio de Compra</span><br><strong>${precio_compra:,.2f}</strong></div>
                    <div><span style="color: var(--text-muted); font-size: 0.85rem;">Precio Actual</span><br><strong>${precio_actual:,.2f}</strong></div>
                    <div><span style="color: var(--text-muted); font-size: 0.85rem;">P&L Abierto</span><br><strong class="{pnl_class}">{pnl:+.2f}%</strong></div>
                    <div><span style="color: var(--text-muted); font-size: 0.85rem;">Stop ATR Dinámico</span><br><strong class="text-danger">${estado.get('stop_dinamico', 0.0):,.2f}</strong></div>
                </div>
            </div>
            """
        else:
            estado_str = "ESCANEO ACTIVO"
            dot_class = "dot-waiting"
            text_status_class = "text-warning"
            equidad = estado.get('saldo_usd', 1000.0)
            pos_html = ""

        mercado_bloques = ""
        for par, datos in estado.get('mercado_actual', {}).items():
            is_active = (par == estado.get('par_activo'))
            active_style = "border-color: var(--success);" if is_active else ""

            rsi = datos.get('rsi', 50)
            if rsi > 70: rsi_color, rsi_text = "var(--danger)", "#fff"
            elif rsi > 50: rsi_color, rsi_text = "var(--success)", "#fff"
            else: rsi_color, rsi_text = "#2a2e39", "var(--text-main)"

            adx = datos.get('adx', 20)
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

        historial_list = estado.get('historial', [])
        historial = "".join([f"<div class='log-line'>{linea}</div>" for linea in historial_list])
        if not historial: historial = "<div class='log-line'>Sistema inicializado. Analizando mercados...</div>"

        html_final = HTML_TEMPLATE.format(
            hora_actual=obtener_hora_local(),
            dot_class=dot_class,
            estado_str=estado_str,
            text_status_class=text_status_class,
            saldo_usd=f"{estado.get('saldo_usd', 1000.0):,.2f}",
            equidad_estimada=f"{equidad:,.2f}",
            win_rate=f"{win_rate:.1f}",
            trades_ganadores=estado.get('trades_ganadores', 0),
            trades_perdedores=estado.get('trades_perdedores', 0),
            mercado_html=mercado_bloques,
            posicion_html=pos_html,
            historial_html=historial,
            balance_data_json=balance_data_json,
            balance_labels_json=balance_labels_json
        )
        self.wfile.write(html_final.encode('utf-8'))

# ==========================================
# MOTOR CUANTITATIVO Y BUCLE DE TRADING
# ==========================================
def obtener_datos_kraken(pair_symbol):
    m_map = {"BTC/USD": "XXBTZUSD", "ETH/USD": "XETHZUSD", "SOL/USD": "SOLUSD"}
    symbol = m_map.get(pair_symbol, "XXBTZUSD")
    try:
        url = f"https://api.kraken.com/0/public/OHLC?pair={symbol}&interval=15"
        res = requests.get(url, timeout=5).json()
        if res.get("error"):
            return None
        key = list(res["result"].keys())[0]
        raw_candles = res["result"][key]
        velas = []
        for c in raw_candles[-30:]:
            velas.append({
                'time': c[0],
                'open': float(c[1]),
                'high': float(c[2]),
                'low': float(c[3]),
                'close': float(c[4]),
                'vwap': float(c[5]),
                'volume': float(c[6])
            })
        return velas
    except Exception as e:
        return None

def ejecutar_bucle_quant():
    print("=== INICIANDO QUANT ENGINE V2.1 (FIXED & IMPROVED) ===")
    
    # Inicialización de estado en BD si no existe
    estado = obtener_estado()
    if "balance_history" not in estado:
        estado["balance_history"] = [estado.get("saldo_usd", 1000.0)]
    guardar_estado(estado)

    while True:
        try:
            estado = obtener_estado()
            mercado = {}

            for par in PARES:
                velas = obtener_datos_kraken(par)
                if not velas or len(velas) < 15:
                    continue

                precios = [v['close'] for v in velas]
                precio_actual = precios[-1]
                rsi = calcular_rsi(precios)
                atr = calcular_atr(velas)
                adx = calcular_adx(velas)
                vwap = calcular_vwap(velas)

                mercado[par] = {
                    "precio": precio_actual,
                    "rsi": rsi,
                    "atr": atr,
                    "adx": adx,
                    "vwap": vwap
                }

                # GESTIÓN DE POSICIÓN ABIERTA
                if estado.get("en_posicion") and estado.get("par_activo") == par:
                    precio_compra = estado["precio_compra"]
                    nuevo_stop = max(estado["stop_dinamico"], precio_actual - (1.5 * atr))
                    estado["stop_dinamico"] = nuevo_stop

                    # Condición de Salida (Stop Loss o Trailing Stop)
                    if precio_actual <= estado["stop_dinamico"]:
                        monto_recuperado = estado["cantidad_activa"] * precio_actual
                        pnl_usd = monto_recuperado - (estado["cantidad_activa"] * precio_compra)
                        
                        estado["saldo_usd"] += monto_recuperado
                        estado["en_posicion"] = False
                        estado["par_activo"] = None
                        
                        if pnl_usd >= 0:
                            estado["trades_ganadores"] += 1
                        else:
                            estado["trades_perdedores"] += 1

                        estado.setdefault("balance_history", []).append(round(estado["saldo_usd"], 2))
                        
                        msg = f"🔴 VENTA EJECUTADA [{par}] a ${precio_actual:,.2f} | PnL: ${pnl_usd:+.2f} | Nuevo Saldo: ${estado['saldo_usd']:,.2f}"
                        registrar_evento_en_estado(estado, msg)
                        print(f"[{obtener_hora_local()}] {msg}")

                # GESTIÓN DE ENTRADA (ESTRATEGIA CUANTITATIVA AL 25%)
                elif not estado.get("en_posicion"):
                    # Filtros estrictos: Tendencia (Precio > VWAP), Fuerza (ADX > 25), Momentum (RSI entre 50 y 65)
                    if precio_actual > vwap and adx > 25.0 and 50.0 <= rsi <= 65.0:
                        capital_a_invertir = estado["saldo_usd"] * 0.25 # Gestión de capital al 25%
                        
                        if capital_a_invertir >= 10.0:
                            cantidad = capital_a_invertir / precio_actual
                            estado["saldo_usd"] -= capital_a_invertir
                            estado["en_posicion"] = True
                            estado["par_activo"] = par
                            estado["precio_compra"] = precio_actual
                            estado["cantidad_activa"] = cantidad
                            estado["stop_dinamico"] = precio_actual - (1.5 * atr)

                            msg = f"🟢 COMPRA EJECUTADA [{par}] a ${precio_actual:,.2f} | Invertido: ${capital_a_invertir:,.2f} (25%) | Stop Inicial: ${estado['stop_dinamico']:,.2f}"
                            registrar_evento_en_estado(estado, msg)
                            print(f"[{obtener_hora_local()}] {msg}")

            estado["mercado_actual"] = mercado
            guardar_estado(estado)

        except Exception as e:
            print(f"[{obtener_hora_local()}] Error en el bucle principal: {e}")

        time.sleep(120) # Pausa de 2 minutos entre escaneos

# ==========================================
# PUNTO DE ENTRADA PRINCIPAL
# ==========================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    
    # Iniciar servidor web en hilo secundario
    server = HTTPServer(('0.0.0.0', port), WebDashboardHandler)
    t_server = threading.Thread(target=server.serve_forever)
    t_server.daemon = True
    t_server.start()
    print(f"✓ Matriz PRO iniciada en puerto {port}")

    # Iniciar motor cuantitativo en hilo principal
    ejecutar_bucle_quant()