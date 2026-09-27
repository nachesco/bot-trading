import os
import time
import threading
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
import ccxt
import pandas as pd
import requests
from datetime import datetime

# =========================================================
# CONFIGURACIÓN Y ESTADO DEL BOT
# =========================================================
ARCHIVO_ESTADO = "estado_trading.json"
STOP_LOSS_PCT = 0.02
TAKE_PROFIT_PCT = 0.04

saldo_usd = 1000.0
btc_poseido = 0.0
precio_entrada = 0.0
en_posicion = False
historial_operaciones = []

# Cargar estado guardado si existe
if os.path.exists(ARCHIVO_ESTADO):
    try:
        with open(ARCHIVO_ESTADO, "r") as f:
            estado = json.load(f)
            saldo_usd = estado.get("saldo_usd", 1000.0)
            btc_poseido = estado.get("btc_poseido", 0.0)
            precio_entrada = estado.get("precio_entrada", 0.0)
            en_posicion = estado.get("en_posicion", False)
            historial_operaciones = estado.get("historial_operaciones", [])
    except Exception as e:
        print(f"[ERROR CARGANDO ESTADO]: {e}")

def guardar_estado():
    estado = {
        "saldo_usd": saldo_usd,
        "btc_poseido": btc_poseido,
        "precio_entrada": precio_entrada,
        "en_posicion": en_posicion,
        "historial_operaciones": historial_operaciones[-20:] # Guardar últimas 20 operaciones
    }
    try:
        with open(ARCHIVO_ESTADO, "w") as f:
            json.dump(estado, f)
    except Exception as e:
        print(f"[ERROR GUARDANDO ESTADO]: {e}")

exchange = ccxt.kraken({'enableRateLimit': True})

def obtener_datos(simbolo='BTC/USD'):
    try:
        ohlcv = exchange.fetch_ohlcv(simbolo, timeframe='4h', limit=50)
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
    except Exception as e:
        print(f"[ERROR KRAKEN]: {e}")
        return None

# =========================================================
# SERVIDOR WEB PARA VER EL ESTADO EN EL NAVEGADOR
# =========================================================
class WebPanelHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()

        df = obtener_datos('BTC/USD')
        precio_actual = df.iloc[-1]['close'] if df is not None else 0.0
        rsi_actual = df.iloc[-1]['rsi'] if df is not None else 0.0

        valor_btc = btc_poseido * precio_actual
        valor_total = saldo_usd + valor_btc
        pnl = valor_total - 1000.0
        pnl_pct = (pnl / 1000.0) * 100

        filas_historial = ""
        for op in reversed(historial_operaciones):
            color = "#22c55e" if op.get("tipo") == "COMPRA" else ("#ef4444" if "STOP" in op.get("tipo") else "#3b82f6")
            filas_historial += f"""
            <tr>
                <td style="padding: 8px; border-bottom: 1px solid #334155;">{op.get('fecha', '')}</td>
                <td style="padding: 8px; border-bottom: 1px solid #334155; color: {color}; font-weight: bold;">{op.get('tipo', '')}</td>
                <td style="padding: 8px; border-bottom: 1px solid #334155;">${op.get('precio', 0):,.2f}</td>
                <td style="padding: 8px; border-bottom: 1px solid #334155;">{op.get('detalle', '')}</td>
            </tr>
            """

        if not filas_historial:
            filas_historial = "<tr><td colspan='4' style='padding: 12px; text-align: center; color: #94a3b8;'>Sin operaciones registradas aún.</td></tr>"

        html = f"""
        <!DOCTYPE html>
        <html lang="es">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Panel Bot de Trading</title>
            <style>
                body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #0f172a; color: #f8fafc; margin: 0; padding: 20px; }}
                .container {{ max-width: 800px; margin: 0 auto; }}
                .card {{ background-color: #1e293b; border-radius: 12px; padding: 20px; margin-bottom: 20px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1); }}
                .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; }}
                .stat-box {{ background-color: #0f172a; padding: 15px; border-radius: 8px; text-align: center; }}
                .stat-title {{ font-size: 0.85em; color: #94a3b8; margin-bottom: 5px; }}
                .stat-value {{ font-size: 1.4em; font-weight: bold; }}
                .positive {{ color: #22c55e; }}
                .negative {{ color: #ef4444; }}
                .status-badge {{ display: inline-block; padding: 4px 12px; border-radius: 20px; font-weight: bold; font-size: 0.9em; }}
                .badge-buy {{ background-color: #166534; color: #4ade80; }}
                .badge-wait {{ background-color: #334155; color: #cbd5e1; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 0.9em; }}
                th {{ text-align: left; padding: 8px; background-color: #0f172a; color: #94a3b8; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1 style="text-align: center; margin-bottom: 30px;">🤖 Panel de Control - Bot de Trading</h1>
                
                <div class="card">
                    <h2>📊 Resumen de Cartera</h2>
                    <div class="grid">
                        <div class="stat-box">
                            <div class="stat-title">Valor Total Estimado</div>
                            <div class="stat-value">${valor_total:,.2f}</div>
                        </div>
                        <div class="stat-box">
                            <div class="stat-title">Saldo en USD</div>
                            <div class="stat-value">${saldo_usd:,.2f}</div>
                        </div>
                        <div class="stat-box">
                            <div class="stat-title">BTC Poseído</div>
                            <div class="stat-value">{btc_poseido:.6f}</div>
                        </div>
                        <div class="stat-box">
                            <div class="stat-title">Rendimiento (P/L)</div>
                            <div class="stat-value {'positive' if pnl >= 0 else 'negative'}">${pnl:+,.2f} ({pnl_pct:+.2f}%)</div>
                        </div>
                    </div>
                </div>

                <div class="card">
                    <h2>📈 Estado del Mercado (BTC/USD)</h2>
                    <div class="grid">
                        <div class="stat-box">
                            <div class="stat-title">Precio BTC</div>
                            <div class="stat-value">${precio_actual:,.2f}</div>
                        </div>
                        <div class="stat-box">
                            <div class="stat-title">RSI (14)</div>
                            <div class="stat-value">{rsi_actual:.1f}</div>
                        </div>
                        <div class="stat-box">
                            <div class="stat-title">Estado Actual</div>
                            <div class="stat-value">
                                <span class="status-badge {'badge-buy' if en_posicion else 'badge-wait'}">
                                    {'COMPRADO' if en_posicion else 'EN ESPERA'}
                                </span>
                            </div>
                        </div>
                    </div>
                </div>

                <div class="card">
                    <h2>📜 Historial de Operaciones</h2>
                    <table>
                        <thead>
                            <tr>
                                <th>Fecha</th>
                                <th>Tipo</th>
                                <th>Precio BTC</th>
                                <th>Detalle</th>
                            </tr>
                        </thead>
                        <tbody>
                            {filas_historial}
                        </tbody>
                    </table>
                </div>
            </div>
        </body>
        </html>
        """
        self.wfile.write(html.encode('utf-8'))

    def log_message(self, format, *args):
        return

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), WebPanelHandler)
    print(f"[SERVIDOR WEB] Panel activo en el puerto {port}")
    server.serve_forever()

