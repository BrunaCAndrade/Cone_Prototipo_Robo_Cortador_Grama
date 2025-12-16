import os
import subprocess
import logging
import zipfile
import time
import asyncio
from glob import glob
from datetime import datetime

# Comunicacao Serial
import threading
from collections import deque
import serial

# servidor HTTP + rotas
from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, Response
from fastapi.responses import StreamingResponse
import queue
from fastapi.templating import Jinja2Templates

# --- Configurações de diretórios ---
BASE_DIR = "/home/cone/cone_interface"
REC_DIR = os.path.join(BASE_DIR, "recordings")
LOG_DIR = os.path.join(BASE_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "system.log")

# Garantir que pastas existam para evitar erro
os.makedirs(REC_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# Sistema de log para debug em arquivo
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
)
logger = logging.getLogger("CONE")

# Cria a aplicação FastAPI
app = FastAPI(title="Cone Backend")

# Jinja2 para renderizar HTML (index.html) com lista de arquivos
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "app/templates"))# ajustar diretorio

# --- Estado do Sistema ---
class CameraManager:
    """
    Abstrai o controle da câmera via subprocess.
    Mantém estado (idle / recording / photo_sequence) e o handle do processo.
    """
    def __init__(self):
        self.process = None              # subprocess.Popen do rpicam-vid (quando gravando)
        self.mode = "idle"               # estado atual
        self.current_filename = None     # base do nome do arquivo atual (sem extensão)

    def set_mode(self, new_mode: str):
        # Apenas troca o estado e registra no log
        logger.info(f"Câmera mudou de estado: {self.mode} → {new_mode}")
        self.mode = new_mode

    def stop_process(self):
        """
        Para o processo de gravação se existir.
        - terminate() tenta encerrar de forma “limpa”
        - wait(timeout) espera até 2s
        - kill() força se travar
        """
        if self.process:
            if self.process.poll() is None:  # None => processo ainda rodando
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
            self.process = None

        # Sempre volta para idle e limpa nome atual
        self.set_mode("idle")
        self.current_filename = None

    def start_recording(self, filename_base):
        """
        Inicia gravação contínua com rpicam-vid.
        - Só permite se estiver idle
        - Usa "-t 0" => grava “indefinidamente” até você parar o processo
        """
        if self.mode != "idle":
            raise Exception("Câmera ocupada!")

        h264_path = os.path.join(REC_DIR, f"{filename_base}.h264")
        logger.info(f"Iniciando gravação: {h264_path}")

        cmd = [
            "rpicam-vid",
            "-t", "0",                      # 0 ms => roda sem timeout (até parar)
            "--width", "1296", "--height", "972",
            "--framerate", "30",
            "-o", h264_path,                # saída do vídeo (H.264 bruto)
            "--nopreview"
        ]

        # Popen: inicia processo e retorna imediatamente (não bloqueia)
        self.process = subprocess.Popen(cmd)
        self.current_filename = filename_base
        self.set_mode("recording")

        return h264_path

    def stop_recording(self):
        """
        Se estiver gravando, para o processo e devolve o nome-base do arquivo gravado.
        """
        if self.mode == "recording":
            last_file = self.current_filename
            self.stop_process()
            return last_file
        return None

    def take_photo(self):
        """
        Tira uma foto única com rpicam-still.
        IMPORTANTE: subprocess.run(..., check=True) BLOQUEIA até terminar.
        """
        if self.mode != "idle":
            raise Exception("Câmera ocupada")

        filename = datetime.now().strftime("IMG_%Y%m%d_%H%M%S.jpg")
        filepath = os.path.join(REC_DIR, filename)

        logger.info(f"Capturando foto: {filepath}")

        cmd = [
            "rpicam-still",
            "-t", "50",                      # timeout (ms) antes de capturar; aqui ~50ms
            "-o", filepath,
            "--width", "1296", "--height", "972",
            "--nopreview"
        ]

        subprocess.run(cmd, check=True)      # se falhar, levanta CalledProcessError
        logger.info(f"Foto salva: {filepath}")
        return filename


# Instância global (única) do gerenciador
cam = CameraManager()

