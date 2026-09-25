import os
import time
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import ccxt
import pandas as pd
import requests

# =========================================================
# 1. SERVIDOR DE SALUD PARA RENDER (CORREGIDO Y ÚNICO)
# =========================================================
class RenderHealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(b"Bot de Trading activo")

    def do_HEAD(self):
        """Evita el error 501 de Render en comprobaciones automaticas."""
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()

    def log_message(self, format, *args):
        """Silencia los logs innecesarios de peticiones HTTP."""
        return

def iniciar_servidor_salud():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer.allow_reuse_address = True
    server = HTTPServer(('0.0.0.0', port), RenderHealthHandler)
    print(f" Servidor web de salud activo en el puerto {port}")
    server.serve_forever()

# Iniciar servidor web una sola vez en un hilo secundario
threading.Thread(target=iniciar_servidor_salud, daemon=True).start()

# =========================================================
# 2. CONFIGURACIÓN Y CREDENCIALES DE TELEGRAM
# =========================================================
# Lee de las variables de entorno de Render, o usa tus datos por defecto si fallan
TOKEN = os.environ.get("TELEGRAM_TOKEN", "88628860776:AAE5I29ZKaNQHdxbNwynFXfWiB3HoN9XtAo")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "402919772")

def enviar_telegram(mensaje: str):
    """Envía un mensaje con formato Markdown a Telegram."""
    if not TOKEN or not CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": mensaje,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error al enviar a Telegram: {e}")

# =========================================================
# 3. ESTRATEGIA, PARÁMETROS Y PERSISTENCIA DE ESTADO
# =========================================================
STOP_LOSS_PCT = 2.0     # Vende automáticamente si cae un 2%
TAKE_PROFIT_PCT = 4.0   # Vende automáticamente si sube un 4%
RSI_MAX_COMPRA = 70.0   # Filtro de sobrecompra

ARCHIVO_ESTADO = "estado_bot.json"
saldo_usd = 1000.0
btc_poseidos = 0.0
precio_compra = 0.0
en_posicion = False

def cargar_estado():
    """Recupera la cartera guardada para que no se pierda al reiniciar Render."""
    global saldo_usd, btc_poseidos, precio_compra, en_posicion
    if os.path.exists(ARCHIVO_ESTADO):
        try:
            with open(ARCHIVO_ESTADO, "r") as f:
                datos = json.load(f)
                saldo_usd = datos.get("saldo_usd", 1000.0)
                btc_poseidos = datos.get("btc_poseidos", 0.0)
                precio_compra = datos.get("precio_compra", 0.0)
                en_posicion = datos.get("en_posicion", False)
        except Exception as e:
            print(f"Error al cargar estado: {e}")

def guardar_estado():
    """Guarda el estado actual en disco."""
    try:
        with open(ARCHIVO_ESTADO, "w") as f:
            json.dump({
                "saldo_usd": saldo_usd,
                "btc_poseidos": btc_poseidos,
                "precio_compra": precio_compra,
                "en_posicion": en_posicion
            }, f)
    except Exception as e:
        print(f"Error al guardar estado: {e}")

# =========================================================
# 4. INDICADORES TÉCNICOS Y ANÁLISIS
# =========================================================
def calcular_rsi(df: pd.DataFrame, periodo: int = 14) -> pd.Series:
    delta = df['close'].diff()
    gain = delta.clip(lower=0)
    loss = -1 * delta.clip(upper=0)
    ema_gain = gain.ewm(com=periodo - 1, adjust=False).mean()
    ema_loss = loss.ewm(com=periodo - 1, adjust=False).mean()
    rs = ema_gain / ema_loss
    return 100 - (100 / (1 + rs))

# =========================================================
# 5. COMANDOS INTERACTIVOS DE TELEGRAM (/saldo y /estado)
# =========================================================
def escuchar_comandos():
    """Atiende comandos en Telegram para consultar el estado bajo demanda."""
    offset = None
    exchange = ccxt.binance()
    while True:
        try:
            url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
            params = {"timeout": 20, "offset": offset}
            res = requests.get(url, params=params, timeout=25).json()
            
            if "result" in res:
                for update in res["result"]:
                    offset = update["update_id"] + 1
                    msg = update.get("message", {})
                    texto = msg.get("text", "").strip().lower()
                    remitente = str(msg.get("chat", {}).get("id", ""))
                    
                    if remitente != CHAT_ID:
                        continue
                        
                    ohlcv = exchange.fetch_ohlcv('BTC/USDT', timeframe='1h', limit=5)
                    precio_actual = ohlcv[-1][4]
                    
                    if texto in ["/saldo", "saldo"]:
                        total = saldo_usd if not en_posicion else (btc_poseidos * precio_actual)
                        enviar_telegram(
                            f"💼 *ESTADO DE TU CARTERA*\n\n"
                            f"• *USDT Disponible:* `${saldo_usd:,.2f}`\n"
                            f"• *BTC Poseído:* `{btc_poseidos:.6f}`\n"
                            f"• *Valor Total:* `${total:,.2f} USDT`"
                        )
                    elif texto in ["/estado", "estado"]:
                        pos_str = "🟢 COMPRADO" if en_posicion else "⚪ LIQUIDEZ (USDT)"
                        enviar_telegram(
                            f"📊 *ESTADO DEL MERCADO*\n\n"
                            f"• *Precio BTC:* `${precio_actual:,.2f}`\n"
                            f"• *Posición:* {pos_str}"
                        )
        except Exception:
            time.sleep(5)
        time.sleep(1)

