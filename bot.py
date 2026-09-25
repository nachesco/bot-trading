import ccxt
import pandas as pd
import requests
import time

# Tus credenciales de Telegram
TELEGRAM_TOKEN = '8628860776:AAE5I29ZKaNQHdxbNwynFXfWiB3HoN9XtAo'
TELEGRAM_CHAT_ID = '402919772'

def enviar_telegram(mensaje: str):
    """Envía un mensaje directo a tu Telegram sin intermediarios."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensaje,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error al enviar mensaje: {e}")

# Conexión con Binance
exchange = ccxt.binance()

def analizar_mercado():
    """Analiza el precio de Bitcoin y envía el informe."""
    ohlcv = exchange.fetch_ohlcv('BTC/USDT', timeframe='1h', limit=30)
    df = pd.DataFrame(ohlcv, columns=['tiempo', 'open', 'high', 'low', 'close', 'volume'])
    
    precio_actual = df['close'].iloc[-1]
    df['sma_10'] = df['close'].rolling(10).mean()
    sma_actual = df['sma_10'].iloc[-1]

    mensaje = (
        f"📊 **Informe de Trading**\n\n"
        f"• **BTC/USDT:** ${precio_actual:,.2f}\n"
        f"• **Media (10h):** ${sma_actual:,.2f}\n\n"
        f"🤖 *Analizando mercado 24/7 en Render.*"
    )
    
    enviar_telegram(mensaje)

if __name__ == '__main__':
    enviar_telegram("🚀 **¡Bot activado y limpio!** Ya no hay spam ni publicidad.")
    
    while True:
        try:
            analizar_mercado()
            time.sleep(900)  # Cada 15 minutos
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(60)