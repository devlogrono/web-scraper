# Web Scraper RFEF

## Descripción general

Este proyecto es un **web scraper orientado a la extracción de datos competitivos de fútbol** desde la web de la RFEF, estructurando la información en varias fases encadenadas:

1. Competiciones
2. Jornadas
3. Actas
4. Sustituciones

El scraper combina **peticiones HTTP con sesión autenticada** y **automatización con navegador (Selenium / undetected-chromedriver)** cuando la web lo requiere.

El resultado final del proceso es la **persistencia de los datos en una base de datos MySQL** y, de forma intermedia, el uso de DataFrames para el tratamiento de la información.

---

## Estructura del proyecto

```
.
├── main.py
├── config.py
├── http_client.py
├── competitions.py
├── scraper_competiciones.py
├── scraper_jornadas.py
├── scraper_actas.py
├── requirements.txt
├── logs/
│   └── scraper.log
```

### Descripción de archivos

* **main.py**
  Punto de entrada del proyecto. Orquesta la ejecución completa del scraper:

  * Inicializa logging
  * Crea la sesión HTTP
  * Ejecuta los scrapers en orden
  * Consolida los DataFrames
  * Persiste los datos en MySQL

* **config.py**
  Contiene la configuración general del scraper:

  * URLs base
  * Credenciales
  * Parámetros de espera y rate limit
  * Configuración de base de datos

* **http_client.py**
  Gestiona la creación de la sesión HTTP autenticada y utilidades asociadas a las peticiones.

* **competitions.py**
  Definiciones y estructuras relacionadas con las competiciones a procesar.

* **scraper_competiciones.py**
  Extrae la información de competiciones disponibles.

* **scraper_jornadas.py**
  Obtiene las jornadas de cada competición, utilizando Selenium cuando es necesario.

* **scraper_actas.py**
  Accede a las actas de los partidos, gestiona la sesión autenticada y extrae:

  * Información del partido
  * Sustituciones

* **requirements.txt**
  Dependencias necesarias para ejecutar el proyecto.

* **logs/scraper.log**
  Archivo de logs generado automáticamente por el sistema de logging.

---

## Flujo de ejecución

1. Se inicia el programa desde `main.py`.
2. Se configura el sistema de logging.
3. Se crea una sesión HTTP autenticada contra la web objetivo.
4. Se obtienen las competiciones configuradas.
5. Para cada competición:

   * Se obtienen las jornadas.
   * Para cada jornada, se obtienen las actas.
6. Se procesan y consolidan los datos en DataFrames.
7. Se insertan los datos en la base de datos MySQL.

---

## Sistema de logging

* El logging se inicializa en `main.py`.
* El propio código crea automáticamente:

  * El directorio `logs/`
  * El archivo `scraper.log`
* Los mensajes se escriben:

  * En consola
  * En el archivo de log

El nivel de logging por defecto es **INFO**.

---

## Requisitos del sistema

* Python 3.10+
* Google Chrome instalado (para Selenium)
* Acceso a la base de datos MySQL configurada

---

## Instalación

1. Crear un entorno virtual:

```bash
python3 -m venv venv
source venv/bin/activate
```

2. Instalar dependencias:

```bash
pip install -r requirements.txt
```

---

## Ejecución

El scraper admite la ejecución en dos entornos:

 - AWS (por defecto)
 - Local

La diferencia principal entre entornos es la configuración de Google Chrome utilizada por Selenium (undetected_chromedriver).

## Ejecución en AWS (por defecto)

El scraper se ejecuta como módulo Python, desde fuera del directorio del proyecto, tal como se viene haciendo actualmente.

```bash
python -m web_scraper.main
```

En este modo:

 - Se asume entorno AWS.
 - Se utiliza Chrome for Testing configurado en el sistema.

## Ejecución en entorno local

Para ejecutar el scraper en local manteniendo el mismo esquema de ejecución por módulo, es necesario indicar explícitamente el entorno mediante el parámetro --env.

```bash
python -m web_scraper.main --env=local
```

En este modo:

 - Se utiliza la versión local de Google Chrome instalada en el sistema.

 - No se fuerza la ruta del binario de Chrome.

 - Se ajusta automáticamente la versión principal de Chrome utilizada por undetected_chromedriver.

## Ejecución en background

Ejecutar el scraper en segundo plano:

```bash
nohup python -m web_scraper.main &
```

Comprobar proceso:

```bash
ps aux | grep main.py
```

Ver logs en tiempo real:

```bash
tail -f logs/scraper.log
```

---

## Notas operativas

* El scraper implementa pausas y esperas para evitar bloqueos por rate limit.
* Algunas partes del scraping requieren navegador automatizado.
* La ejecución completa puede ser prolongada dependiendo del volumen de datos.

---