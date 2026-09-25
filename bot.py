import os
import time
import logging
import threading
from flask import Flask
import ccxt
import pandas as pd
import requests

# ---------------------------------------------------------------------------
# CONFIGURACIÓN Y CREDENCIALES
# ---------------------------------------------------------------------------
TELEGRAM_TOKEN = '8628860776:AAE5I29ZKaNQHdxbNwynFXfWiB3HoN9XtAo'
TELEGRAM_CHAT_ID = '402919772'

# Configuración de logs en consola
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conexión a Binance mediante CCXT
exchange = ccxt.binance({
    'enableRateLimit': True,
})

# ---------------------------------------------------------------------------
# SERVIDOR HTTP (FLASK) PARA MANTENER RENDER ACTIVO 24/7
# ---------------------------------------------------------------------------
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot de Trading activo y ejecutándose en Render 24/7.", 200

@app.route('/health')
def health():
    return "OK", 200

def run_flask():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

# ---------------------------------------------------------------------------
# FUNCIONES AUXILIARES DE TRADING Y NOTIFICACIONES
# ---------------------------------------------------------------------------
def enviar_mensaje_telegram(texto: str):
    """Envía un mensaje directo a Telegram mediante la API de Bot."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": texto,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        logger.error(f"Error enviando mensaje a Telegram: {e}")

def obtener_datos_mercado(symbol='BTC/USDT', timeframe='1h', limit=50):
    """Obtiene datos de velas de Binance y calcula indicadores básicos."""
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        
        # Indicadores técnicos sencillos: Medias Móviles (SMA 10 y SMA 30)
        df['sma_10'] = df['close'].rolling(window=10).mean()
        df['sma_30'] = df['close'].rolling(window=30).mean()
        
        return df
    except Exception as e:
        logger.error(f"Error obteniendo datos del mercado: {e}")
        return None

def generar_informe_estado():
    """Genera un informe completo del mercado actual."""
    df = obtener_datos_mercado('BTC/USDT', '1h', 50)
    if df is None or df.empty:
        return "❌ Error al conectar con el mercado."
    
    precio_actual = df['close'].iloc[-1]
    sma_10 = df['sma_10'].iloc[-1]
    sma_30 = df['sma_30'].iloc[-1]
    tendencia = "🚀 Alcista" if sma_10 > sma_30 else "📉 Bajista / Lateral"

    mensaje = (
        f"📊 **Estado del Bot de Trading**\n\n"
        f"• **Par:** BTC/USDT\n"
        f"• **Precio Actual:** ${precio_actual:,.2f}\n"
        f"• **SMA (10h):** ${sma_10:,.2f}\n"
        f"• **SMA (30h):** ${sma_30:,.2f}\n"
        f"• **Tendencia:** {tendencia}\n\n"
        f"🟢 *Servidor en Render funcionando correctamente.*"
    )
    return mensaje

# ---------------------------------------------------------------------------
# PROCESADOR DE COMANDOS DE TELEGRAM (LONG POLLING VIA TELEGRAM API)
# ---------------------------------------------------------------------------
def procesar_actualizaciones_telegram():
    """Bucle principal para escuchar y responder comandos en Telegram."""
    last_update_id = 0
    url_get_updates = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    
    logger.info("Iniciando escucha de comandos de Telegram...")
    
    while True:
        try:
            params = {"offset": last_update_id + 1, "timeout": 30}
            response = requests.get(url_get_updates, params=params, timeout=35)
            
            if response.status_code == 200:
                data = response.json()
                for result in data.get("result", []):
                    last_update_id = result["update_id"]
                    
                    message = result.get("message", {})
                    text = message.get("text", "")
                    chat_id = str(message.get("chat", {}).get("id", ""))
                    
                    # Verificar que solo responda a tu CHAT_ID por seguridad
                    if chat_id == TELEGRAM_CHAT_ID:
                        if text in ['/start', '/help']:
                            msg = (
                                "🤖 **Bienvenido a tu Bot de Trading**\n\n"
                                "Comandos disponibles:\n"
                                "• `/estado` - Muestra el análisis técnico actual\n"
                                "• `/saldo` - Muestra el balance estimado\n"
                                "• `/btc` - Precio rápido de Bitcoin"
                            )
                            enviar_mensaje_telegram(msg)
                            
                        elif text in ['/estado', '/status']:
                            informe = generar_informe_estado()
                            enviar_mensaje_telegram(informe)
                            
                        elif text == '/saldo':
                            msg = (
                                "💰 **Resumen de Cuenta (Modo Simulación)**\n\n"
                                "• **USDT disponible:** $1,000.00\n"
                                "• **BTC invertido:** 0.00 BTC\n"
                                "• **Valor Total:** $1,000.00"
                            )
                            enviar_mensaje_telegram(msg)
                            
                        elif text == '/btc':
                            df = obtener_datos_mercado('BTC/USDT', '1m', 2)
                            if df is not None and not df.empty:
                                precio = df['close'].iloc[-1]
                                enviar_mensaje_telegram(f"🪙 **BTC/USDT:** ${precio:,.2f}")
                            else:
                                enviar_mensaje_telegram("❌ No se pudo obtener el precio.")
                                
        except Exception as e:
            logger.error(f"Error en bucle de Telegram: {e}")
            time.sleep(5)
            
        time.sleep(1)

# ---------------------------------------------------------------------------
# PUNTO DE ENTRADA PRINCIPAL
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    # 1. Iniciar el servidor Flask en un hilo secundario
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    # 2. Notificar por Telegram que el bot ha arrancado
    enviar_mensaje_telegram("🚀 **¡Bot de Trading desplegado y activo en Render!**\nEnvía `/estado` para comprobar el mercado.")
    
    # 3. Iniciar la escucha de comandos en el hilo principal
    procesar_actualizaciones_telegram()