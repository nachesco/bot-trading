import ccxt
import pandas as pd
import requests
import time

# Tus credenciales reales de Telegram
TELEGRAM_TOKEN = '8628860776:AAEQHlVjzM1fXFhBPuTPjrhzyoDKBamaKII'
TELEGRAM_CHAT_ID = '402919772'

def enviar_telegram(mensaje: str):
    """Envía un mensaje a tu cuenta de Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensaje,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error al enviar mensaje a Telegram: {e}")

# Conexión pública a Binance
exchange = ccxt.binance()

def analizar_mercado():
    """Descarga el precio actual de Bitcoin y envía un informe a Telegram."""
    # Descargar las últimas 30 velas de 1 hora
    ohlcv = exchange.fetch_ohlcv('BTC/USDT', timeframe='1h', limit=30)
    df = pd.DataFrame(ohlcv, columns=['tiempo', 'open', 'high', 'low', 'close', 'volume'])
    
    precio_actual = df['close'].iloc[-1]
    
    # Calculamos una media móvil sencilla de las últimas 10 horas
    df['sma_10'] = df['close'].rolling(10).mean()
    sma_actual = df['sma_10'].iloc[-1]

    # Preparamos el mensaje para tu móvil
    mensaje = (
        f"📊 **Informe del Bot de Trading**\n\n"
        f"• **Activo:** BTC/USDT\n"
        f"• **Precio actual:** ${precio_actual:,.2f}\n"
        f"• **Media (10h):** ${sma_actual:,.2f}\n\n"
        f"🤖 *El servidor en Render sigue analizando el mercado 24/7.*"
    )
    
    print(f"[{pd.Timestamp.now()}] Análisis realizado. Precio BTC: ${precio_actual}")
    enviar_telegram(mensaje)

if __name__ == '__main__':
    # Mensaje instantáneo de prueba al arrancar el servidor
    enviar_telegram("🤖 **¡Bot encendido correctamente en Render!**\nTu servidor está listo y enviará informes periódicos.")
    
    # Bucle automático: analiza el mercado cada 15 minutos
    while True:
        try:
            analizar_mercado()
            # Espera 15 minutos (900 segundos) antes de volver a consultar
            time.sleep(900)
        except Exception as e:
            print(f"Error en la ejecución: {e}")
            time.sleep(60)