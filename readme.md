# Assetto Corsa Dedicated Server en Debian WSL

## Descripción

Servidor dedicado de Assetto Corsa ejecutado sobre Debian en WSL 2, accesible para otros jugadores mediante Tailscale (sin exponer puertos en el router). Incluye `ac_config.py`, un asistente interactivo en terminal que sincroniza el contenido del juego instalado en Windows y genera `server_cfg.ini` y `entry_list.ini` a partir de las pistas, coches y liveries disponibles.

**Arquitectura**

- Windows: Steam con *Assetto Corsa* y *Assetto Corsa Dedicated Server* instalados (origen del contenido y del binario `acServer`).
- Debian WSL: ejecuta `acServer` como servicio systemd y el asistente de configuración.
- Tailscale: red privada entre el servidor y los jugadores.

**Puertos:** 9600 (UDP y TCP) y 8081 (TCP).

## 1. Infraestructura en Debian WSL

Los comandos asumen `bash` (shell por defecto de Debian).

### 1.1 Requisitos

- Windows 11 con WSL 2.
- Steam con *Assetto Corsa* y *Assetto Corsa Dedicated Server* instalados.
- Cuenta de Tailscale.

### 1.2 Crear la distro (PowerShell)

```powershell
wsl --install Debian --name AssettoCorsaServer
```

Crea el usuario cuando lo solicite. Luego entra a la distro con `wsl -d AssettoCorsaServer`.

### 1.3 Verificar systemd

```bash
ps -p 1 -o comm=
```

Debe devolver `systemd`. Si no, ejecuta:

```bash
printf '[boot]\nsystemd=true\n' | sudo tee /etc/wsl.conf
```

y reinicia la distro.

### 1.4 Paquetes

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y curl rsync git
```

### 1.5 Tailscale

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
tailscale ip -4
```

- La IP mostrada es la que usarán los jugadores.
- Si juegas desde el mismo equipo, Tailscale también debe estar activo en Windows.
- Las ACL de la tailnet deben permitir 9600 (UDP/TCP) y 8081 (TCP). **Por defecto lo están**.

### 1.6 Copiar el servidor dedicado

```bash
STEAM="/mnt/c/Program Files (x86)/Steam/steamapps/common"
SRV_SRC="$STEAM/Assetto Corsa Dedicated Server"
DEST="$HOME/assetto-server"

mkdir -p "$DEST"
rsync -a --exclude='content/' "$SRV_SRC/" "$DEST/"
chmod +x "$DEST/acServer"
```

El contenido (`content/`) lo sincroniza el asistente. Verifica con `ls "$DEST"` que existan `acServer` y `cfg/`.

### 1.7 Ajustes base en `cfg/server_cfg.ini`

```bash
micro "$DEST/cfg/server_cfg.ini"
```
También puedes usar nano para editar archivos.

Valores obligatorios para uso en LAN/VPN:

```ini
REGISTER_TO_LOBBY=0
UDP_PORT=9600
TCP_PORT=9600
HTTP_PORT=8081
UDP_PLUGIN_ADDRESS=
AUTH_PLUGIN_ADDRESS=
ADMIN_PASSWORD=<tu_contraseña>
```

### 1.8 Servicio systemd

```bash
sudo tee /etc/systemd/system/assetto-corsa.service > /dev/null <<EOF
[Unit]
Description=Assetto Corsa Dedicated Server
After=network-online.target tailscaled.service
Wants=network-online.target

[Service]
User=$USER
WorkingDirectory=$HOME/assetto-server
ExecStart=$HOME/assetto-server/acServer
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable assetto-corsa.service
```

El servicio se inicia al ejecutar el asistente por primera vez (sección 2), cuando ya existe contenido y configuración válida.

## 2. Instalación del asistente de configuración

### 2.1 Instalar uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
exec $SHELL -l
```

### 2.2 Clonar el repositorio

```bash
git clone https://github.com/Athirtro/ACDS-WSL/ ~/assetto-server
```

Actualizar en el futuro:

```bash
git -C ~/assetto-server pull
```
**TODO: comando pendiente de revisión.**

### 2.3 Ejecutar

```bash
uv run ~/assetto-server/ac_config.py
```

`uv` instala las dependencias (`questionary`) automáticamente en la primera ejecución.

### 2.4 Verificar el servidor

```bash
systemctl status assetto-corsa.service --no-pager
sudo journalctl -u assetto-corsa.service -n 25 --no-pager
```

Debe aparecer `Found car CAR_0` y `PAGE OK`, y el servicio habilitado y activo.

### 2.5 Conexión de jugadores

- Content Manager: IP de Tailscale del servidor, puerto 9600.
- Enlace directo: `acmanager://race/online/join?ip=<ip-tailscale>&httpPort=8081`

