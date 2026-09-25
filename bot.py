import os
import time
import logging
import threading
from flask import Flask
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
# FUNCIONES AUXILIARES DE PRECIOS (SIN BLOQUEOS EN RENDER)
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

def obtener_precio_btc():
    """Obtiene el precio de Bitcoin usando Coinbase (permite IPs de servidores cloud)."""
    try:
        # API pública de Coinbase Pro / Exchange
        url = "https://api.exchange.coinbase.com/products/BTC-USD/ticker"
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=5)
        if response.status_code == 200:
            data = response.json()
            return {
                "precio": float(data['price']),
                "fuente": "Coinbase"
            }
    except Exception as e:
        logger.warning(f"Fallo Coinbase API ({e}), usando respaldo CoinGecko...")

    try:
        # Respaldo con CoinGecko
        url_cg = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"
        response = requests.get(url_cg, timeout=5)
        if response.status_code == 200:
            data = response.json().get('bitcoin', {})
            return {
                "precio": float(data.get('usd', 0)),
                "fuente": "CoinGecko"
            }
    except Exception as e:
        logger.error(f"Error en CoinGecko: {e}")

    return None

def generar_informe_estado():
    """Genera un informe del mercado actual."""
    datos = obtener_precio_btc()
    if datos is None:
        return "❌ Error al conectar con los servidores de mercado."
    
    precio = datos["precio"]
    fuente = datos["fuente"]

    mensaje = (
        f"📊 **Estado del Mercado - BTC/USD**\n\n"
        f"• **Precio Actual:** ${precio:,.2f}\n"
        f"• **Fuente de datos:** {fuente}\n\n"
        f"🟢 *Servidor Render respondiendo correctamente sin bloqueos.*"
    )
    return mensaje

# ---------------------------------------------------------------------------
# BUCLE DE COMANDOS DE TELEGRAM
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
                    
                    if chat_id == TELEGRAM_CHAT_ID:
                        if text in ['/start', '/help']:
                            msg = (
                                "🤖 **Bot de Trading Activo**\n\n"
                                "Comandos disponibles:\n"
                                "• `/estado` - Estado del mercado\n"
                                "• `/saldo` - Resumen de saldo\n"
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
                            datos = obtener_precio_btc()
                            if datos:
                                enviar_mensaje_telegram(f"🪙 **BTC/USD:** ${datos['precio']:,.2f}")
                            else:
                                enviar_mensaje_telegram("❌ Error al consultar precio.")
                                
        except Exception as e:
            logger.error(f"Error en bucle de Telegram: {e}")
            time.sleep(5)
            
        time.sleep(1)

# ---------------------------------------------------------------------------
# PUNTO DE ENTRADA PRINCIPAL
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    enviar_mensaje_telegram("🚀 **¡Bot migrado a Coinbase!** Cero bloqueos geográficos.")
    procesar_actualizaciones_telegram()