SERIAL_PORT = os.environ.get("STM_PORT", "usb-FTDI_FT232R_USB_UART_A9YD53RF-if00-port0")#comando citado  nas configuracoes gerais
SERIAL_BAUD = int(os.environ.get("STM_BAUD", "115200"))

class StmSerialBridge:
    def __init__(self, port: str, baud: int):
        self.port = port
        self.baud = baud
        self.ser = None
        self.lock = threading.Lock()
        self.thread = None
        self.stop_evt = threading.Event()

        self.last_status = {"en": None, "arr": None, "ccr": None, "raw": None}
        self.logs = deque(maxlen=300)  # buffer circular

        # SSE: cada cliente terá sua própria fila
        self.sse_clients = set()
        self.sse_lock = threading.Lock()

    def open(self):
        if self.ser and self.ser.is_open:
            return
        self.ser = serial.Serial(self.port, self.baud, timeout=0.2, write_timeout=0.5)
        self.stop_evt.clear()
        self.thread = threading.Thread(target=self._reader_loop, daemon=True)
        self.thread.start()

    def close(self):
        self.stop_evt.set()
        try:
            if self.ser:
                self.ser.close()
        except Exception:
            pass
        self.ser = None

    def send(self, cmd: str):
        if not self.ser or not self.ser.is_open:
            raise RuntimeError("Serial não está aberta")
        line = (cmd.strip() + "\n").encode("ascii", errors="ignore")
        with self.lock:
            self.ser.write(line)
            self.ser.flush()

    def _broadcast_sse(self, line: str):
        with self.sse_lock:
            dead = []
            for q in self.sse_clients:
                try:
                    q.put_nowait(line)
                except Exception:
                    dead.append(q)
            for q in dead:
                self.sse_clients.discard(q)

    def _push_log(self, line: str):
        self.logs.append(line)
        self._broadcast_sse(line)

        # parse simples do STAT
        if line.startswith("STAT,"):
            self.last_status["raw"] = line
            # exemplo: STAT,ms=...,en=1,arr=...,ccr=...
            parts = line.split(",")
            for p in parts:
                if p.startswith("en="):
                    self.last_status["en"] = int(p.split("=", 1)[1])
                elif p.startswith("arr="):
                    self.last_status["arr"] = int(p.split("=", 1)[1])
                elif p.startswith("ccr="):
                    self.last_status["ccr"] = int(p.split("=", 1)[1])

    def _reader_loop(self):
        while not self.stop_evt.is_set():
            try:
                raw = self.ser.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="ignore").strip()
                if line:
                    self._push_log(line)
            except Exception as e:
                self._push_log(f"LOG,ms={int(time.time()*1000)},lvl=E,msg=serial_read_error:{e}")
                time.sleep(0.5)

stm = StmSerialBridge(SERIAL_PORT, SERIAL_BAUD)

