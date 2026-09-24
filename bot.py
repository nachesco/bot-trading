import os
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import ccxt
import pandas as pd
import requests

# =========================================================
# CONFIGURACIÓN Y CREDENCIALES DE TELEGRAM
# =========================================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8628860776:AAEQHlVjzM1fXFhBPuTPjrhzyoDKBamaKII")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "402919772")

def enviar_telegram(mensaje: str):
    """Envía una notificación en formato HTML a tu cuenta de Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensaje,
        "parse_mode": "HTML"
    }
    try:
        respuesta = requests.post(url, json=payload, timeout=10)
        if respuesta.status_code != 200:
            print(f"[ERROR TELEGRAM]: Respuesta de API {respuesta.status_code} - {respuesta.text}")
    except Exception as e:
        print(f"[ERROR TELEGRAM]: Error de conexión: {e}")

# =========================================================
# 1. SERVIDOR HTTP PARA EL PLAN GRATUITO EN RENDER
# =========================================================
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Responde a las peticiones del verificador de estado de Render."""
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write("🤖 Bot de Trading activo 24/7 en Render!".encode('utf-8'))

    def log_message(self, format, *args):
        """Silencia las peticiones HTTP en consola para mantener los logs limpios."""
        return

def run_dummy_server():
    """Abre el puerto asignado por Render para mantener el Web Service activo."""
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), DummyHandler)
    print(f"[SERVIDOR] Servidor HTTP de Render listo en el puerto {port}")
    server.serve_forever()

# =========================================================
# 2. LÓGICA DE ANÁLISIS DE MERCADO
# =========================================================
# Conexión pública a Binance con control de frecuencia de peticiones
exchange = ccxt.binance({
    'enableRateLimit': True
})

ultima_tendencia = None

def analizar_mercado():
    global ultima_tendencia
    simbolo = 'BTC/USDT'
    
    # Descargar las últimas 30 velas de 1 hora
    ohlcv = exchange.fetch_ohlcv(simbolo, timeframe='1h', limit=30)
    df = pd.DataFrame(ohlcv, columns=['tiempo', 'open', 'high', 'low', 'close', 'volume'])
    df['tiempo'] = pd.to_datetime(df['tiempo'], unit='ms')
    
    precio_actual = df['close'].iloc[-1]
    
    # Cálculo de medias móviles (Rápida 5 horas, Lenta 20 horas)
    df['sma_rapida'] = df['close'].rolling(5).mean()
    df['sma_lenta'] = df['close'].rolling(20).mean()
    
    sma_rapida_val = df['sma_rapida'].iloc[-1]
    sma_lenta_val = df['sma_lenta'].iloc[-1]
    
    # Determinamos la tendencia actual
    tendencia_actual = "ALCISTA 📈" if sma_rapida_val > sma_lenta_val else "BAJISTA / LATERAL 📉"
    
    fecha_hora = pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{fecha_hora}] {simbolo} | Precio: ${precio_actual:,.2f} | SMA5: ${sma_rapida_val:,.2f} | SMA20: ${sma_lenta_val:,.2f}")

    # Preparamos el informe
    mensaje = (
        f"📊 <b>Informe del Bot de Trading</b>\n\n"
        f"• <b>Activo:</b> {simbolo}\n"
        f"• <b>Precio actual:</b> ${precio_actual:,.2f}\n"
        f"• <b>Media Rápida (5h):</b> ${sma_rapida_val:,.2f}\n"
        f"• <b>Media Lenta (20h):</b> ${sma_lenta_val:,.2f}\n"
        f"• <b>Estado:</b> {tendencia_actual}\n\n"
        f"🤖 <i>Servidor activo 24/7 en Render ({fecha_hora})</i>"
    )
    
    enviar_telegram(mensaje)

# =========================================================
# 3. BUCLE PRINCIPAL DE EJECUCIÓN 24/7
# =========================================================
if __name__ == '__main__':
    print("=== INICIANDO BOT DE TRADING CON CONEXIÓN A TELEGRAM Y RENDER ===")
    
    # 1. Iniciar el servidor web de Render en segundo plano
    server_thread = threading.Thread(target=run_dummy_server, daemon=True)
    server_thread.start()

    # 2. Enviar mensaje de bienvenida al encender el bot
    enviar_telegram("🚀 <b>¡Bot encendido correctamente en Render!</b>\nTu servidor está listo y enviará informes periódicos cada 15 minutos.")
    
    # 3. Bucle automático de análisis cada 15 minutos
    while True:
        try:
            analizar_mercado()
            time.sleep(900)  # Revisa cada 15 minutos (900 segundos)
        except Exception as e:
            print(f"[ERROR]: Ocurrió un fallo en la ejecución: {e}")
            time.sleep(60)  # Reintenta en 1 minuto en caso de error