import os
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import ccxt
import pandas as pd
import requests
from datetime import datetime

# =========================================================
# CONFIGURACIÓN Y CREDENCIALES DE TELEGRAM
# =========================================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8628860776:AAEQHlVjzM1fXFhBPuTPjrhzyoDKBamaKII")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "402919772")

def enviar_telegram(mensaje: str):
    """Envía notificaciones a Telegram en formato HTML."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensaje,
        "parse_mode": "HTML"
    }
    try:
        respuesta = requests.post(url, json=payload, timeout=10)
        if respuesta.status_code != 200:
            print(f"[ERROR TELEGRAM]: Respuesta {respuesta.status_code} - {respuesta.text}")
    except Exception as e:
        print(f"[ERROR TELEGRAM]: Error de conexión: {e}")

# =========================================================
# 1. SERVIDOR HTTP PARA MANTENER RENDER ACTIVO
# =========================================================
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write("🤖 Bot de Trading cuantitativo activo en Render!".encode('utf-8'))

    def log_message(self, format, *args):
        return

def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), DummyHandler)
    print(f"[SERVIDOR] Servidor HTTP de Render listo en el puerto {port}")
    server.serve_forever()

# =========================================================
# 2. ESTADO Y PARÁMETROS DE PAPER TRADING (SIMULACIÓN)
# =========================================================
STOP_LOSS_PCT = 0.02   # Protección: Máximo 2% de pérdida
TAKE_PROFIT_PCT = 0.04  # Objetivo: 4% de ganancia

# Cartera simulada inicial
saldo_usdt = 1000.0
btc_poseido = 0.0
precio_entrada = 0.0
en_posicion = False

exchange = ccxt.binance({'enableRateLimit': True})

def obtener_datos(simbolo='BTC/USDT'):
    """Obtiene velas de 1 hora y calcula Medias Móviles y RSI."""
    ohlcv = exchange.fetch_ohlcv(simbolo, timeframe='1h', limit=50)
    df = pd.DataFrame(ohlcv, columns=['tiempo', 'open', 'high', 'low', 'close', 'volume'])
    
    # Medias Móviles
    df['sma_rapida'] = df['close'].rolling(5).mean()
    df['sma_lenta'] = df['close'].rolling(20).mean()
    
    # Cálculo del indicador RSI (14 periodos)
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(window=14).mean()
    avg_loss = loss.rolling(window=14).mean()
    rs = avg_gain / avg_loss
    df['rsi'] = 100 - (100 / (1 + rs))
    
    return df

# =========================================================
# 3. ESCUCHADOR DE COMANDOS EN TIEMPO REAL (/saldo, /estado)
# =========================================================
def escuchar_comandos_telegram():
    """Escucha mensajes entrantes en Telegram y responde a /saldo, /estado, /ayuda."""
    offset = None
    print("[TELEGRAM] Escuchador de comandos interactivos activado...")
    
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
            params = {"timeout": 20, "offset": offset}
            res = requests.get(url, params=params, timeout=25).json()
            
            if "result" in res:
                for update in res["result"]:
                    offset = update["update_id"] + 1
                    message = update.get("message", {})
                    texto = message.get("text", "").strip().lower()
                    chat_id_remitente = str(message.get("chat", {}).get("id", ""))

                    # Seguridad: Responder únicamente si el mensaje proviene de tu CHAT_ID
                    if chat_id_remitente != str(TELEGRAM_CHAT_ID):
                        continue

                    # Obtener datos actualizados para la respuesta
                    try:
                        df = obtener_datos('BTC/USDT')
                        precio_actual = df.iloc[-1]['close']
                        rsi_actual = df.iloc[-1]['rsi']
                    except Exception:
                        precio_actual = 0.0
                        rsi_actual = 0.0

                    # COMANDO: /saldo
                    if texto.startswith("/saldo"):
                        valor_actual_btc = btc_poseido * precio_actual
                        valor_total = saldo_usdt + valor_actual_btc
                        pnl = valor_total - 1000.0
                        
                        msg = (
                            f"💼 <b>ESTADO DE TU CARTERA</b>\n\n"
                            f"💵 <b>USDT Disponible:</b> ${saldo_usdt:,.2f}\n"
                            f"🪙 <b>BTC en Cartera:</b> {btc_poseido:.6f} BTC\n"
                            f"📊 <b>Valor Total Estimado:</b> ${valor_total:,.2f}\n"
                            f"📈 <b>Ganancia/Pérdida Total:</b> ${pnl:+,.2f}"
                        )
                        enviar_telegram(msg)

                    # COMANDO: /estado
                    elif texto.startswith("/estado"):
                        if en_posicion and precio_entrada > 0:
                            pnl_pct = ((precio_actual - precio_entrada) / precio_entrada) * 100
                            estado_pos = (
                                f"🟢 <b>EN POSICIÓN (COMPRADO)</b>\n"
                                f"• Precio entrada: ${precio_entrada:,.2f}\n"
                                f"• Rendimiento actual: {pnl_pct:+.2f}%"
                            )
                        else:
                            estado_pos = "⚪ <b>EN ESPERA (Sin posición abierta)</b>"

                        msg = (
                            f"🤖 <b>ESTADO DEL BOT Y MERCADO</b>\n\n"
                            f"📍 <b>Par:</b> BTC/USDT\n"
                            f"💰 <b>Precio Actual:</b> ${precio_actual:,.2f}\n"
                            f"📊 <b>RSI (1h):</b> {rsi_actual:.1f}\n"
                            f"📌 <b>Posición:</b>\n{estado_pos}"
                        )
                        enviar_telegram(msg)

                    # COMANDO: /start O /ayuda
                    elif texto.startswith("/start") or texto.startswith("/ayuda") or texto.startswith("/help"):
                        msg = (
                            f"👋 <b>¡Comandos disponibles del Bot!</b>\n\n"
                            f"➡️ <b>/saldo</b> - Muestra tu saldo en USDT, BTC y ganancia/pérdida total\n"
                            f"➡️ <b>/estado</b> - Muestra el precio actual de BTC, RSI y posición\n"
                            f"➡️ <b>/ayuda</b> - Muestra este menú de ayuda"
                        )
                        enviar_telegram(msg)

        except Exception as e:
            print(f"[ERROR COMANDOS TELEGRAM]: {e}")
            time.sleep(5)

        time.sleep(1)

# =========================================================
# 4. ESTRATEGIA DE TRADING Y GESTIÓN DE RIESGO
# =========================================================
def ejecutar_estrategia():
    global saldo_usdt, btc_poseido, precio_entrada, en_posicion
    
    simbolo = 'BTC/USDT'
    df = obtener_datos(simbolo)
    
    actual = df.iloc[-1]
    anterior = df.iloc[-2]
    precio_actual = actual['close']
    rsi_actual = actual['rsi']
    
    fecha_hora = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    print(f"[{fecha_hora}] {simbolo} | Precio: ${precio_actual:,.2f} | RSI: {rsi_actual:.1f} | Saldo Simulado: ${saldo_usdt:,.2f}")

    # ---------------------------------------------------------
    # CASO A: TENEMOS UNA POSICIÓN ABIERTA (Gestionar Riesgo)
    # ---------------------------------------------------------
    if en_posicion:
        rendimiento = (precio_actual - precio_entrada) / precio_entrada
        var_pct = rendimiento * 100
        
        # 1. STOP LOSS ALCANZADO (-2%)
        if rendimiento <= -STOP_LOSS_PCT:
            saldo_usdt = btc_poseido * precio_actual
            msg = (
                f"🛡️ <b>[STOP LOSS EJECUTADO]</b>\n\n"
                f"• <b>Activo:</b> {simbolo}\n"
                f"• <b>Precio Venta:</b> ${precio_actual:,.2f}\n"
                f"• <b>Pérdida recortada:</b> {var_pct:.2f}%\n"
                f"💰 <b>Nuevo Saldo Total:</b> ${saldo_usdt:,.2f} USDT"
            )
            print(f"[VENTA - STOP LOSS] {msg}")
            enviar_telegram(msg)
            en_posicion = False
            btc_poseido = 0.0

        # 2. TAKE PROFIT ALCANZADO (+4%)
        elif rendimiento >= TAKE_PROFIT_PCT:
            saldo_usdt = btc_poseido * precio_actual
            msg = (
                f"🎯 <b>[TAKE PROFIT ALCANZADO]</b>\n\n"
                f"• <b>Activo:</b> {simbolo}\n"
                f"• <b>Precio Venta:</b> ${precio_actual:,.2f}\n"
                f"• <b>Ganancia Asegurada:</b> +{var_pct:.2f}%\n"
                f"💰 <b>Nuevo Saldo Total:</b> ${saldo_usdt:,.2f} USDT"
            )
            print(f"[VENTA - TAKE PROFIT] {msg}")
            enviar_telegram(msg)
            en_posicion = False
            btc_poseido = 0.0

        # 3. VENTA TÉCNICA (Cruce Bajista de Medias)
        elif anterior['sma_rapida'] >= anterior['sma_lenta'] and actual['sma_rapida'] < actual['sma_lenta']:
            saldo_usdt = btc_poseido * precio_actual
            msg = (
                f"🔴 <b>[VENTA TÉCNICA - CRUCE BAJISTA]</b>\n\n"
                f"• <b>Activo:</b> {simbolo}\n"
                f"• <b>Precio Venta:</b> ${precio_actual:,.2f}\n"
                f"• <b>Resultado Operación:</b> {var_pct:+.2f}%\n"
                f"💰 <b>Nuevo Saldo Total:</b> ${saldo_usdt:,.2f} USDT"
            )
            print(f"[VENTA TÉCNICA] {msg}")
            enviar_telegram(msg)
            en_posicion = False
            btc_poseido = 0.0

    # ---------------------------------------------------------
    # CASO B: NO TENEMOS POSICIÓN (Buscar Oportunidad de Compra)
    # ---------------------------------------------------------
    else:
        cruce_alcista = (anterior['sma_rapida'] <= anterior['sma_lenta']) and (actual['sma_rapida'] > actual['sma_lenta'])
        rsi_favorable = rsi_actual < 60  # Evita comprar sobrecalentado
        
        if cruce_alcista and rsi_favorable:
            btc_poseido = saldo_usdt / precio_actual
            precio_entrada = precio_actual
            
            sl_precio = precio_entrada * (1 - STOP_LOSS_PCT)
            tp_precio = precio_entrada * (1 + TAKE_PROFIT_PCT)
            
            msg = (
                f"🟢 <b>[COMPRA SIMULADA EJECUTADA]</b>\n\n"
                f"• <b>Activo:</b> {simbolo}\n"
                f"• <b>Precio Compra:</b> ${precio_actual:,.2f}\n"
                f"• <b>Cantidad:</b> {btc_poseido:.6f} BTC\n"
                f"• <b>RSI Actual:</b> {rsi_actual:.1f}\n"
                f"-----------------------------------\n"
                f"🛡️ <b>Stop Loss (-2%):</b> ${sl_precio:,.2f}\n"
                f"🎯 <b>Take Profit (+4%):</b> ${tp_precio:,.2f}"
            )
            print(f"[COMPRA] {msg}")
            enviar_telegram(msg)
            en_posicion = True
            saldo_usdt = 0.0

# =========================================================
# 5. BUCLE DE EJECUCIÓN PRINCIPAL
# =========================================================
if __name__ == '__main__':
    print("=== INICIANDO MOTOR DE TRADING CUANTITATIVO CON PAPER TRADING ===")
    
    # 1. Iniciar servidor HTTP para Render
    server_thread = threading.Thread(target=run_dummy_server, daemon=True)
    server_thread.start()

    # 2. Iniciar escuchador de comandos de Telegram en segundo plano
    telegram_thread = threading.Thread(target=escuchar_comandos_telegram, daemon=True)
    telegram_thread.start()

    # 3. Notificación inicial en Telegram
    enviar_telegram(
        "🧠 <b>[MODO PAPER TRADING ACTIVADO CON COMANDOS]</b>\n\n"
        "• <b>Capital Ficticio:</b> $1,000.00 USDT\n"
        "• <b>Stop Loss:</b> 2%\n"
        "• <b>Take Profit:</b> 4%\n"
        "• <b>Filtro RSI:</b> Sí (< 60)\n\n"
        "💬 <i>Escribe <b>/saldo</b> o <b>/estado</b> en este chat para consultar tu cuenta.</i>"
    )
    
    # 4. Bucle de monitoreo continuo (revisa cada 5 minutos)
    while True:
        try:
            ejecutar_estrategia()
            time.sleep(300)  # Revisa el mercado cada 5 minutos (300 segundos)
        except Exception as e:
            print(f"[ERROR EN BUCLE]: {e}")
            time.sleep(30)