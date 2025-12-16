# main_controller.py
# Controle híbrido: ArUco (navegação) + Linha (segurança)

import cv2
import logging

from line_detector import detectar_limite, logica_limite_linha
from aruco_nav import calcular_pose_aruco, logica_planejamento_corte
from serial_comm import inicializar_serial, enviar_comando_stm, fechar_serial

# =============================================================================
# LOGGING GLOBAL (CONTROLA TODOS OS MÓDULOS)
# =============================================================================
LOG_FILE = "controle_hibrido.log"

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

# Remove handlers antigos (evita duplicação e logs infinitos em uma mesma posição)
if root_logger.handlers:
    for h in root_logger.handlers[:]:
        root_logger.removeHandler(h)

formatter = logging.Formatter(
    "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# Handler de arquivo (TUDO vai para o arquivo)
file_handler = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
file_handler.setFormatter(formatter)
file_handler.setLevel(logging.INFO)

# Handler de console
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
console_handler.setLevel(logging.INFO)

root_logger.addHandler(file_handler)
root_logger.addHandler(console_handler)

logging.info("MAIN: sistema de controle híbrido iniciado")

# =============================================================================
# CONFIGURAÇÕES
# =============================================================================
CAMERA_INDEX = 0
ROI_Y_START = 100
ROI_Y_END = 480
ROI_X_START = 0
ROI_X_END = 640

# =============================================================================
# MAIN LOOP
# =============================================================================
def main_loop_controle():
    serial_ok = inicializar_serial()

    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        logging.critical("MAIN: erro ao abrir câmera")
        return

    logging.info("MAIN: loop de controle iniciado")

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        # --------------------------------------------------
        # LINE DETECTOR (SEGURANÇA)
        # --------------------------------------------------
        frame_roi = frame[ROI_Y_START:ROI_Y_END, ROI_X_START:ROI_X_END]
        cy_roi, frame_roi_proc = detectar_limite(frame_roi)
        comando_barreira, status_barreira, cor_barreira = logica_limite_linha(cy_roi)

        # --------------------------------------------------
        # ARUCO (NAVEGAÇÃO)
        # --------------------------------------------------
        arucos = calcular_pose_aruco(frame)
        comando_aruco = logica_planejamento_corte(arucos)

        # --------------------------------------------------
        # ARBITRAGEM DE PRIORIDADE
        # --------------------------------------------------
        comando_final = comando_aruco

        if comando_barreira == "S":
            comando_final = "S"
        elif comando_barreira == "D":
            if comando_aruco in ("L", "R"):
                comando_final = comando_aruco
            else:
                comando_final = "D"

        # --------------------------------------------------
        # SERIAL
        # --------------------------------------------------
        if serial_ok:
            enviar_comando_stm(comando_final)

        # --------------------------------------------------
        # VISUALIZAÇÃO
        # --------------------------------------------------
        frame[ROI_Y_START:ROI_Y_END, ROI_X_START:ROI_X_END] = frame_roi_proc

        cv2.putText(frame, f"ARUCO CMD: {comando_aruco}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        cv2.putText(frame, f"SEGURANCA: {status_barreira}", (10, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, cor_barreira, 2)
        cv2.putText(frame, f"FINAL: {comando_final}", (10, 90),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 3)

        cv2.imshow("Controle Híbrido", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()
    fechar_serial()
    logging.info("MAIN: sistema encerrado")

# =============================================================================
if __name__ == "__main__":
    main_loop_controle()
