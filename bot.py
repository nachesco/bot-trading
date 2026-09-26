import os
import time
import json
import logging
import threading
from flask import Flask
import ccxt
import pandas as pd
import requests

# ------------------------------------------------------------------
# CONFIGURACIÓN Y LOGS
# ------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "TU_TELEGRAM_TOKEN_AQUI")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")  # Se auto-guardará con el comando /start

ESTADO_FILE = "paper_trading.json"

# Parámetros Estrategia V5
SYMBOL = "BTC/USDT"
TIMEFRAME = "4h"
TAKE_PROFIT_PCT = 5.0
STOP_LOSS_INICIAL_PCT = 3.5
TRAILING_STOP_DIST_PCT = 2.0
ADX_MINIMO = 22.0
COMISION = 0.001  # 0.1% Binance

# ------------------------------------------------------------------
# SERVIDOR FLASK (Requerido por Render)
# ------------------------------------------------------------------
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot de Trading V5 (Paper Trading) activo y ejecutándose 24/7.", 200

# ------------------------------------------------------------------
# GESTIÓN DE ESTADO (PAPER TRADING)
# ------------------------------------------------------------------
def cargar_estado():
    if os.path.exists(ESTADO_FILE):
        try:
            with open(ESTADO_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Error cargando {ESTADO_FILE}: {e}")
    
    # Estado inicial por defecto
    return {
        "saldo_usdt": 1000.0,
        "btc_comprado": 0.0,
        "en_posicion": False,
        "precio_compra": 0.0,
        "max_precio_alcanzado": 0.0,
        "chat_id": TELEGRAM_CHAT_ID,
        "historial": []
    }

def guardar_estado(estado):
    try:
        with open(ESTADO_FILE, "w") as f:
            json.dump(estado, f, indent=4)
    except Exception as e:
        logging.error(f"Error guardando {ESTADO_FILE}: {e}")

# ------------------------------------------------------------------
# NOTIFICACIONES DE TELEGRAM
# ------------------------------------------------------------------
def enviar_telegram(mensaje, chat_id=None):
    estado = cargar_estado()
    cid = chat_id or estado.get("chat_id") or TELEGRAM_CHAT_ID
    if not cid or TELEGRAM_TOKEN == "TU_TELEGRAM_TOKEN_AQUI":
        logging.warning("Telegram no configurado adecuadamente (falta chat_id o token).")
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": cid, "text": mensaje, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        logging.error(f"Error enviando mensaje a Telegram: {e}")

# ------------------------------------------------------------------
# ANÁLISIS TÉCNICO DE MERCADO (ESTRATEGIA V5)
# ------------------------------------------------------------------
def obtener_analisis_tecnico():
    try:
        exchange = ccxt.binance()
        velas = exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=300)
        df = pd.DataFrame(velas, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        # Indicadores: EMAs y SMA 200
        df['EMA_9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['EMA_21'] = df['close'].ewm(span=21, adjust=False).mean()
        df['SMA_200'] = df['close'].rolling(window=200).mean()

        # RSI (14)
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))

        # ADX (14)
        df['prev_close'] = df['close'].shift(1)
        df['tr1'] = df['high'] - df['low']
        df['tr2'] = (df['high'] - df['prev_close']).abs()
        df['tr3'] = (df['low'] - df['prev_close']).abs()
        df['tr'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)

        df['up_move'] = df['high'] - df['high'].shift(1)
        df['down_move'] = df['low'].shift(1) - df['low']

        df['+dm'] = 0.0
        df['-dm'] = 0.0
        df.loc[(df['up_move'] > df['down_move']) & (df['up_move'] > 0), '+dm'] = df['up_move']
        df.loc[(df['down_move'] > df['up_move']) & (df['down_move'] > 0), '-dm'] = df['down_move']

        df['tr_14'] = df['tr'].ewm(alpha=1/14, adjust=False).mean()
        df['+dm_14'] = df['+dm'].ewm(alpha=1/14, adjust=False).mean()
        df['-dm_14'] = df['-dm'].ewm(alpha=1/14, adjust=False).mean()

        df['+di'] = 100 * (df['+dm_14'] / df['tr_14'])
        df['-di'] = 100 * (df['-dm_14'] / df['tr_14'])
        df['dx'] = 100 * (df['+di'] - df['-di']).abs() / (df['+di'] + df['-di'])
        df['ADX'] = df['dx'].ewm(alpha=1/14, adjust=False).mean()

        df = df.dropna()
        actual = df.iloc[-1]
        anterior = df.iloc[-2]
        return actual, anterior
    except Exception as e:
        logging.error(f"Error obteniendo datos de Binance: {e}")
        return None, None

