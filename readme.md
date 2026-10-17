# ACDS: Assetto Corsa Dedicated Server en Windows 11 (nativo)

## Descripción

Servidor dedicado de Assetto Corsa ejecutado de forma nativa en Windows 11, accesible para otros jugadores mediante Tailscale (sin exponer puertos en el router). Incluye `ac_config.py`, un asistente interactivo en terminal que lee las pistas, coches y liveries disponibles en el servidor y genera `server_cfg.ini` y `entry_list.ini`.

**Arquitectura**

- **Windows 11:** ejecuta `acServer.exe`, instalado vía Steam (*Assetto Corsa Dedicated Server*), y el asistente de configuración.
- **Tailscale para Windows:** red privada (VPN/LAN) entre el servidor y los jugadores.
- **`ac_config.py`:** se ejecuta directamente en PowerShell mediante `uv`. Opera sobre la carpeta del servidor en Steam, y controla el proceso con PowerShell (`Get-Process`), `taskkill` y `subprocess`. No requiere servicios de Windows.

**Puertos:** 9600 (UDP y TCP) y 8081 (TCP).

## 1. Preparación del entorno

Todos los comandos son de **PowerShell**.

### 1.1 Prerrequisitos

- Windows 11.
- Steam con *Assetto Corsa Dedicated Server* instalado.
- [Tailscale para Windows](https://tailscale.com/download/windows) instalado y con sesión iniciada.

### 1.2 Chocolatey

Desde **PowerShell como Administrador**:

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force
[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072
iex ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))
```

Verifica con `choco --version`.

### 1.3 Herramientas

```powershell
choco install git uv -y
```

Cierra y vuelve a abrir PowerShell para que `git` y `uv` queden en el `PATH`.

### 1.4 Clonar el repositorio

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\Tools" | Out-Null
git clone https://github.com/Athirtro/ACDS-WSL/ "$env:USERPROFILE\Tools\ACDS-WSL"
```

Actualizar en el futuro:

```powershell
git -C "$env:USERPROFILE\Tools\ACDS-WSL" pull
```

### 1.5 Tailscale

```powershell
tailscale ip -4
```

- La IP mostrada es la que usarán los jugadores.
- Las ACL de la tailnet deben permitir 9600 (UDP/TCP) y 8081 (TCP). **Por defecto lo están**.
- Si juegas desde el mismo equipo, puedes usar la IP de Tailscale igualmente o conectarte desde la IP local que detecte el CM.

### 1.6 Firewall de Windows

Permite el tráfico de `acServer.exe` (PowerShell como Administrador):

```powershell
$exe = "C:\Program Files (x86)\Steam\steamapps\common\Assetto Corsa Dedicated Server\acServer.exe"
New-NetFirewallRule -DisplayName "ACDS TCP" -Direction Inbound -Program $exe -Protocol TCP -LocalPort 9600,8081 -Action Allow
New-NetFirewallRule -DisplayName "ACDS UDP" -Direction Inbound -Program $exe -Protocol UDP -LocalPort 9600 -Action Allow
```

### 1.7 Ajustes base en `cfg\server_cfg.ini`

```powershell
notepad "C:\Program Files (x86)\Steam\steamapps\common\Assetto Corsa Dedicated Server\cfg\server_cfg.ini"
```

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

> La carpeta del servidor está en `Program Files (x86)`. Para editar archivos allí, abre el editor o PowerShell como Administrador.

## 2. Ejecución del asistente

### 2.1 Ejecutar

Desde **PowerShell como Administrador** (necesario para escribir en `Program Files (x86)`):

```powershell
cd "$env:USERPROFILE\Tools\ACDS-WSL"
uv run ac_config.py
```

En la primera ejecución `uv` instala las dependencias (`questionary`) automáticamente.

### 2.2 Gestión del proceso

El script gestiona `acServer.exe` sin servicios de Windows:

1. **Detención:** localiza los procesos de `acServer.exe` de la carpeta del servidor (`Get-Process`, filtrando por ruta) y los termina con `taskkill /PID <pid> /T /F`.
2. **Escritura:** guarda `server_cfg.ini` y `entry_list.ini`.
3. **Inicio:** lanza `acServer.exe` en una **nueva consola** con `subprocess.Popen`, incluso si la escritura falló.

