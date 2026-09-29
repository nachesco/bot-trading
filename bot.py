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
TRAILING_STOP_PCT = 1.5  # Distancia en % desde el precio máximo alcanzado

# --- CONEXIÓN A MONGODB ---
MONGO_URI = os.environ.get("MONGO_URI")
if MONGO_URI:
    cliente_mongo = MongoClient(MONGO_URI)
    db = cliente_mongo['trading_bot']
    coleccion_estado = db['estado']
else:
    print("⚠️ ADVERTENCIA: No se encontró MONGO_URI en las variables de Render.", flush=True)

def obtener_estado():
    """Lee el estado de la base de datos o lo inicializa con las nuevas métricas."""
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
            "stop_dinamico": 0.0
        }
        coleccion_estado.insert_one(estado)
    
    # Asegurar que las llaves nuevas existan si la DB es antigua
    if "precio_max_alcanzado" not in estado:
        estado.update({
            "precio_max_alcanzado": estado.get("precio_compra", 0.0),
            "stop_dinamico": 0.0
        })
    if "balance_history" not in estado:
        estado.update({
            "balance_history": [1000.0], 
            "trades_ganadores": 0, 
            "trades_perdedores": 0, 
            "gross_profit": 0.0, 
            "gross_loss": 0.0, 
            "max_balance": estado.get("saldo_usd", 1000.0)
        })
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
        
        # Cálculos de métricas
        total_trades = estado['trades_ganadores'] + estado['trades_perdedores']
        win_rate = (estado['trades_ganadores'] / total_trades * 100) if total_trades > 0 else 0
        profit_factor = (estado['gross_profit'] / estado['gross_loss']) if estado['gross_loss'] > 0 else (estado['gross_profit'] if estado['gross_profit'] > 0 else 0)
        
        saldo_actual = estado['saldo_usd'] if not estado['en_posicion'] else (estado['btc_poseidos'] * estado.get('ultimo_precio', estado['precio_compra']))
        drawdown = ((estado['max_balance'] - saldo_actual) / estado['max_balance'] * 100) if estado['max_balance'] > 0 else 0

        historial_items = "".join([f"<li>{item}</li>" for item in estado.get('historial', [])])
        balance_json = json.dumps(estado['balance_history'])
        
        # Info extra del Stop Dinámico para el dashboard
        stop_info_html = f"Stop Dinámico: ${estado.get('stop_dinamico', 0):,.2f}" if estado['en_posicion'] else "Sin Stop Activo"

        html = f"""
        <html>
        <head>
            <title>Trading Bot Dashboard</title>
            <meta http-equiv="refresh" content="30">
            <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
            <style>
                body {{ font-family: monospace; padding: 20px; background-color: #0b0f19; color: #00ff66; max-width: 900px; margin: auto; }}
                h2 {{ color: #ffffff; border-bottom: 1px solid #333; padding-bottom: 10px; }}
                .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
                .box {{ background: #151a28; padding: 20px; border-radius: 8px; border: 1px solid #2a3441; margin-bottom: 20px; }}
                .metric-title {{ color: #8892b0; font-size: 12px; text-transform: uppercase; }}
                .metric-value {{ color: #ffffff; font-size: 24px; margin-top: 5px; }}
                ul {{ list-style-type: none; padding: 0; margin: 0; }}
                li {{ padding: 8px 0; border-bottom: 1px solid #2a3441; color: #a8b2d1; font-size: 13px; }}
                canvas {{ max-height: 300px; }}
            </style>
        </head>
        <body>
            <h2>📊 Panel de Control Cuantitativo (BTC/USD - Kraken)</h2>
            
            <!-- TARJETAS DE ESTADO -->
            <div class="grid">
                <div class="box">
                    <div class="metric-title">Capital Actual</div>
                    <div class="metric-value">${saldo_actual:,.2f} USD</div>
                    <div style="margin-top:10px; color:{'#ff4444' if estado['en_posicion'] else '#00ff66'}">
                        Estado: {'🟢 EN POSICIÓN' if estado['en_posicion'] else '🔴 LÍQUIDO'}
                    </div>
                </div>
                <div class="box">
                    <div class="metric-title">Win Rate / Profit Factor</div>
                    <div class="metric-value">{win_rate:.1f}% / {profit_factor:.2f}</div>
                    <div style="margin-top:10px; color:#ffb86c">
                        {stop_info_html}
                    </div>
                </div>
            </div>

            <!-- GRÁFICO CHART.JS -->
            <div class="box">
                <canvas id="equityChart"></canvas>
            </div>

            <!-- HISTORIAL -->
            <div class="box">
                <div class="metric-title" style="margin-bottom:15px;">Última Actividad</div>
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
                            backgroundColor: 'rgba(0, 255, 102, 0.1)',
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
    """Actualiza las métricas cuando se cierra una posición"""
    estado['balance_history'].append(saldo_obtenido)
    
    if saldo_obtenido > estado['max_balance']:
        estado['max_balance'] = saldo_obtenido
        
    if ganancia_usd > 0:
        estado['trades_ganadores'] += 1
        estado['gross_profit'] += ganancia_usd
    else:
        estado['trades_perdedores'] += 1
        estado['gross_loss'] += abs(ganancia_usd)

# --- AHORA (Con Kraken) ---
def analizar_y_operar():
    estado = obtener_estado()
    
    exchange = ccxt.kraken({'enableRateLimit': True})
    ohlcv = exchange.fetch_ohlcv('BTC/USD', timeframe='15m', limit=50)
    
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['sma_20'] = df['close'].rolling(20).mean()
    
    precio_actual = df['close'].iloc[-1]
    media_actual = df['sma_20'].iloc[-1]
    
    # Guardamos el último precio para que el dashboard calcule el saldo
    estado['ultimo_precio'] = precio_actual 

    if estado['en_posicion']:
        porcentaje_variacion = ((precio_actual - estado['precio_compra']) / estado['precio_compra']) * 100
        saldo_obtenido = estado['btc_poseidos'] * precio_actual
        ganancia_usd = saldo_obtenido - (estado['btc_poseidos'] * estado['precio_compra'])

        # --- LÓGICA DE TRAILING STOP ---
        # 1. Actualizamos el precio máximo alcanzado
        precio_max_alcanzado = estado.get('precio_max_alcanzado', estado['precio_compra'])
        if precio_actual > precio_max_alcanzado:
            precio_max_alcanzado = precio_actual
            estado['precio_max_alcanzado'] = precio_max_alcanzado

        # 2. Calculamos los dos stops (El fijo inicial y el dinámico)
        precio_stop_inicial = estado['precio_compra'] * (1 - STOP_LOSS_PCT / 100)
        precio_trailing = precio_max_alcanzado * (1 - TRAILING_STOP_PCT / 100)
        
        # 3. El stop real será el mayor de los dos
        stop_dinamico = max(precio_stop_inicial, precio_trailing)
        estado['stop_dinamico'] = stop_dinamico

        # TAKE PROFIT (+4%) - Opcional, lo mantenemos por si quieres un límite superior duro
        if porcentaje_variacion >= TAKE_PROFIT_PCT:
            actualizar_estadisticas_venta(estado, saldo_obtenido, ganancia_usd)
            estado['saldo_usd'] = saldo_obtenido
            estado['btc_poseidos'] = 0.0
            estado['en_posicion'] = False
            estado['precio_max_alcanzado'] = 0.0
            estado['stop_dinamico'] = 0.0
            registrar_evento(estado, f"🎯 TAKE PROFIT (+{TAKE_PROFIT_PCT}%) | Venta: ${precio_actual:,.2f} | Ganancia: +${ganancia_usd:,.2f}")

        # STOP DINÁMICO (Stop Loss / Trailing Stop)
        elif precio_actual <= stop_dinamico:
            actualizar_estadisticas_venta(estado, saldo_obtenido, ganancia_usd)
            estado['saldo_usd'] = saldo_obtenido
            estado['btc_poseidos'] = 0.0
            estado['en_posicion'] = False
            estado['precio_max_alcanzado'] = 0.0
            estado['stop_dinamico'] = 0.0
            
            # Formatear el mensaje dependiendo de si fue el Trailing o el Inicial
            if stop_dinamico == precio_trailing and precio_trailing > precio_stop_inicial:
                registrar_evento(estado, f"🛡️ TRAILING STOP | Venta: ${precio_actual:,.2f} | Resultado: ${ganancia_usd:+,.2f}")
            else:
                registrar_evento(estado, f"🛑 STOP LOSS (-{STOP_LOSS_PCT}%) | Venta: ${precio_actual:,.2f} | Pérdida: -${abs(ganancia_usd):,.2f}")

        # VENTA POR ESTRATEGIA (Cruce bajista SMA 20)
        elif precio_actual < media_actual:
            actualizar_estadisticas_venta(estado, saldo_obtenido, ganancia_usd)
            estado['saldo_usd'] = saldo_obtenido
            estado['btc_poseidos'] = 0.0
            estado['en_posicion'] = False
            estado['precio_max_alcanzado'] = 0.0
            estado['stop_dinamico'] = 0.0
            registrar_evento(estado, f"🔴 VENTA (Bajo SMA) | Venta: ${precio_actual:,.2f} | Resultado: ${ganancia_usd:+,.2f}")
        
        else:
            registrar_evento(estado, f"📦 Posición Activa | BTC: ${precio_actual:,.2f} | PnL: {porcentaje_variacion:+.2f}%")

    else:
        # COMPRA POR ESTRATEGIA (Cruce alcista SMA 20)
        if precio_actual > media_actual:
            estado['btc_poseidos'] = estado['saldo_usd'] / precio_actual
            estado['precio_compra'] = precio_actual
            estado['precio_max_alcanzado'] = precio_actual # Inicializamos el trailing al comprar
            estado['en_posicion'] = True
            estado['saldo_usd'] = 0.0
            registrar_evento(estado, f"🟢 COMPRA | Entrada: ${precio_actual:,.2f} | SMA: ${media_actual:,.2f}")
        else:
            registrar_evento(estado, f"💤 En Espera | BTC: ${precio_actual:,.2f} | SMA: ${media_actual:,.2f}")

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
        print("=== BOT V6 INICIADO (Dashboard Profesional - KRAKEN) ===", flush=True)
        hilo_bot = threading.Thread(target=bucle_trading)
        hilo_bot.daemon = True
        hilo_bot.start()
        run_server()