# =========================================================
# ESTRATEGIA Y LÓGICA DE TRADING
# =========================================================
def registrar_operacion(tipo, precio, detalle):
    fecha_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    historial_operaciones.append({
        "fecha": fecha_str,
        "tipo": tipo,
        "precio": precio,
        "detalle": detalle
    })

def ejecutar_estrategia():
    global saldo_usd, btc_poseido, precio_entrada, en_posicion
    simbolo = 'BTC/USD'
    
    df = obtener_datos(simbolo)
    if df is None: return
    
    precio_actual = df.iloc[-1]['close']
    
    vela_cerrada_actual = df.iloc[-2]
    vela_cerrada_anterior = df.iloc[-3]
    
    if en_posicion:
        rendimiento = (precio_actual - precio_entrada) / precio_entrada
        var_pct = rendimiento * 100
        
        # Stop Loss
        if rendimiento <= -STOP_LOSS_PCT:
            saldo_usd = btc_poseido * precio_actual
            print(f"[VENTA] Stop Loss a ${precio_actual:,.2f} ({var_pct:.2f}%)")
            registrar_operacion("STOP LOSS", precio_actual, f"Pérdida: {var_pct:.2f}% | Saldo: ${saldo_usd:,.2f}")
            en_posicion = False
            btc_poseido = 0.0
            guardar_estado()
            
        # Take Profit
        elif rendimiento >= TAKE_PROFIT_PCT:
            saldo_usd = btc_poseido * precio_actual
            print(f"[VENTA] Take Profit a ${precio_actual:,.2f} (+{var_pct:.2f}%)")
            registrar_operacion("TAKE PROFIT", precio_actual, f"Ganancia: +{var_pct:.2f}% | Saldo: ${saldo_usd:,.2f}")
            en_posicion = False
            btc_poseido = 0.0
            guardar_estado()
            
        # Venta Técnica
        elif vela_cerrada_anterior['sma_rapida'] >= vela_cerrada_anterior['sma_lenta'] and vela_cerrada_actual['sma_rapida'] < vela_cerrada_actual['sma_lenta']:
            saldo_usd = btc_poseido * precio_actual
            print(f"[VENTA] Cruce Bajista a ${precio_actual:,.2f} ({var_pct:+.2f}%)")
            registrar_operacion("VENTA TÉCNICA", precio_actual, f"Resultado: {var_pct:+.2f}% | Saldo: ${saldo_usd:,.2f}")
            en_posicion = False
            btc_poseido = 0.0
            guardar_estado()

    else:
        cruce_alcista = (vela_cerrada_anterior['sma_rapida'] <= vela_cerrada_anterior['sma_lenta']) and (vela_cerrada_actual['sma_rapida'] > vela_cerrada_actual['sma_lenta'])
        rsi_favorable = vela_cerrada_actual['rsi'] < 60
        
        if cruce_alcista and rsi_favorable:
            btc_poseido = saldo_usd / precio_actual
            precio_entrada = precio_actual
            
            print(f"[COMPRA] Entrada a ${precio_actual:,.2f}")
            registrar_operacion("COMPRA", precio_actual, f"Cantidad: {btc_poseido:.6f} BTC")
            en_posicion = True
            saldo_usd = 0.0
            guardar_estado()

# =========================================================
# ARRANQUE PRINCIPAL
# =========================================================
if __name__ == '__main__':
    threading.Thread(target=run_web_server, daemon=True).start()
    print("🧠 Bot iniciado sin dependencias de Telegram.")
    
    while True:
        try:
            ejecutar_estrategia()
        except Exception as e:
            print(f"[ERROR BUCLE]: {e}")
        time.sleep(300)