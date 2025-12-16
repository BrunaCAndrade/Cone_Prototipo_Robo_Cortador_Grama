# CONE — Cortador Operado por Navegação Eletrônica
 UTFPR — Campus Pato Branco | Engenharia de Computação | Oficina de Integração | Projeto Robotnik  
## Visão geral
O **CONE (Cortador Operado por Navegação Eletrônica)** é um robô autônomo para corte de grama, desenvolvido com **arquitetura de processamento distribuído**:

- **Alto nível (Raspberry Pi 4)**: visão computacional (TensorFlow Lite), tomada de decisão, navegação visual e **PWA** para coleta/gestão de dados (fotos, vídeos, logs, datasets).
- **Baixo nível (STM32 Blackpill F411)**: controle em tempo real, leitura de sensores (ex.: giroscópio, acelerômetro, lasers/ToF) e acionamento de motores.

Este repositório concentra a **infra do Raspberry Pi (AP + FastAPI + PWA + captura de câmera)** e integrações (ex.: UART com STM32).

---

## Hardware
### Raspberry Pi 4 Model B
- CPU: Broadcom BCM2711, Quad core Cortex-A72 (ARM v8) 64-bit @ 1.8 GHz  
- RAM: 4 GB  
- Armazenamento: microSD 32 GB  
- Alimentação: 5V (USB-C, mínimo 3A)

### Câmera
**Raspberry Pi Camera Module v1.3**
- Sensor: OmniVision OV5647 (CMOS 5 MP)
- Resolução máxima: 2592 × 1944
- Vídeo: 1080p @ 30 fps
- FOV: ~60°–65°

### MCU (baixo nível)
**STM32 F411CEU6**
- Controle dos motores (ponte H l298n)
- Comunicação com Raspberry Pi via **UART** (por meio de TTL - USB)

---