threading.Thread(target=escuchar_comandos, daemon=True).start()

# =========================================================
# 6. LÓGICA PRINCIPAL DE OPERACIONES
# =========================================================
def analizar_y_operar(simbolo='BTC/USDT'):
    global saldo_usd, btc_poseidos, precio_compra, en_posicion
    
    exchange = ccxt.binance()
    ohlcv = exchange.fetch_ohlcv(simbolo, timeframe='1h', limit=60)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    
    df['sma_20'] = df['close'].rolling(20).mean()
    df['rsi'] = calcular_rsi(df, periodo=14)
    
    precio_actual = df['close'].iloc[-1]
    media_actual = df['sma_20'].iloc[-1]
    rsi_actual = df['rsi'].iloc[-1]
    
    # --- EVALUACIÓN DE VENTA ---
    if en_posicion:
        porcentaje_variacion = ((precio_actual - precio_compra) / precio_compra) * 100
        saldo_obtenido = btc_poseidos * precio_actual
        ganancia_usd = saldo_obtenido - (btc_poseidos * precio_compra)

        # 1. Take Profit (+4%)
        if porcentaje_variacion >= TAKE_PROFIT_PCT:
            saldo_usd = saldo_obtenido
            btc_poseidos = 0.0
            en_posicion = False
            guardar_estado()
            
            enviar_telegram(
                f"🎯 *TAKE PROFIT ALCANZADO (+{TAKE_PROFIT_PCT}%)*\n\n"
                f"• *Precio Venta:* `${precio_actual:,.2f}`\n"
                f"• *Ganancia:* `+${ganancia_usd:,.2f} USD` (+{porcentaje_variacion:.2f}%)\n\n"
                f"🏆 *NUEVO SALDO:* `${saldo_usd:,.2f} USDT`"
            )

        # 2. Stop Loss (-2%)
        elif porcentaje_variacion <= -STOP_LOSS_PCT:
            saldo_usd = saldo_obtenido
            btc_poseidos = 0.0
            en_posicion = False
            guardar_estado()
            
            enviar_telegram(
                f"🛑 *STOP LOSS DISPARADO (-{STOP_LOSS_PCT}%)*\n\n"
                f"• *Precio Venta:* `${precio_actual:,.2f}`\n"
                f"• *Pérdida:* `${ganancia_usd:,.2f} USD` ({porcentaje_variacion:.2f}%)\n\n"
                f"💰 *NUEVO SALDO:* `${saldo_usd:,.2f} USDT`"
            )

        # 3. Venta por Cruce de Media (Cierre de tendencia)
        elif precio_actual < media_actual:
            saldo_usd = saldo_obtenido
            btc_poseidos = 0.0
            en_posicion = False
            guardar_estado()
            
            emoji = "🚀" if ganancia_usd >= 0 else "📉"
            enviar_telegram(
                f"🔴 *VENTA POR CRUCE TÉCNICO (SMA 20)*\n\n"
                f"• *Precio Venta:* `${precio_actual:,.2f}`\n"
                f"• *Resultado:* {emoji} `${ganancia_usd:+,.2f} USD` ({porcentaje_variacion:+.2f}%)\n\n"
                f"💰 *NUEVO SALDO:* `${saldo_usd:,.2f} USDT`"
            )

    # --- EVALUACIÓN DE COMPRA ---
    else:
        # Señal válida de compra
        if precio_actual > media_actual and rsi_actual < RSI_MAX_COMPRA:
            btc_poseidos = saldo_usd / precio_actual
            precio_compra = precio_actual
            en_posicion = True
            guardar_estado()
            
            precio_stop = precio_compra * (1 - STOP_LOSS_PCT/100)
            precio_tp = precio_compra * (1 + TAKE_PROFIT_PCT/100)
            
            enviar_telegram(
                f"🟢 *COMPRA SIMULADA EJECUTADA*\n\n"
                f"• *Precio Entrada:* `${precio_actual:,.2f}`\n"
                f"• *RSI:* `{rsi_actual:.1f}` (< {RSI_MAX_COMPRA})\n"
                f"• *Invertido:* `${saldo_usd:,.2f} USDT`\n\n"
                f"🎯 *Take Profit Target:* `${precio_tp:,.2f}`\n"
                f"🛡️ *Stop Loss Target:* `${precio_stop:,.2f}`"
            )

    print(f"[{pd.Timestamp.now().strftime('%H:%M:%S')}] Análisis completado | BTC: ${precio_actual:,.2f} | RSI: {rsi_actual:.1f}")

# =========================================================
# 7. EJECUCIÓN PRINCIPAL
# =========================================================
if __name__ == '__main__':
    cargar_estado()
    
    enviar_telegram(
        f"🚀 *BOT REINICIADO Y CORREGIDO EN RENDER*\n\n"
        f"• *Estrategia:* SMA 20 + Filtro RSI (<{RSI_MAX_COMPRA})\n"
        f"• *Gestión de riesgo:* TP +{TAKE_PROFIT_PCT}% | SL -{STOP_LOSS_PCT}%\n\n"
        f"💡 _Puedes enviarme `/saldo` o `/estado` por aquí cuando quieras._"
    )
    
    while True:
        try:
            analizar_y_operar('BTC/USDT')
            time.sleep(900)  # Revisa el mercado cada 15 minutos
        except Exception as e:
            print(f"[ERROR BUCLE]: {e}")
            time.sleep(30)