Para detener el servidor manualmente, cierra la consola de `acServer.exe` o ejecuta:

```powershell
taskkill /IM acServer.exe /T /F
```

El servidor **no se reinicia con Windows**: se inicia al ejecutar el asistente, o manualmente ejecutando `acServer.exe` desde su carpeta o desde Steam al ejecutar el Assetto Corsa Dedicated Server.

### 2.3 Verificar el servidor

En la consola de `acServer.exe` deben aparecer `Found car CAR_0` y `PAGE OK`. Comprueba además que el proceso está activo:

```powershell
Get-Process acServer
```

### 2.4 Conexión de jugadores

- Content Manager: IP de Tailscale del servidor, puerto 9600.
- Enlace directo: `acmanager://race/online/join?ip=<ip-tailscale>&httpPort=8081`

Obtén `<ip-tailscale>` con `tailscale ip -4` o desde la consola de administración de Tailscale.

### 2.5 Argumentos

| Argumento | Descripción | Por defecto |
|---|---|---|
| `--root` | Carpeta raíz del servidor (contiene `acServer.exe`, `cfg\` y `content\`) | `C:\Program Files (x86)\Steam\steamapps\common\Assetto Corsa Dedicated Server` |
| `--exe` | Nombre del ejecutable del servidor | `acServer.exe` |
| `--no-service` | No detiene ni inicia el proceso del servidor | desactivado |

Ejemplo con otra ubicación:

```powershell
uv run ac_config.py --root "D:\SteamLibrary\steamapps\common\Assetto Corsa Dedicated Server"
```

## 3. Capacidades actuales

**Escaneo y selección**
- Lee el contenido directamente desde `<root>\content`.
- Detecta pistas válidas y sus layouts.
- Detecta coches válidos y sus liveries (lee `ui_skin.json` para mostrar el nombre).
- Carga la configuración actual como valores por defecto.

**Parámetros configurables**
- Pista y layout.
- Sesiones a ejecutar: práctica, qualify y carrera, en cualquier combinación (al menos una).
- Duración de práctica y qualify (minutos, mínimo 1). Por defecto: 20 y 15 minutos.
- Carrera por vueltas (por defecto 10) o por tiempo en minutos.
- Overtime de qualify (`QUALIFY_MAX_WAIT_PERC`): porcentaje de la mejor vuelta de la sesión que se concede para terminar la vuelta en curso al acabarse el tiempo. Por defecto: 120.
- Tiempo de cierre de carrera (`RACE_OVER_TIME`): segundos que tienen los demás pilotos para terminar tras cruzar el líder. Por defecto: 180.
- Vuelta extra en carreras por tiempo (`RACE_EXTRA_LAP`). Por defecto: desactivada.
- Hora de inicio (formato 24 h, `HH:MM`, por defecto 14:00) y multiplicador del paso del tiempo (entero, por defecto 1). **En desarrollo, soporte parcial** (ver más abajo).
- Número de slots (solo jugadores humanos, sin IA).
- Asignación de coches: el mismo para todos o uno por slot.
- Asignación de liveries.
- Ayudas (opcional; por defecto se conservan los valores actuales): ABS y control de tracción (0 = prohibido, 1 = solo coches que lo incluyen, 2 = todos), control de estabilidad y embrague automático (sí/no).

> La práctica no tiene overtime: el servidor la termina exactamente al cumplirse su duración.

**Hora y paso del tiempo (en desarrollo, soporte parcial)**
- La hora se escribe en `SUN_ANGLE` (`[SERVER]`) con la relación `16° por hora, 13:00 = 0°`, y el multiplicador en `TIME_OF_DAY_MULT`.
- **Rango de hora soportado:** de **06:00 a 18:00**, verificado en pruebas. Fuera de ese rango (horarios nocturnos) el reloj se queda fijo en las 18:00, porque `acServer.exe` vainilla no admite horas nocturnas mediante `SUN_ANGLE`.
- **Multiplicador del paso del tiempo:** **no funciona correctamente**; el tiempo transcurre siempre con multiplicador 1, sea cual sea el valor configurado.
- Las jornadas nocturnas y el multiplicador requieren WeatherFX de CSP (el tiempo se codifica en `GRAPHICS` de `[WEATHER_n]`, con `_type`, `_time`, `_mult` y `_start`). El script ya intenta escribirlo, pero aún no produce el resultado esperado en Content Manager con CSP. Se investiga el formato exacto que genera Content Manager para reproducirlo.
- Con WeatherFX, cada sesión reinicia la hora a la de inicio configurada (el tiempo no es continuo entre sesiones).

**Escritura y proceso**
- Muestra un resumen y pide confirmación antes de guardar.
- Detiene `acServer.exe`, escribe la configuración y lo vuelve a iniciar, incluso si la escritura falló.
- Las sesiones omitidas se eliminan de `server_cfg.ini` (el servidor solo ejecuta las secciones presentes); las activas conservan sus claves existentes (`WAIT_TIME`, etc.).
- Las secciones de sesión se escriben en orden (`[PRACTICE]`, `[QUALIFY]`, `[RACE]`) inmediatamente después de `[SERVER]`.
- En carreras por vueltas solo se escribe `LAPS`, y en carreras por tiempo solo `TIME`; nunca ambas.
- La carrera siempre se escribe con `IS_OPEN=1` (entrada libre). Con `IS_OPEN=2` la ventana de entrada se cierra antes de la salida y, con la carrera como primera sesión, el servidor entraba en bucle y rechazaba conexiones con error de *handshake*.
- Genera `entry_list.ini` con una sección `[CAR_n]` por slot.
- Escribe `CARS=` separado por `;` y ajusta `MAX_CLIENTS` al número de slots.
- Crea copias de seguridad `server_cfg.ini.bak` y `entry_list.ini.bak` antes de sobrescribir.
- Si no hay permisos de escritura, informa y termina con código 3 (ejecuta PowerShell como Administrador).

### Cosas por hacer

1. Soporte para configurar libremente las sesiones (práctica, qualify y carrera): omitirlas, combinarlas y ajustar su duración y overtime. **Completada.**
2. Soporte para el ajuste de hora y del paso del tiempo. **En desarrollo, soporte parcial:** hora funcional de 06:00 a 18:00; pendientes las jornadas nocturnas y el multiplicador de tiempo.
3. Soporte para el ajuste del clima.
4. Soporte para el ajuste de temperatura ambiente y del asfalto.
5. Soporte para el ajuste del viento.
6. Soporte para el ajuste de condición del circuito.
7. Soporte para el ajuste de ayudas (ABS, control de tracción, control de estabilidad, embrague automático). **En desarrollo:** implementado y probado.
8. Soporte para el ajuste de realismo: desgaste de neumáticos, consumo de combustible, daños y mantas térmicas.
9. Soporte para el ajuste completo de parámetros del servidor.
10. Plantillas de sesión: Práctica, Hotlap, Race, Track Day y Weekend.
11. Presets de evento reutilizables (pista, coches, liveries, sesiones, clima y ayudas).
12. Interfaz gráfica accesible mediante API local.
13. Soporte futuro para otros sistemas operativos, especialmente basados en Linux.

## Dirección técnica

El script actual es un MVP para Windows 11. El desarrollo continúa en este orden:

1. **Refactor por reglas:** cada parámetro configurable (hora, clima, viento, ayudas, realismo, sesiones, overtime, etc.) se encapsula en su propia unidad de lógica, con su clave, sección del `.ini`, tipo, validación y valor por defecto. Añadir un parámetro nuevo no debe requerir modificar el resto del programa.
2. **Separación MVC simplificado:**
   - Modelo: reglas, estado y repositorios (`.ini`, contenido, presets).
   - Controlador: casos de uso (aplicar configuración, controlar el proceso `acServer.exe`).
   - Vista: CLI como primera implementación, intercambiable.
   - El control de procesos (`taskkill`, `subprocess`) queda aislado detrás de una interfaz para facilitar portar a otros sistemas operativos (ítem 13).
3. **Funcionalidades:** completar la lista de tareas pendientes sobre la nueva arquitectura.
4. **Presets de evento:** pista, coches, liveries, sesiones, clima y ayudas reutilizables.
5. **Interfaz web:** API local sobre el controlador y UI web para usuarios que no dominen la consola. Se inicia solo cuando las funcionalidades estén completas.
6. **Instalador:** script de PowerShell de un solo comando que instale Chocolatey, `git` y `uv`, clone el repositorio en `~\Tools\ACDS-WSL`, configure el firewall y deje listo el asistente.