Revisa la consola de TailScale para asegurarte de la IP para reemplazar por `<ip-tailscale>`.

### 2.6 Argumentos

| Argumento | Descripción | Por defecto |
|---|---|---|
| `--root` | Carpeta del servidor | carpeta del script |
| `--source` | `content/` del juego en Windows | `/mnt/c/Program Files (x86)/Steam/steamapps/common/assettocorsa/content` |
| `--service` | Nombre del servicio systemd | `assetto-corsa.service` |
| `--no-service` | No detiene ni inicia el servicio | desactivado |
| `--no-sync` | Omite la sincronización de contenido | desactivado |
| `--mirror` | Borra en el destino lo que ya no exista en el origen | desactivado |

## 3. Capacidades actuales

**Sincronización de contenido**
- Copia `content/` desde Windows con `rsync`, excluyendo modelos `*.kn5`.
- Muestra progreso en vivo, tiempo total y carpetas actualizadas.
- Si el origen no está disponible, continúa con el contenido existente.

**Escaneo y selección**
- Detecta pistas válidas y sus layouts.
- Detecta coches válidos y sus liveries (lee `ui_skin.json` para mostrar el nombre).
- Carga la configuración actual como valores por defecto.

**Parámetros configurables**
- Pista y layout.
- Duración de práctica y qualify (minutos, mínimo 1).
- Duración de carrera (vueltas).
- Número de slots (solo jugadores humanos, sin IA).
- Asignación de coches: el mismo para todos o uno por slot.
- Asignación de liveries.

**Escritura y servicio**
- Muestra un resumen y pide confirmación antes de guardar.
- Detiene el servicio, escribe la configuración y lo vuelve a iniciar, incluso si la escritura falla.
- Genera `entry_list.ini` desde `[CAR_0]`.
- Escribe `CARS=` separado por `;` y ajusta `MAX_CLIENTS` al número de slots.
- Crea copias de seguridad `server_cfg.ini.bak` y `entry_list.ini.bak` antes de sobrescribir.

### Cosas por hacer:
1. Soporte para omitir sesiones de práctica y qualify.
2. Soporte para el ajuste de hora.
3. Soporte para el ajuste del clima.
4. Soporte para el ajuste de temperatura ambiente y del asfalto.
5. Soporte para el ajuste del viento.
6. Soporte para el ajuste de condición del circuito.
7. Soporte para el ajuste de ayudas (ABS, control de tracción, control de estabilidad, embrague automático).
8. Soporte para el ajuste de realismo: desgaste de neumáticos, consumo de combustible, daños y mantas térmicas.
9. Soporte para el ajuste completo de parámetros del servidor.
10. Plantillas de sesión: Práctica, Hotlap, Race, Track Day y Weekend.
11. Presets de evento reutilizables (pista, coches, liveries, sesiones, clima y ayudas).
12. Interfaz gráfica accesible mediante API local.

## Dirección técnica

El script actual es un MVP. El desarrollo continúa en este orden:

1. **Refactor por reglas:** cada parámetro configurable (hora, clima, viento, ayudas, realismo, sesiones, etc.) se encapsula en su propia unidad de lógica, con su clave, sección del `.ini`, tipo, validación y valor por defecto. Añadir un parámetro nuevo no debe requerir modificar el resto del programa.
2. **Separación MVC simplificado:**
   - Modelo: reglas, estado y repositorios (`.ini`, contenido, presets).
   - Controlador: casos de uso (sincronizar, aplicar configuración, controlar el servicio).
   - Vista: CLI como primera implementación, intercambiable.
3. **Funcionalidades:** completar la lista de tareas pendientes sobre la nueva arquitectura.
4. **Interfaz web:** API sobre el controlador y UI web para usuarios que no dominen la consola. Se inicia solo cuando las funcionalidades estén completas.
5. **Instaladores:** scripts de un solo comando que descarguen el repositorio y preparen la infraestructura (distro, systemd, Tailscale, servidor dedicado, servicio y asistente).
