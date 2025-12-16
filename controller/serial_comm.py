# serial_comm.py
import serial
import logging
import time

logger = logging.getLogger("serial")

# ====================================================================
# *** MODO DE SIMULAÇÃO ***
# MUDE ESTE VALOR PARA False QUANDO ESTIVER RODANDO NO RASPBERRY PI
SIMULATION_MODE = True
# ====================================================================

SERIAL_PORT = '/dev/ttyACM0' 
BAUD_RATE = 115200

ser = None 

def inicializar_serial():
    """Tenta inicializar a comunicação serial com simulação."""
    global ser
    
    if SIMULATION_MODE:
        logger.info("SERIAL: MODO DE SIMULAÇÃO ATIVO. Comunicação serial ignorada.")
        return True # Retorna True para não bloquear o main_controller
        
    try:
        # Se não estiver em simulação, tenta abrir a porta real (como antes)
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.1)
        time.sleep(2) 
        logger.info(f"SERIAL: Comunicação REAL inicializada em {SERIAL_PORT}.")
        return True
    except serial.SerialException as e:
        # Se falhar no modo real, registra o CRITICAL e retorna False
        logger.critical(f"SERIAL: ERRO ao abrir a porta {SERIAL_PORT}. Verifique a porta/cabo: {e}")
        return False

def enviar_comando_stm(comando: str):
    """Envia o comando de 1 caractere ('F', 'S', 'R', 'L') para o STM32."""
    if SIMULATION_MODE:
        # No modo de simulação, apenas registra o comando que seria enviado
        logger.debug(f"SERIAL SIMULADA: Comando a ser enviado -> {comando}")
        return
        
    global ser
    if ser and ser.is_open and comando:
        try:
            ser.write(comando.encode('utf-8') + b'\n') 
            logger.debug(f"SERIAL REAL: Enviado comando -> {comando}")
        except Exception as e:
            logger.error(f"SERIAL REAL: Erro ao escrever na porta serial: {e}")

def fechar_serial():
    """Fecha a conexão serial."""
    if SIMULATION_MODE:
        logger.info("SERIAL: Comunicação de simulação encerrada.")
        return
        
    global ser
    if ser and ser.is_open:
        try:
            ser.close()
            logger.info("SERIAL: Comunicação real encerrada.")
        except:
             pass