# ------------------------------------------------------------------
# BUCLE DE TRADING AUTOMÁTICO (EVALUACIÓN CADA 5 MINUTOS)
# ------------------------------------------------------------------
def ejecutar_ciclo_trading():
    logging.info("Iniciando motor de Paper Trading V5...")
    while True:
        try:
            estado = cargar_estado()
            actual, anterior = obtener_analisis_tecnico()

            if actual is not None and anterior is not None:
                precio_actual = actual['close']
                high_actual = actual['high']
                low_actual = actual['low']
                rsi = actual['RSI']
                adx = actual['ADX']
                ema9 = actual['EMA_9']
                ema21 = actual['EMA_21']
                sma200 = actual['SMA_200']

                # 1. SI ESTAMOS EN POSICIÓN: EVALUAR SALIDAS Y TRAILING STOP
                if estado["en_posicion"]:
                    if high_actual > estado["max_precio_alcanzado"]:
                        estado["max_precio_alcanzado"] = high_actual
                        guardar_estado(estado)

                    precio_compra = estado["precio_compra"]
                    precio_tp = precio_compra * (1 + TAKE_PROFIT_PCT / 100.0)
                    precio_sl_inicial = precio_compra * (1 - STOP_LOSS_INICIAL_PCT / 100.0)
                    precio_trailing = estado["max_precio_alcanzado"] * (1 - TRAILING_STOP_DIST_PCT / 100.0)
                    stop_efectivo = max(precio_sl_inicial, precio_trailing)

                    cruce_bajista = (anterior['EMA_9'] >= anterior['EMA_21']) and (ema9 < ema21)

                    vender = False
                    motivo = ""
                    precio_salida = precio_actual

                    if high_actual >= precio_tp:
                        vender = True
                        motivo = f"Take Profit (+{TAKE_PROFIT_PCT}%)"
                        precio_salida = precio_tp
                    elif low_actual <= stop_efectivo:
                        vender = True
                        es_trailing = stop_efectivo > precio_sl_inicial
                        motivo = f"Trailing Stop (-{TRAILING_STOP_DIST_PCT}% del máximo)" if es_trailing else f"Stop Loss Inicial (-{STOP_LOSS_INICIAL_PCT}%)"
                        precio_salida = stop_efectivo
                    elif cruce_bajista:
                        vender = True
                        motivo = "Cruce Bajista EMA 9/21"
                        precio_salida = precio_actual

                    if vender:
                        capital_obtenido = estado["btc_comprado"] * precio_salida
                        saldo_final = capital_obtenido * (1 - COMISION)
                        
                        inversion_inicial = estado["precio_compra"] * estado["btc_comprado"] / (1 - COMISION)
                        pnl_pct = ((saldo_final - inversion_inicial) / inversion_inicial) * 100
                        
                        estado["saldo_usdt"] = saldo_final
                        estado["btc_comprado"] = 0.0
                        estado["en_posicion"] = False
                        
                        log_op = f"🔴 *VENTA REALIZADA (PAPER)*\nMotivo: {motivo}\nPrecio Salida: ${precio_salida:,.2f}\nPNL Operación: {pnl_pct:+.2f}%\nNuevo Saldo USDT: ${saldo_final:,.2f}"
                        estado["historial"].append(log_op)
                        guardar_estado(estado)
                        enviar_telegram(log_op)

                # 2. SI NO ESTAMOS EN POSICIÓN: EVALUAR COMPRA
                else:
                    cruce_alcista = (anterior['EMA_9'] <= anterior['EMA_21']) and (ema9 > ema21)
                    tendencia_alcista = precio_actual > sma200

                    if cruce_alcista and rsi < 65 and tendencia_alcista and adx > ADX_MINIMO:
                        capital_disponible = estado["saldo_usdt"] * (1 - COMISION)
                        btc_comprados = capital_disponible / precio_actual
                        
                        estado["btc_comprado"] = btc_comprados
                        estado["precio_compra"] = precio_actual
                        estado["max_precio_alcanzado"] = high_actual
                        estado["saldo_usdt"] = 0.0
                        estado["en_posicion"] = True
                        
                        log_op = f"🟢 *COMPRA REALIZADA (PAPER)*\nPar: {SYMBOL}\nPrecio Entrado: ${precio_actual:,.2f}\nCantidad BTC: {btc_comprados:.6f}\nADX Actual: {adx:.1f}\nRSI: {rsi:.1f}"
                        estado["historial"].append(log_op)
                        guardar_estado(estado)
                        enviar_telegram(log_op)

        except Exception as e:
            logging.error(f"Error en ciclo de trading: {e}")

        # Esperar 5 minutos entre chequeos
        time.sleep(300)

