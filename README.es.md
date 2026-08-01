# Monad Buy Bot

Un bot de Telegram de alertas de compras para la blockchain **Monad**.
Agrégalo a tu grupo, rastrea cualquier token ERC-20 y recibe alertas de compra
en tiempo real con emojis personalizables, alertas de ballenas y estadísticas
de 24 h — tanto para tokens listados en DEX como para tokens en curva de
vinculación ("incubación").

## Características

- **Cualquier token de Monad** — listados en DEX (PancakeSwap v2/v3, Uniswap
  v3, Kuru) y **tokens en incubación / curva de vinculación** (launchpads
  estilo nad.fun), con progreso de graduación en vivo dentro de la alerta.
- **Emojis personalizables** — emoji de compra y emoji de ballena por grupo;
  la alerta repite 1 emoji por cada `N` MON gastados (paso configurable,
  máximo 20).
- **Alertas de ballenas** — emoji dedicado y línea de ballena por encima de un
  umbral en MON configurable por grupo.
- **Multi-idioma** — English (`en`), Español (`es`), 中文 (`zh`) por grupo.
- **Estadísticas y leaderboard** — volumen de compras de 24 h y mejores
  compradores por grupo.
- **Botones en línea** — cada alerta enlaza a la transacción, al gráfico y a
  una página de compra.
- **Configuración con botones** — `/settings` abre un menú interactivo:
  idioma, emojis (con presets), umbrales, interruptores de ventas/escáner y
  gestión de tokens, sin necesidad de memorizar comandos.
- **Persistencia SQLite** — ajustes, tokens rastreados y estadísticas
  sobreviven a los reinicios.

## Notas sobre Monad

- Mainnet: **chain ID 143**, RPC `https://rpc.monad.xyz`, token nativo **MON**
  (18 decimales), explorador `https://monadvision.com`.
- Testnet: chain ID 10143, RPC `https://testnet-rpc.monad.xyz`.
- Monad es totalmente compatible con EVM; el bot usa las herramientas
  estándar de `web3.py`.
- Los tokens en incubación se negocian en una curva de vinculación hasta su
  graduación (~225.000 MON recaudados); después la liquidez migra a un DEX.
  Ambas fases son rastreadas.

## Comandos

| Comando | Descripción | Admin |
|---|---|---|
| `/start` | Mensaje de bienvenida e introducción del bot | no |
| `/help` | Mostrar todos los comandos | no |
| `/addtoken <address>` | Rastrear las compras de un token en este grupo | sí |
| `/removetoken <address>` | Dejar de rastrear un token | sí |
| `/tokens` | Listar tokens rastreados | no |
| `/setemoji <emoji>` | Establecer emoji de alerta de compra | sí |
| `/setwhaleemoji <emoji>` | Establecer emoji de alerta de ballena | sí |
| `/setlanguage <en\|es\|zh>` | Establecer idioma del grupo | sí |
| `/setminbuy <MON>` | Compra mínima para disparar alertas | sí |
| `/setwhale <MON>` | Umbral de alerta de ballena en MON | sí |
| `/price [address]` | Precio del token en MON/USD | no |
| `/mcap [address]` | Capitalización de mercado del token | no |
| `/incubation [address]` | Progreso de la curva de vinculación (incubación) | no |
| `/stats` | Estadísticas de compras de 24 h del grupo | no |
| `/leaderboard` | Mejores compradores del grupo | no |
| `/settings` | Abrir el menú de configuración con botones | no |
| `/about` | Acerca de este bot | no |

Los comandos de administración requieren ser administrador del grupo; en
chats privados todos están permitidos. `[address]` es opcional y usa por
defecto el primer token rastreado del grupo.

## Configuración con botones

Envía `/settings` en tu grupo y configúralo todo con botones en línea:

- **Idioma** — selector de English / Español / 中文 (el actual marcado ✅).
- **Emojis** — emojis de alerta de compra, ballena y venta. Elige entre más
  de 10 presets por tipo (🟢🟩💚🚀🔥💰🤑⚡🦄🌕 para compras,
  🐋🐳🦈🐙💎🏦👑🔱⚓🌊 para ballenas) o toca **✏️ Personalizado** y responde
  con tu propio emoji (entrada guiada mediante ForceReply).
- **Cantidades** — compra mínima, umbral de ballena y paso de emoji, con
  presets de 1/5/10/50/100 MON o un valor personalizado (validado, debe ser
  > 0).
- **Interruptores** — alertas de venta y alertas de nuevos tokens (escáner),
  activado/desactivado por grupo.
- **Tokens** — la lista de tokens rastreados con un botón 🗑 por token para
  dejar de rastrearlo. `/tokens` muestra los mismos botones.
- `/start` en un grupo muestra accesos directos ⚙️ Configuración / 📖 Ayuda /
  ➕ Añádeme.

Cualquier miembro del grupo puede abrir y explorar los menús; solo los
administradores pueden cambiar valores (la misma regla que los comandos de
administración).

## Herramientas avanzadas

Cuando los módulos avanzados están habilitados, estos comandos extra están
disponibles (alertas de precio, alertas de venta, escáner de lanzamientos,
tarjeta de seguridad del token y un panel en vivo):

| Comando | Descripción | Admin |
|---|---|---|
| `/pricealert <address> <above\|below> <price_MON>` | Establecer una alerta de precio para un token | no |
| `/alerts` | Listar y gestionar tus alertas de precio | no |
| `/scanner` | Activar/desactivar alertas de nuevos tokens en incubación | sí |
| `/sells` | Activar/desactivar alertas de venta en este grupo | sí |
| `/tokeninfo [address]` | Tarjeta de seguridad del token (liquidez, holders) | no |
| `/dashboard` | Publicar un panel de estadísticas que se actualiza solo | sí |

Las alertas de venta y del escáner también se pueden activar desde el menú
de botones de `/settings`.

## Instalación

### 1. Crea el bot con BotFather

1. Abre [@BotFather](https://t.me/BotFather) en Telegram.
2. Envía `/newbot` y sigue las instrucciones para obtener tu **token del bot**.
3. (Recomendado) Envía `/setprivacy` → `Disable` para que el bot pueda leer
   los mensajes del grupo; luego agrégalo a tu grupo como miembro.

### 2. Configura el entorno

```bash
cp .env.example .env
# edita .env y establece TELEGRAM_TOKEN
```

Solo `TELEGRAM_TOKEN` es obligatorio — todos los demás campos tienen valores
por defecto funcionales (RPC de Monad mainnet, chain ID 143, explorador
monadvision, enlaces de compra de nad.fun). Consulta `.env.example` para la
lista completa.

### 3a. Ejecutar localmente

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

### 3b. Ejecutar con Docker

```bash
docker build -t monad-buy-bot .
docker run -d --env-file .env -v $(pwd)/data:/app/data monad-buy-bot
```

o con docker compose:

```bash
docker compose up -d --build
```

El volumen `data/` conserva la base de datos SQLite entre reinicios.

### 4. Empieza a rastrear

En tu grupo (como administrador):

```
/addtoken 0x1234...abcd
/setminbuy 5
/setwhale 500
/setemoji 🔥
/setlanguage es
```

## Pruebas

```bash
pip install -r requirements.txt
python -m pytest tests/ -v
```

Las pruebas están completamente simuladas (mock) — sin llamadas reales a RPC
ni a Telegram.

## Estructura del proyecto

```
bot/        Capa de Telegram (handlers, notifier, keyboards, app)
chain/      Capa RPC de Monad (client, listener, detector, price, incubation)
core/       config, base de datos SQLite, i18n, dataclasses compartidos
locales/    traducciones en.json / es.json / zh.json
tests/      suite de pytest (chain y telegram simulados)
main.py     punto de entrada
```