# --- Funções auxiliares ---
async def run_burst_sequence(count: int):
    """
    Sequência de fotos (burst) em background.
    - Define modo photo_sequence
    - Tira N fotos com rpicam-still
    - Espera 0.5s entre cada foto
    """
    try:
        cam.set_mode("photo_sequence")
        for i in range(count):
            filename = datetime.now().strftime(f"SEQ_{i+1}_%Y%m%d_%H%M%S.jpg")
            filepath = os.path.join(REC_DIR, filename)
            logger.info(f"Capturando foto sequência {i+1}/{count}: {filepath}")

            # Aqui você suprime stdout/stderr; bom para “não poluir”
            # Mas ruim para debugar: se falhar, você perde o motivo.
            subprocess.run(
                ["rpicam-still", "-t", "1", "-o", filepath, "--nopreview"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

            await asyncio.sleep(0.5)
    finally:
        cam.set_mode("idle")

def convert_single_h264(h264_file: str):
    """
    Converte um .h264 para .mp4 via ffmpeg, depois apaga o .h264.
    """
    try:
        mp4_file = h264_file.replace(".h264", ".mp4")
        logger.info(f"Iniciando conversão: {h264_file} → {mp4_file}")

        # -c copy: remuxa sem recodificar (rápido). Pode falhar se o stream não estiver “compatível”.
        result = subprocess.run(
            ["ffmpeg", "-y", "-framerate", "30", "-i", h264_file, "-c", "copy", mp4_file],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        if result.returncode != 0:
            logger.error(f"ffmpeg erro {result.returncode} ao converter {h264_file}")
            return

        os.remove(h264_file)
        logger.info(f"Conversão concluída: {mp4_file}")

    except Exception as e:
        logger.error(f"Erro conversão {h264_file}: {e}")

# --- Rotas principais ---
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """
    Renderiza o index.html listando arquivos mais recentes (mp4/jpg) em recordings/.
    """
    all_files = sorted(
        glob(os.path.join(REC_DIR, "*")),
        key=os.path.getmtime,
        reverse=True
    )

    # Filtra apenas mp4/jpg
    files = [
        os.path.basename(f)
        for f in all_files
        if f.endswith((".mp4", ".jpg"))
    ]

    return templates.TemplateResponse("index.html", {"request": request, "files": files})

@app.get("/api/status")
def get_status():
    """
    Retorna o estado atual da câmera (idle/recording/photo_sequence).
    """
    return {"mode": cam.mode}

@app.get("/api/record/start")
def start_record():
    """
    Inicia gravação.
    OBS: rota GET com efeito colateral (iniciar gravação) — normalmente seria POST.
    """
    try:
        filename = datetime.now().strftime("VID_%Y%m%d_%H%M%S")
        cam.start_recording(filename)
        return {"status": "recording", "file": filename}
    except Exception as e:
        raise HTTPException(status_code=409, detail=str(e))

@app.get("/api/record/stop")
def stop_record():
    """
    Para gravação se estava gravando.
    """
    filename = cam.stop_recording()
    if filename:
        return {"status": "stopped", "file": filename}
    return {"status": "ignored"}

@app.get("/api/photo/single")
def take_single_photo():
    """
    Tira foto única.
    """
    try:
        cam.take_photo()
        return {"status": "captured"}
    except Exception as e:
        raise HTTPException(status_code=409, detail=str(e))

@app.get("/api/photo/sequence")
async def take_sequence(background_tasks: BackgroundTasks):
    """
    Dispara sequência de 5 fotos em background.
    """
    if cam.mode != "idle":
        raise HTTPException(status_code=409, detail="Ocupado")

    background_tasks.add_task(run_burst_sequence, 5)
    return {"status": "started"}

# --- Conversão manual ---
@app.get("/api/convert_all")
def convert_all(background_tasks: BackgroundTasks):
    """
    Procura todos os .h264 e agenda conversão em background.
    """
    h264_files = glob(os.path.join(REC_DIR, "*.h264"))

    if not h264_files:
        return {"status": "no_files"}

    for f in h264_files:
        background_tasks.add_task(convert_single_h264, f)

    return {"status": "conversion_started", "count": len(h264_files)}

# --- Manipulação de arquivos ---
@app.get("/api/files/download/{filename}")
def download_file(filename: str):
    """
    Baixa um arquivo de recordings/.
    ALERTA: sem sanitização, pode existir risco de path traversal (ex: ../).
    """
    return FileResponse(os.path.join(REC_DIR, filename))

@app.get("/api/files/delete_all")
def delete_all():
    """
    Apaga tudo em recordings/.
    ALERTA: sem try/except e sem checar se é arquivo (pode falhar se houver subpastas).
    """
    for f in glob(os.path.join(REC_DIR, "*")):
        os.remove(f)
    return {"status": "deleted"}

@app.get("/api/files/zip")
def download_zip():
    """
    Cria um ZIP com mp4/jpg e retorna para download.
    OBS: escreve em BASE_DIR/media.zip (fixo) — chamadas concorrentes podem colidir.
    """
    zip_path = os.path.join(BASE_DIR, "media.zip")
    with zipfile.ZipFile(zip_path, "w") as zipf:
        for f in glob(os.path.join(REC_DIR, "*")):
            if f.endswith((".mp4", ".jpg")):
                zipf.write(f, os.path.basename(f))
    return FileResponse(zip_path, filename="media.zip")

# --- Logs ---
@app.get("/api/logs/app")
def get_app_log():
    """
    Retorna o log do aplicativo (system.log).
    """
    return FileResponse(LOG_FILE, media_type="text/plain", filename="application.log")

@app.get("/api/logs/kernel")
def get_kernel_log():
    """
    Tenta ler logs do kernel.
    - Primeiro via dmesg -T (humano-legível)
    - Se falhar, tenta journalctl -k
    """
    try:
        output = subprocess.check_output(
            ["dmesg", "-T"],
            encoding="utf-8",
            errors="ignore"
        )
        lines = output.splitlines()[-500:]
        return Response(content="\n".join(lines), media_type="text/plain")
    except Exception:
        try:
            output = subprocess.check_output(
                ["journalctl", "-k", "-n", "500", "--no-pager"],
                encoding="utf-8",
                errors="ignore"
            )
            return Response(content=output, media_type="text/plain")
        except Exception as e2:
            return Response(content=f"Erro ao ler Kernel Log: {e2}", media_type="text/plain")

@app.get("/api/logs/system")
def get_system_log():
    """
    Retorna últimas 500 linhas do journal do sistema.
    """
    try:
        output = subprocess.check_output(
            ["journalctl", "-n", "500", "--no-pager"],
            encoding="utf-8"
        )
        return Response(content=output, media_type="text/plain")
    except Exception as e:
        return Response(content=f"Erro ao ler System Log: {e}", media_type="text/plain")

# --- TAILSCALE ---
@app.get("/api/tailscale/status")
def tailscale_status():
    """
    Checa se tailscaled está active.
    """
    try:
        out = subprocess.check_output(
            ["systemctl", "is-active", "tailscaled"],
            encoding="utf-8"
        ).strip()
        return {"tailscale": out}
    except:
        return {"tailscale": "unknown"}

@app.get("/api/tailscale/disable")
def disable_tailscale():
    """
    Para e desabilita tailscaled.
    OBS: requer sudo sem senha ou serviço rodando com permissões elevadas.
    """
    try:
        subprocess.run(["sudo", "systemctl", "stop", "tailscaled"])
        subprocess.run(["sudo", "systemctl", "disable", "tailscaled"])
        logger.info("Tailscale DESATIVADO via API")
        return {"tailscale": "disabled"}
    except Exception as e:
        logger.error(f"Erro ao desativar tailscale: {e}")
        return {"error": str(e)}

@app.get("/api/tailscale/enable")
def enable_tailscale():
    """
    Habilita e inicia tailscaled.
    """
    try:
        subprocess.run(["sudo", "systemctl", "enable", "tailscaled"])
        subprocess.run(["sudo", "systemctl", "start", "tailscaled"])
        logger.info("Tailscale ATIVADO via API")
        return {"tailscale": "enabled"}
    except Exception as e:
        logger.error(f"Erro ao ativar tailscale: {e}")
        return {"error": str(e)}

@app.get("/api/motor/on")
def motor_on():
    stm.send("ON")
    return {"ok": True}

@app.get("/api/motor/off")
def motor_off():
    stm.send("OFF")
    return {"ok": True}

@app.get("/api/motor/status")
def motor_status():
    # dispara STATUS no STM e devolve o último status conhecido também
    stm.send("STATUS")
    return {"ok": True, "last": stm.last_status}

@app.get("/api/motor/stream")
def motor_stream():
    q = queue.Queue(maxsize=200)

    # registra cliente SSE
    with stm.sse_lock:
        stm.sse_clients.add(q)

    def gen():
        # opcional: manda backlog imediato
        for line in list(stm.logs)[-50:]:
            yield f"data: {line}\n\n"

        try:
            while True:
                line = q.get()
                yield f"data: {line}\n\n"
        finally:
            with stm.sse_lock:
                stm.sse_clients.discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream")

@app.on_event("startup")
def _startup():
    try:
        stm.open()
        logger.info(f"STM serial aberta em {stm.port} @ {stm.baud}")
    except Exception as e:
        logger.error(f"Falha ao abrir serial STM: {e}")

# --- Shutdown ---
@app.on_event("shutdown")
def shutdown_event():
    """
    Quando o servidor desliga, garante que processo da câmera não fique órfão.
    """
    cam.stop_process()
    stm.close()