# ------------------------------------------------------------------
# ESCUCHA DE COMANDOS EN TELEGRAM (POLLING)
# ------------------------------------------------------------------
def procesar_comandos_telegram():
    if TELEGRAM_TOKEN == "TU_TELEGRAM_TOKEN_AQUI":
        return

    offset = None
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"

    while True:
        try:
            params = {"timeout": 20, "offset": offset}
            res = requests.get(url, params=params, timeout=25).json()
            
            if "result" in res:
                for update in res["result"]:
                    offset = update["update_id"] + 1
                    message = update.get("message", {})
                    text = message.get("text", "")
                    chat_id = message.get("chat", {}).get("id")

                    if not chat_id or not text:
                        continue

                    # Guardar chat_id automático
                    estado = cargar_estado()
                    if estado.get("chat_id") != chat_id:
                        estado["chat_id"] = chat_id
                        guardar_estado(estado)

                    # Comandos
                    if text == "/start" or text == "/ayuda":
                        msg = ("🤖 *Trading Bot V5 Activo*\n\n"
                               "Comandos disponibles:\n"
                               "➡️ /estado - Ver indicadores de BTC y posición actual\n"
                               "➡️ /saldo - Ver tu saldo en USDT, BTC y rentabilidad\n"
                               "➡️ /reset - Reiniciar saldo simulado a $1,000 USDT")
                        enviar_telegram(msg, chat_id)

                    elif text == "/saldo":
                        actual, _ = obtener_analisis_tecnico()
                        precio_actual = actual['close'] if actual is not None else 0.0
                        
                        if estado["en_posicion"]:
                            valor_btc = estado["btc_comprado"] * precio_actual
                            pnl = ((valor_btc - (estado["precio_compra"] * estado["btc_comprado"])) / (estado["precio_compra"] * estado["btc_comprado"])) * 100
                            msg = f"💰 *SALDO PAPER TRADING*\n\nPosición: COMPRADO 🟢\nBTC Poseído: {estado['btc_comprado']:.6f}\nPrecio Compra: ${estado['precio_compra']:,.2f}\nValor Actual: ${valor_btc:,.2f}\nProfit Flotante: {pnl:+.2f}%"
                        else:
                            rentabilidad = ((estado['saldo_usdt'] - 1000.0) / 1000.0) * 100
                            msg = f"💰 *SALDO PAPER TRADING*\n\nSaldo Disponible: ${estado['saldo_usdt']:,.2f} USDT\nProfit Acumulado: {rentabilidad:+.2f}%"
                        
                        enviar_telegram(msg, chat_id)

                    elif text == "/estado":
                        actual, _ = obtener_analisis_tecnico()
                        if actual is not None:
                            precio = actual['close']
                            rsi = actual['RSI']
                            adx = actual['ADX']
                            pos = "COMPRADO 🟢" if estado["en_posicion"] else "EN ESPERA ⚪"
                            msg = f"📊 *ESTADO DEL MERCADO (BTC/USDT)*\n\nPrecio Actual: ${precio:,.2f}\nADX (4H): {adx:.1f} (Mín: {ADX_MINIMO})\nRSI (4H): {rsi:.1f}\nEstado Bot: {pos}"
                        else:
                            msg = "⚠️ Error consultando mercado en Binance."
                        enviar_telegram(msg, chat_id)

                    elif text == "/reset":
                        estado["saldo_usdt"] = 1000.0
                        estado["btc_comprado"] = 0.0
                        estado["en_posicion"] = False
                        estado["precio_compra"] = 0.0
                        estado["max_precio_alcanzado"] = 0.0
                        estado["historial"] = []
                        guardar_estado(estado)
                        enviar_telegram("🔄 *Paper Trading Reiniciado:* Saldo reestablecido a $1,000.00 USDT.", chat_id)

        except Exception as e:
            logging.error(f"Error escuchando Telegram: {e}")
            time.sleep(5)

# ------------------------------------------------------------------
# ARRANQUE MULTI-THREADING
# ------------------------------------------------------------------
if __name__ == "__main__":
    # Thread 1: Ciclo de trading continuo
    t_trading = threading.Thread(target=ejecutar_ciclo_trading, daemon=True)
    t_trading.start()

    # Thread 2: Escucha de comandos Telegram
    t_telegram = threading.Thread(target=procesar_comandos_telegram, daemon=True)
    t_telegram.start()

    # Thread Principal: Servidor Web Flask (para Render)
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)