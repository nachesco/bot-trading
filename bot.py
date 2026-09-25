import ccxt
import pandas as pd
import requests
import time
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# Servidor web ligero para satisfacer a Render.com
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot de Trading activo")

def start_health_check_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
    print(f"Servidor web de salud iniciado en el puerto {port}")
    server.serve_forever()

# Iniciar el servidor web en un hilo secundario sin bloquear el bot
threading.Thread(target=start_health_check_server, daemon=True).start()

# --- CONFIGURACIÓN DE TELEGRAM ---
TOKEN = "8628860776:AAFcmxMmxmdVmPy8EAWC--iP0mXCtEG2MLk"  # Reemplaza por tu Token de @BotFather
CHAT_ID = "402919772"

# --- CONFIGURACIÓN DE RIESGO Y ESTRATEGIA ---
STOP_LOSS_PCT = 2.0     # Vende automáticamente si cae un 2%
TAKE_PROFIT_PCT = 4.0   # Vende automáticamente si sube un 4%
RSI_MAX_COMPRA = 70.0   # No compra si el RSI es mayor o igual a 70 (Sobrecomprado)

# --- ESTADO INICIAL DEL SIMULADOR ---
saldo_usd = 1000.0       # Capital inicial ficticio
btc_poseidos = 0.0      # Cantidad de Bitcoin comprados
precio_compra = 0.0     # Precio al que se realizó la última compra
en_posicion = False     # Indica si actualmente tenemos BTC o estamos en efectivo

def enviar_telegram(mensaje: str):
    """Envía un mensaje con formato Markdown a tu Telegram."""
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

