import ccxt
import pandas as pd
import requests
import time

# --- CONFIGURACIÓN DE TELEGRAM ---
TOKEN = "8628860776:AAFcmxMmxmdVmPy8EAWC--iP0mXCtEG2MLk"
CHAT_ID = "402919772"

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

def analizar_mercado(simbolo='BTC/USDT'):
    """Obtiene datos de Binance, calcula indicadores y envía señal."""
    exchange = ccxt.binance()
    
    # Obtener las últimas 50 velas de 1 hora
    ohlcv = exchange.fetch_ohlcv(simbolo, timeframe='1h', limit=50)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    
    # Calcular Media Móvil Sencilla (SMA 20)
    df['sma_20'] = df['close'].rolling(20).mean()
    
    precio_actual = df['close'].iloc[-1]
    media_actual = df['sma_20'].iloc[-1]
    
    # Lógica básica de señal
    if precio_actual > media_actual:
        estado = "🟢 **TENDENCIA ALCISTA** (Precio por encima de la media)"
        sugerencia = "Momento favorable o de mantención."
    else:
        estado = "🔴 **TENDENCIA BAJISTA** (Precio por debajo de la media)"
        sugerencia = "Precaución o posible zona de venta/espera."

    # Estructura del mensaje para Telegram
    mensaje = (
        f"📊 **INFORME DE MERCADO - {simbolo}**\n\n"
        f"• **Precio Actual:** `${precio_actual:,.2f}`\n"
        f"• **Media (20h):** `${media_actual:,.2f}`\n\n"
        f"• **Estado:** {estado}\n"
        f"💡 *Sugerencia:* {sugerencia}\n\n"
        f"🤖 _Monitoreando 24/7 en Render_"
    )
    
    enviar_telegram(mensaje)
    print(f"[{pd.Timestamp.now()}] Análisis enviado a Telegram. Precio: ${precio_actual}")

if __name__ == '__main__':
    # Mensaje de confirmación de arranque
    enviar_telegram("🚀 **Bot de Trading Activado**\nIniciando análisis periódico del mercado...")
    
    while True:
        try:
            analizar_mercado('BTC/USDT')
            # Espera 15 minutos (900 segundos) entre análisis
            time.sleep(900)
        except Exception as e:
            print(f"Error durante la ejecución: {e}")
            time.sleep(60)
