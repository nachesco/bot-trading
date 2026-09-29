import ccxt
import pandas as pd
import time
import os
import threading
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from pymongo import MongoClient
from datetime import datetime

# --- CONFIGURACIÓN DE RIESGO Y ESTRATEGIA ---
STOP_LOSS_PCT = 2.0
TAKE_PROFIT_PCT = 4.0
TRAILING_STOP_PCT = 1.5  # Distancia en % desde el máximo

# --- PARÁMETROS DE FILTROS ---
RSI_PERIODO = 14
RSI_MIN = 50.0  # Mínimo impulso comprador
RSI_MAX = 70.0  # Evita entrar sobrecomprado
SMA_1H_PERIODO = 20  # Periodo de la media en marco de 1 hora

# --- CONEXIÓN A MONGODB ---
MONGO_URI = os.environ.get("MONGO_URI")
if MONGO_URI:
    cliente_mongo = MongoClient(MONGO_URI)
    db = cliente_mongo['trading_bot']
    coleccion_estado = db['estado']
else:
    print("⚠️ ADVERTENCIA: No se encontró MONGO_URI en las variables de Render.", flush=True)

def calcular_rsi(series, period=14):
    """Calcula el Relative Strength Index (RSI) usando suavizado exponencial de Wilder."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -1 * delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def obtener_estado():
    """Lee el estado de la base de datos o lo inicializa."""
    estado = coleccion_estado.find_one({"_id": "estado_actual"})
    if not estado:
        estado = {
            "_id": "estado_actual",
            "saldo_usd": 1000.0,
            "btc_poseidos": 0.0,
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
            "ultimo_precio": 0.0,
            "ultima_sma_15m": 0.0,
            "ultima_sma_1h": 0.0,
            "ultimo_rsi": 0.0
        }
        coleccion_estado.insert_one(estado)
    
    # Asegurar retrocompatibilidad con esquemas antiguos
    if "precio_max_alcanzado" not in estado:
        estado.update({"precio_max_alcanzado": estado.get("precio_compra", 0.0), "stop_dinamico": 0.0})
    if "balance_history" not in estado:
        estado.update({"balance_history": [1000.0], "trades_ganadores": 0, "trades_perdedores": 0, "gross_profit": 0.0, "gross_loss": 0.0, "max_balance": estado.get("saldo_usd", 1000.0)})
    if "ultimo_precio" not in estado:
        estado.update({"ultimo_precio": 0.0, "ultima_sma_15m": 0.0, "ultima_sma_1h": 0.0, "ultimo_rsi": 0.0})
    return estado

def guardar_estado(estado):
    coleccion_estado.update_one({"_id": "estado_actual"}, {"$set": estado})

def registrar_evento(estado, texto):
    fecha_hora = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    linea = f"[{fecha_hora}] {texto}"
    historial = estado.get('historial', [])
    historial.insert(0, linea)
    estado['historial'] = historial[:15]
    print(linea, flush=True)

# --- PANEL WEB INTERACTIVO ---
class WebHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        
        estado = obtener_estado()
        
        # Métricas Cuantitativas
        total_trades = estado['trades_ganadores'] + estado['trades_perdedores']
        win_rate = (estado['trades_ganadores'] / total_trades * 100) if total_trades > 0 else 0
        profit_factor = (estado['gross_profit'] / estado['gross_loss']) if estado['gross_loss'] > 0 else (estado['gross_profit'] if estado['gross_profit'] > 0 else 0)
        
        precio_actual = estado.get('ultimo_precio', estado['precio_compra'])
        saldo_actual = estado['saldo_usd'] if not estado['en_posicion'] else (estado['btc_poseidos'] * precio_actual)
        drawdown = ((estado['max_balance'] - saldo_actual) / estado['max_balance'] * 100) if estado['max_balance'] > 0 else 0

        # Colores dinámicos
        capital_color = "#00ff66" if saldo_actual >= 1000.0 else "#ff4444"
        pf_color = "#00ff66" if profit_factor >= 1.0 else "#ffb86c"

        # PnL Abierto (Flotante)
        if estado['en_posicion'] and estado['precio_compra'] > 0 and precio_actual > 0:
            pnl_pct = ((precio_actual - estado['precio_compra']) / estado['precio_compra']) * 100
            pnl_color = "#00ff66" if pnl_pct >= 0 else "#ff4444"
            pnl_str = f" <span style='color:{pnl_color}; font-size:13px; font-weight:bold;'>(PnL: {pnl_pct:+.2f}%)</span>"
        else:
            pnl_str = ""

        # Información del Stop Dinámico
        stop_val = estado.get('stop_dinamico', 0.0)
        if estado['en_posicion'] and stop_val > 0:
            stop_info_html = f"Stop Dinámico: ${stop_val:,.2f}"
        elif estado['en_posicion']:
            precio_stop_est = estado['precio_compra'] * (1 - STOP_LOSS_PCT / 100)
            stop_info_html = f"Stop Inicial: ${precio_stop_est:,.2f}"
        else:
            stop_info_html = "Sin Stop Activo"

        historial_items = "".join([f"<li>{item}</li>" for item in estado.get('historial', [])])
        balance_json = json.dumps(estado['balance_history'])

        # Valores de Mercado en Vivo
        btc_price_str = f"${precio_actual:,.2f}" if precio_actual > 0 else "Cargando..."
        sma_15m_str = f"${estado.get('ultima_sma_15m', 0):,.2f}" if estado.get('ultima_sma_15m', 0) > 0 else "--"
        sma_1h_str = f"${estado.get('ultima_sma_1h', 0):,.2f}" if estado.get('ultima_sma_1h', 0) > 0 else "--"
        rsi_str = f"{estado.get('ultimo_rsi', 0):.1f}" if estado.get('ultimo_rsi', 0) > 0 else "--"

        html = f"""
        <html>
        <head>
            <title>Trading Bot Dashboard</title>
            <meta http-equiv="refresh" content="30">
            <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
            <style>
                body {{ font-family: monospace; padding: 20px; background-color: #0b0f19; color: #00ff66; max-width: 950px; margin: auto; }}
                h2 {{ color: #ffffff; border-bottom: 1px solid #333; padding-bottom: 10px; }}
                .grid-3 {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 15px; margin-bottom: 20px; }}
                .box {{ background: #151a28; padding: 18px; border-radius: 8px; border: 1px solid #2a3441; }}
                .metric-title {{ color: #8892b0; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; }}
                .metric-value {{ font-size: 22px; font-weight: bold; margin-top: 5px; margin-bottom: 5px; }}
                .sub-info {{ font-size: 12px; color: #a8b2d1; margin-top: 6px; }}
                ul {{ list-style-type: none; padding: 0; margin: 0; }}
                li {{ padding: 8px 0; border-bottom: 1px solid #2a3441; color: #a8b2d1; font-size: 13px; }}
                canvas {{ max-height: 280px; }}
                @media (max-width: 768px) {{ .grid-3 {{ grid-template-columns: 1fr; }} }}
            </style>
        </head>
        <body>
            <h2>📊 Panel de Control Cuantitativo (BTC/USD - Kraken)</h2>
            
            <!-- TARJETAS SUPERIORES (3 COLUMNAS) -->
            <div class="grid-3">
                <!-- TARJETA 1: CAPITAL & ESTADO -->
                <div class="box">
                    <div class="metric-title">Capital Actual</div>
                    <div class="metric-value" style="color: {capital_color}">${saldo_actual:,.2f} USD</div>
                    <div class="sub-info">
                        Estado: {'🟢 EN POSICIÓN' if estado['en_posicion'] else '🔴 LÍQUIDO'}{pnl_str}
                    </div>
                </div>

                <!-- TARJETA 2: DESEMPETAÑA / MÉTRICAS -->
                <div class="box">
                    <div class="metric-title">Win Rate / Profit Factor</div>
                    <div class="metric-value" style="color: #ffffff">
                        {win_rate:.1f}% / <span style="color: {pf_color}">{profit_factor:.2f}</span>
                    </div>
                    <div class="sub-info" style="color: #ffb86c">
                        {stop_info_html} | DD: -{drawdown:.2f}%
                    </div>
                </div>

                <!-- TARJETA 3: MERCADO EN VIVO -->
                <div class="box">
                    <div class="metric-title">Mercado BTC en Vivo</div>
                    <div class="metric-value" style="color: #ffffff">{btc_price_str}</div>
                    <div class="sub-info">
                        SMA 15m: {sma_15m_str} | 1h: {sma_1h_str} | RSI: {rsi_str}
                    </div>
                </div>
            </div>

            <!-- GRÁFICO DE CAPITAL -->
            <div class="box" style="margin-bottom: 20px;">
                <canvas id="equityChart"></canvas>
            </div>

            <!-- HISTORIAL DE ACTIVIDAD -->
            <div class="box">
                <div class="metric-title" style="margin-bottom:15px; color: #ffffff; font-size: 13px;">Última Actividad</div>
                <ul>{historial_items if historial_items else '<li>Sin actividad registrada aún.</li>'}</ul>
            </div>

            <script>
                const ctx = document.getElementById('equityChart').getContext('2d');
                const balances = {balance_json};
                const labels = balances.map((_, index) => index === 0 ? 'Inicio' : 'Op ' + index);
                
                new Chart(ctx, {{
                    type: 'line',
                    data: {{
                        labels: labels,
                        datasets: [{{
                            label: 'Evolución del Capital (USD)',
                            data: balances,
                            borderColor: '#00ff66',
                            backgroundColor: 'rgba(0, 255, 102, 0.08)',
                            borderWidth: 2,
                            fill: true,
                            tension: 0.3,
                            pointRadius: 4,
                            pointBackgroundColor: '#ffffff'
                        }}]
                    }},
                    options: {{
                        responsive: true,
                        plugins: {{ legend: {{ display: false }} }},
                        scales: {{
                            y: {{ grid: {{ color: '#2a3441' }}, ticks: {{ color: '#8892b0' }} }},
                            x: {{ grid: {{ display: false }}, ticks: {{ color: '#8892b0' }} }}
                        }}
                    }}
                }});
            </script>
        </body>
        </html>
        """
        self.wfile.write(html.encode('utf-8'))

    def log_message(self, format, *args):
        pass

def run_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), WebHandler)
    server.serve_forever()

# --- LÓGICA DE TRADING ---
def actualizar_estadisticas_venta(estado, saldo_obtenido, ganancia_usd):
    """Actualiza las métricas cuando se cierra una posición."""
    estado['balance_history'].append(saldo_obtenido)
    if saldo_obtenido > estado['max_balance']:
        estado['max_balance'] = saldo_obtenido
        
    if ganancia_usd > 0:
        estado['trades_ganadores'] += 1
        estado['gross_profit'] += ganancia_usd
    else:
        estado['trades_perdedores'] += 1
        estado['gross_loss'] += abs(ganancia_usd)

def analizar_y_operar():
    estado = obtener_estado()
    exchange = ccxt.kraken({'enableRateLimit': True})
    
    # 1. Marco de 15 minutos (Ejecución y Volumen)
    ohlcv_15m = exchange.fetch_ohlcv('BTC/USD', timeframe='15m', limit=50)
    df_15m = pd.DataFrame(ohlcv_15m, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df_15m['sma_20'] = df_15m['close'].rolling(20).mean()
    df_15m['vol_sma'] = df_15m['volume'].rolling(20).mean()  # Filtro de volumen institucional
    df_15m['rsi'] = calcular_rsi(df_15m['close'], period=RSI_PERIODO)
    
    # Datos en vivo (para gestión de posiciones y stops)
    precio_actual = df_15m['close'].iloc[-1]
    media_15m_actual = df_15m['sma_20'].iloc[-1]
    
    # Datos de vela cerrada (iloc[-2]) para confirmación sólida de entrada
    precio_cierre_15m = df_15m['close'].iloc[-2]
    media_15m_cerrada = df_15m['sma_20'].iloc[-2]
    rsi_cerrado = df_15m['rsi'].iloc[-2]
    volumen_cerrado = df_15m['volume'].iloc[-2]
    volumen_media_cerrada = df_15m['vol_sma'].iloc[-2]
    
    # 2. Marco de 1 hora (Filtro Multi-Timeframe Macro)
    ohlcv_1h = exchange.fetch_ohlcv('BTC/USD', timeframe='1h', limit=50)
    df_1h = pd.DataFrame(ohlcv_1h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df_1h['sma_1h'] = df_1h['close'].rolling(SMA_1H_PERIODO).mean()
    media_1h = df_1h['sma_1h'].iloc[-1]

    # Guardar estado de mercado para el dashboard
    estado['ultimo_precio'] = precio_actual 
    estado['ultima_sma_15m'] = media_15m_actual  # Mostramos la media actual en vivo
    estado['ultima_sma_1h'] = media_1h
    estado['ultimo_rsi'] = rsi_cerrado

    if estado['en_posicion']:
        porcentaje_variacion = ((precio_actual - estado['precio_compra']) / estado['precio_compra']) * 100
        saldo_obtenido = estado['btc_poseidos'] * precio_actual
        ganancia_usd = saldo_obtenido - (estado['btc_poseidos'] * estado['precio_compra'])

        # Trailing Stop & Stop Loss dinámico evaluado en tiempo real
        precio_max_alcanzado = estado.get('precio_max_alcanzado', estado['precio_compra'])
        if precio_actual > precio_max_alcanzado:
            precio_max_alcanzado = precio_actual
            estado['precio_max_alcanzado'] = precio_max_alcanzado

        precio_stop_inicial = estado['precio_compra'] * (1 - STOP_LOSS_PCT / 100)
        precio_trailing = precio_max_alcanzado * (1 - TRAILING_STOP_PCT / 100)
        stop_dinamico = max(precio_stop_inicial, precio_trailing)
        estado['stop_dinamico'] = stop_dinamico

        # CONDICIONES DE SALIDA
        if porcentaje_variacion >= TAKE_PROFIT_PCT:
            actualizar_estadisticas_venta(estado, saldo_obtenido, ganancia_usd)
            estado['saldo_usd'] = saldo_obtenido
            estado['btc_poseidos'] = 0.0
            estado['en_posicion'] = False
            estado['precio_max_alcanzado'] = 0.0
            estado['stop_dinamico'] = 0.0
            registrar_evento(estado, f"🎯 TAKE PROFIT (+{TAKE_PROFIT_PCT}%) | Venta: ${precio_actual:,.2f} | Ganancia: +${ganancia_usd:,.2f}")

        elif precio_actual <= stop_dinamico:
            actualizar_estadisticas_venta(estado, saldo_obtenido, ganancia_usd)
            estado['saldo_usd'] = saldo_obtenido
            estado['btc_poseidos'] = 0.0
            estado['en_posicion'] = False
            estado['precio_max_alcanzado'] = 0.0
            estado['stop_dinamico'] = 0.0
            
            if stop_dinamico == precio_trailing and precio_trailing > precio_stop_inicial:
                registrar_evento(estado, f"🛡️ TRAILING STOP | Venta: ${precio_actual:,.2f} | Resultado: ${ganancia_usd:+,.2f}")
            else:
                registrar_evento(estado, f"🛑 STOP LOSS (-{STOP_LOSS_PCT}%) | Venta: ${precio_actual:,.2f} | Pérdida: -${abs(ganancia_usd):,.2f}")

        elif precio_actual < media_15m_actual:
            actualizar_estadisticas_venta(estado, saldo_obtenido, ganancia_usd)
            estado['saldo_usd'] = saldo_obtenido
            estado['btc_poseidos'] = 0.0
            estado['en_posicion'] = False
            estado['precio_max_alcanzado'] = 0.0
            estado['stop_dinamico'] = 0.0
            registrar_evento(estado, f"🔴 VENTA (Bajo SMA 15m) | Venta: ${precio_actual:,.2f} | Resultado: ${ganancia_usd:+,.2f}")
        
        else:
            registrar_evento(estado, f"📦 Posición Activa | BTC: ${precio_actual:,.2f} | PnL: {porcentaje_variacion:+.2f}%")

    else:
        # CONDICIONES DE ENTRADA (4 FILTROS OBLIGATORIOS EVALUADOS EN VELA CERRADA)
        cruce_alcista_confirmado = precio_cierre_15m > media_15m_cerrada
        tendencia_1h_alcista = precio_actual > media_1h
        rsi_optimo = RSI_MIN <= rsi_cerrado <= RSI_MAX
        volumen_optimo = volumen_cerrado > volumen_media_cerrada

        if cruce_alcista_confirmado and tendencia_1h_alcista and rsi_optimo and volumen_optimo:
            estado['btc_poseidos'] = estado['saldo_usd'] / precio_actual
            estado['precio_compra'] = precio_actual
            estado['precio_max_alcanzado'] = precio_actual
            estado['stop_dinamico'] = precio_actual * (1 - STOP_LOSS_PCT / 100)
            estado['en_posicion'] = True
            estado['saldo_usd'] = 0.0
            registrar_evento(estado, f"🟢 COMPRA | Entrada: ${precio_actual:,.2f} | Vol: OK | RSI: {rsi_cerrado:.1f}")
        else:
            bloqueos = []
            if not cruce_alcista_confirmado: bloqueos.append("15m < SMA")
            if not tendencia_1h_alcista: bloqueos.append(f"1h Bajista")
            if not rsi_optimo: bloqueos.append(f"RSI: {rsi_cerrado:.1f}")
            if not volumen_optimo: bloqueos.append("Vol bajo")
            
            info_filtro = " | ".join(bloqueos)
            registrar_evento(estado, f"💤 En Espera | BTC: ${precio_actual:,.2f} | [{info_filtro}]")

    guardar_estado(estado)

def bucle_trading():
    time.sleep(5)
    while True:
        try:
            analizar_y_operar()
        except Exception as e:
            print(f"❌ Error en bucle: {e}", flush=True)
        time.sleep(300)

if __name__ == '__main__':
    if MONGO_URI:
        print("=== BOT V7 INICIADO (Cierre de Vela + Filtro Volumen + Dashboard V2) ===", flush=True)
        hilo_bot = threading.Thread(target=bucle_trading)
        hilo_bot.daemon = True
        hilo_bot.start()
        run_server()