def calcular_rsi(df: pd.DataFrame, periodo: int = 14) -> pd.Series:
    """Calcula el indicador RSI clásico (Wilder's RSI) usando pure pandas."""
    delta = df['close'].diff()
    gain = delta.clip(lower=0)
    loss = -1 * delta.clip(upper=0)
    
    ema_gain = gain.ewm(com=periodo - 1, adjust=False).mean()
    ema_loss = loss.ewm(com=periodo - 1, adjust=False).mean()
    
    rs = ema_gain / ema_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def analizar_y_operar(simbolo='BTC/USDT'):
    global saldo_usd, btc_poseidos, precio_compra, en_posicion
    
    exchange = ccxt.binance()
    
    # 1. Obtener velas de 1 hora (60 limite para tener margen de calculo de RSI)
    ohlcv = exchange.fetch_ohlcv(simbolo, timeframe='1h', limit=60)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    
    # 2. Calcular indicadores
    df['sma_20'] = df['close'].rolling(20).mean()
    df['rsi'] = calcular_rsi(df, periodo=14)
    
    precio_actual = df['close'].iloc[-1]
    media_actual = df['sma_20'].iloc[-1]
    rsi_actual = df['rsi'].iloc[-1]
    
    # Diagnóstico visual del RSI
    if rsi_actual >= 70:
        estado_rsi = f"🔴 {rsi_actual:.1f} (Sobrecomprado)"
    elif rsi_actual <= 30:
        estado_rsi = f"🟢 {rsi_actual:.1f} (Sobrevendido)"
    else:
        estado_rsi = f"🟡 {rsi_actual:.1f} (Neutral)"

    # 3. Lógica con Gestión de Riesgo e Indicador RSI

    if en_posicion:
        porcentaje_variacion = ((precio_actual - precio_compra) / precio_compra) * 100
        saldo_obtenido = btc_poseidos * precio_actual
        ganancia_usd = saldo_obtenido - (btc_poseidos * precio_compra)

        # CASO A1: TAKE PROFIT (+4%)
        if porcentaje_variacion >= TAKE_PROFIT_PCT:
            saldo_usd = saldo_obtenido
            btc_poseidos = 0.0
            en_posicion = False
            
            mensaje = (
                f"🎯 **TAKE PROFIT ALCANZADO (+{TAKE_PROFIT_PCT}%)**\n\n"
                f"• **Precio Venta:** `${precio_actual:,.2f}`\n"
                f"• **Ganancia:** `+${ganancia_usd:,.2f} USD` (+{porcentaje_variacion:.2f}%)\n\n"
                f"🏆 **NUEVO SALDO:** `${saldo_usd:,.2f} USDT`"
            )
            enviar_telegram(mensaje)

        # CASO A2: STOP LOSS (-2%)
        elif porcentaje_variacion <= -STOP_LOSS_PCT:
            saldo_usd = saldo_obtenido
            btc_poseidos = 0.0
            en_posicion = False
            
            mensaje = (
                f"🛑 **STOP LOSS DISPARADO (-{STOP_LOSS_PCT}%)**\n\n"
                f"• **Precio Venta:** `${precio_actual:,.2f}`\n"
                f"• **Pérdida:** `${ganancia_usd:,.2f} USD` ({porcentaje_variacion:.2f}%)\n\n"
                f"💰 **NUEVO SALDO:** `${saldo_usd:,.2f} USDT`"
            )
            enviar_telegram(mensaje)

        # CASO A3: VENTA POR CRUCE DE MEDIA
        elif precio_actual < media_actual:
            saldo_usd = saldo_obtenido
            btc_poseidos = 0.0
            en_posicion = False
            
            emoji = "🚀" if ganancia_usd >= 0 else "📉"
            
            mensaje = (
                f"🔴 **VENTA POR ESTRATEGIA (SMA 20)**\n\n"
                f"• **Precio Venta:** `${precio_actual:,.2f}`\n"
                f"• **Resultado:** {emoji} `${ganancia_usd:+,.2f} USD` ({porcentaje_variacion:+.2f}%)\n\n"
                f"💰 **NUEVO SALDO:** `${saldo_usd:,.2f} USDT`"
            )
            enviar_telegram(mensaje)

        # CASO A4: MANTENER POSICIÓN
        else:
            precio_stop = precio_compra * (1 - STOP_LOSS_PCT/100)
            precio_tp = precio_compra * (1 + TAKE_PROFIT_PCT/100)
            
            mensaje = (
                f"📦 **POSICIÓN ACTIVA EN CURSO**\n\n"
                f"• **Precio Actual:** `${precio_actual:,.2f}` | **RSI:** {estado_rsi}\n"
                f"• **Target Take Profit:** `${precio_tp:,.2f}` (+{TAKE_PROFIT_PCT}%)\n"
                f"• **Límite Stop Loss:** `${precio_stop:,.2f}` (-{STOP_LOSS_PCT}%)\n"
                f"• **PnL Flotante:** `${ganancia_usd:+,.2f} USD` ({porcentaje_variacion:+.2f}%)\n\n"
                f"💼 **Valor Total Cartera:** `${saldo_obtenido:,.2f} USDT`"
            )
            enviar_telegram(mensaje)

    else:
        # CASO B: SEÑAL DE COMPRA FILTRADA POR RSI
        if precio_actual > media_actual and rsi_actual < RSI_MAX_COMPRA:
            btc_poseidos = saldo_usd / precio_actual
            precio_compra = precio_actual
            en_posicion = True
            precio_stop = precio_compra * (1 - STOP_LOSS_PCT/100)
            precio_tp = precio_compra * (1 + TAKE_PROFIT_PCT/100)
            
            mensaje = (
                f"🟢 **COMPRA SIMULADA EJECUTADA**\n\n"
                f"• **Precio Entrada:** `${precio_actual:,.2f}`\n"
                f"• **Filtro RSI:** `{rsi_actual:.1f}` (< {RSI_MAX_COMPRA} ✅)\n"
                f"• **Capital Invertido:** `${saldo_usd:,.2f} USDT`\n\n"
                f"🎯 **Take Profit:** `${precio_tp:,.2f}` (+{TAKE_PROFIT_PCT}%)\n"
                f"🛡️ **Stop Loss:** `${precio_stop:,.2f}` (-{STOP_LOSS_PCT}%)\n\n"
                f"💡 *Filtro aprobado:* Tendencia alcista y RSI sin sobrecompra."
            )
            enviar_telegram(mensaje)

        # CASO B2: PRECIO ALTO PERO RSI SOBRECOMPRADO (COMPRA BLOQUEADA)
        elif precio_actual > media_actual and rsi_actual >= RSI_MAX_COMPRA:
            mensaje = (
                f"⚠️ **SEÑAL DE COMPRA BLOQUEADA POR RSI**\n\n"
                f"• **Precio BTC:** `${precio_actual:,.2f}` (Por encima de SMA 20)\n"
                f"• **RSI Actual:** {estado_rsi}\n\n"
                f"🛡️ *Filtro de Seguridad:* Se evita la compra porque el mercado está demasiado sobrecomprado."
            )
            enviar_telegram(mensaje)

        # CASO C: EN ESPERA
        else:
            mensaje = (
                f"💤 **EN ESPERA (LIQUIDEZ EN USDT)**\n\n"
                f"• **Precio BTC:** `${precio_actual:,.2f}` | **SMA 20:** `${media_actual:,.2f}`\n"
                f"• **RSI (14):** {estado_rsi}\n\n"
                f"💼 **Saldo Disponible:** `${saldo_usd:,.2f} USDT`"
            )
            enviar_telegram(mensaje)

    print(f"[{pd.Timestamp.now()}] Análisis completado. BTC: ${precio_actual} | RSI: {rsi_actual:.1f}")

if __name__ == '__main__':
    enviar_telegram(
        f"📊 **Bot con Filtro RSI Activado**\n"
        f"• Estrategia: SMA 20 + RSI (<{RSI_MAX_COMPRA})\n"
        f"• Take Profit: +{TAKE_PROFIT_PCT}%\n"
        f"• Stop Loss: -{STOP_LOSS_PCT}%"
    )
    
    while True:
        try:
            analizar_y_operar('BTC/USDT')
            time.sleep(900)
        except Exception as e:
            print(f"Error durante la ejecución: {e}")
            time.sleep(60)