from __future__ import annotations

import logging
import time
import re
import unicodedata
from difflib import SequenceMatcher
from typing import List, Dict, Tuple
from urllib.parse import urljoin, urlparse, parse_qs

import pandas as pd
import requests
from bs4 import BeautifulSoup
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from .config import (
    BASE_URL,
    HTTP_HEADERS,
    ENGINE,
    OUTPUT_JORNADAS_CSV,
    FAST_TEST_JORNADAS_1FF,
    FAST_DEBUG_JORNADAS_MAX,
    RFEF_1FF_SEASON,
    RFEF_1FF_COMPETITION,
    RFEF_1FF_GROUP,
    RFEF_RESULTADOS_URL_TEMPLATE,
    RFEF_CHROME_VERSION_MAIN,
)
from .competitions import COMPETITIONS, JORNADA_URL_TEMPLATE
from .http_client import create_session, is_login_page, login

log = logging.getLogger(__name__)

try:
    # Evitar que el destructor de uc.Chrome vuelva a llamar a quit() al salir
    uc.Chrome.__del__ = lambda self: None  # type: ignore[attr-defined]
except Exception:
    pass

MATCH_BLOCK_SELECTOR = "div.portlet-body.body_fed"
ACTA_BTN_SELECTOR = 'a.btn.btn-sm.btn-success[href*="NFG_CmpPartido"]'
TEAM_LINK_SELECTOR = 'span.font_widgetL a[href*="NFG_VisEquipos"]'
TEAM_BADGE_SELECTOR = "img.escudo_clb"


def _get_first_row_tds(block: BeautifulSoup) -> List[BeautifulSoup]:
    tbl = block.find("table", recursive=False) or block.find("table")
    if not tbl:
        return []
    first_tr = tbl.find("tr")
    if not first_tr:
        return []
    tds = first_tr.find_all("td", recursive=False)
    return tds


def _parse_team_cell(td: BeautifulSoup) -> Tuple[str, str, str, str]:
    name = ""
    team_id = ""
    team_url = ""
    badge_url = ""
    a = td.select_one(TEAM_LINK_SELECTOR)
    if a and a.get("href"):
        name = a.get_text(" ", strip=True)
        rel = a["href"]
        if rel.startswith("NFG_VisEquipos"):
            rel = "/nfg/NPcd/" + rel
        team_url = urljoin(BASE_URL, rel)
        try:
            qs = parse_qs(urlparse(team_url).query)
            team_id = qs.get("Codigo_Equipo", [""])[0]
        except Exception:
            team_id = ""
    img = td.select_one(TEAM_BADGE_SELECTOR)
    if img and img.get("src"):
        badge_url = urljoin(BASE_URL, img["src"]) if not img["src"].startswith("http") else img["src"]
    return name, team_id, team_url, badge_url


def _parse_center_info(td_center: BeautifulSoup) -> Tuple[str, str, str, str]:
    goles_local = ""
    goles_visit = ""
    fecha = ""
    hora = ""
    goals_spans = td_center.select("h4 strong span.resultado_cerrada")
    if len(goals_spans) >= 2:
        gl_txt = goals_spans[0].get_text(strip=True)
        gv_txt = goals_spans[1].get_text(strip=True)
        import re as _re

        m1 = _re.search(r"\d+", gl_txt)
        m2 = _re.search(r"\d+", gv_txt)
        goles_local = m1.group(0) if m1 else ""
        goles_visit = m2.group(0) if m2 else ""
    dt_spans = td_center.select("h5 span.esconder")
    for i in range(len(dt_spans) - 1):
        f_txt = dt_spans[i].get_text(strip=True)
        h_txt = dt_spans[i + 1].get_text(strip=True)
        if any(ch.isdigit() for ch in f_txt) and ":" in h_txt:
            fecha = f_txt
            hora = h_txt
            break
    return goles_local, goles_visit, fecha, hora


def _parse_info_cells(block: BeautifulSoup) -> Tuple[str, str, str]:
    campo = ""
    tipo = ""
    arbitro = ""
    outer_tbl = block.find("table", recursive=False) or block.find("table")
    if not outer_tbl:
        return campo, tipo, arbitro
    outer_trs = outer_tbl.find_all("tr", recursive=False)
    if len(outer_trs) < 2:
        return campo, tipo, arbitro
    inner_tbl = None
    for td in outer_trs[1].find_all("td", recursive=False):
        t = td.find("table")
        if t:
            inner_tbl = t
            break
    if not inner_tbl:
        return campo, tipo, arbitro
    info_tr = inner_tbl.find("tr")
    if not info_tr:
        return campo, tipo, arbitro
    import re

    for td in info_tr.find_all("td", recursive=False):
        text = td.get_text(" ", strip=True)
        if "Campo:" in text:
            a = td.find("a")
            if a:
                campo = a.get_text(" ", strip=True)
            m = re.search(r"(Hierba\s+[A-Za-zÁÉÍÓÚÜÑñ ]+)", text, flags=re.IGNORECASE)
            if m:
                tipo = m.group(1).strip()
            continue
        if "Árbitro" in text or "Arbitro" in text:
            name = re.sub(r"^\s*Á?rbitro:\s*", "", text, flags=re.IGNORECASE).strip(" -:\u00A0 ")
            arbitro = name
            continue
    return campo, tipo, arbitro


def _normalize_team_name_for_match(name: str) -> str:
    s = unicodedata.normalize("NFKD", (name or "")).encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = re.sub(r"\b(sad|s\.a\.d\.|cf|c\.f\.|cd|c\.d\.|ud|u\.d\.|fc|f\.c\.|club)\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def fetch_rfef_actas_for_jornada_1ff(driver, journey: int) -> List[Tuple[str, str]]:
    url = RFEF_RESULTADOS_URL_TEMPLATE.format(
        season=RFEF_1FF_SEASON,
        competition=RFEF_1FF_COMPETITION,
        group=RFEF_1FF_GROUP,
        journey=journey,
    )
    log.info("1FF RFEF: cargando jornada %s: %s", journey, url)
    driver.get(url)

    try:
        WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Aceptar') or contains(., 'Aceptar todas') or contains(., 'Aceptar todo') or contains(., 'Aceptar cookies') ]"))
        ).click()
    except Exception:
        pass

    try:
        WebDriverWait(driver, 30).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "a.ext"))
        )
    except Exception:
        pass

    results: List[Tuple[str, str]] = []
    links = driver.find_elements(By.CSS_SELECTOR, "a.ext")
    log.info("1FF RFEF: jornada %s, enlaces 'a.ext' encontrados: %s", journey, len(links))
    for a in links:
        href = a.get_attribute("href") or ""
        if not href:
            continue
        full_url = href if href.startswith("http") else urljoin("https://resultados.rfef.es", href)
        parsed = urlparse(full_url)
        qs = parse_qs(parsed.query)

        acta_id = ""
        # Aceptar varias variantes de parámetro de acta
        for key in ("CodActa", "codActa", "cod_acta", "codacta"):
            vals = qs.get(key)
            if vals and vals[0]:
                acta_id = vals[0]
                break

        if not acta_id:
            # Fallback: intentar extraer un id de acta de la propia URL
            m = re.search(r"[?&]acta=(\d+)", href)
            if not m:
                m = re.search(r"/acta/(\d+)", parsed.path)
            if m:
                acta_id = m.group(1)

        if not acta_id:
            continue

        results.append((acta_id, full_url))

    if not results:
        # En jornadas sin actas detectadas, dejamos trazas con ejemplos de href para depurar cambios de RFEF
        sample_hrefs = [a.get_attribute("href") or "" for a in links][:10]
        log.info("1FF RFEF: jornada %s sin actas detectadas. Ejemplos de href: %s", journey, sample_hrefs)

    log.info("1FF RFEF: jornada %s, actas de partido detectadas: %s", journey, len(results))
    return results


def fetch_rfef_matches_for_jornada_1ff(driver, journey: int) -> List[Dict[str, str]]:
    url = RFEF_RESULTADOS_URL_TEMPLATE.format(
        season=RFEF_1FF_SEASON,
        competition=RFEF_1FF_COMPETITION,
        group=RFEF_1FF_GROUP,
        journey=journey,
    )
    log.info("1FF RFEF: cargando jornada %s (matches): %s", journey, url)
    driver.get(url)

    try:
        WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//button[contains(., 'Aceptar') or contains(., 'Aceptar todas') or contains(., 'Aceptar todo') or contains(., 'Aceptar cookies') ]",
                )
            )
        ).click()
    except Exception:
        pass

    try:
        WebDriverWait(driver, 30).until(
            EC.presence_of_all_elements_located(
                (
                    By.CSS_SELECTOR,
                    "main section div.row.mx-auto.align-items-stretch.p-4[role='region']",
                )
            )
        )
    except Exception:
        pass

    soup = BeautifulSoup(driver.page_source, "lxml")
    blocks = soup.select("main section div.row.mx-auto.align-items-stretch.p-4[role='region']")
    out: List[Dict[str, str]] = []

    for block in blocks:
        local_name = ""
        visit_name = ""
        gl = ""
        gv = ""
        acta_link = ""
        acta_id = ""

        imgs = [img for img in block.select("img") if img.get("alt")]
        if imgs:
            local_name = imgs[0]["alt"].strip()
        if len(imgs) >= 2:
            visit_name = imgs[1]["alt"].strip()

        score_node = block.select_one("div.text-lg")
        if score_node:
            score_txt = score_node.get_text(" ", strip=True)
            m_sc = re.search(r"(\d+)\s*-\s*(\d+)", score_txt)
            if m_sc:
                gl = m_sc.group(1)
                gv = m_sc.group(2)

        a_acta = block.select_one("a.ext[href]")
        if a_acta and a_acta.get("href"):
            href = a_acta["href"].strip()
            acta_link = href if href.startswith("http") else urljoin("https://resultados.rfef.es", href)
            qs = parse_qs(urlparse(acta_link).query)
            acta_id = qs.get("CodActa", [""])[0] or qs.get("cod_acta", [""])[0]

        out.append(
            {
                "local_name": local_name,
                "visit_name": visit_name,
                "goles_local": gl,
                "goles_visit": gv,
                "acta_id": acta_id,
                "acta_link": acta_link,
                "norm_local": _normalize_team_name_for_match(local_name),
                "norm_visit": _normalize_team_name_for_match(visit_name),
            }
        )

    log.info("1FF RFEF: jornada %s, partidos detectados en resultados RFEF: %s", journey, len(out))
    return out


def _assign_rfef_actas_by_fuzzy(
    rows_j: List[Dict[str, str]],
    rfef_matches: List[Dict[str, str]],
    hoja: str,
    jornada: int,
) -> None:
    used = set()
    for idx, row in enumerate(rows_j):
        nom_loc = row.get("nombre_local", "")
        nom_vis = row.get("nombre_visitante", "")
        if not nom_loc and not nom_vis:
            continue
        gl = (row.get("goles_equipo_local") or "").strip()
        gv = (row.get("goles_equipo_visitante") or "").strip()
        nl = _normalize_team_name_for_match(nom_loc)
        nv = _normalize_team_name_for_match(nom_vis)

        best_score = 0.0
        best_k = -1
        for k, m in enumerate(rfef_matches):
            if k in used:
                continue
            m_gl = (m.get("goles_local") or "").strip()
            m_gv = (m.get("goles_visit") or "").strip()
            if gl and gv and (m_gl != gl or m_gv != gv):
                continue
            rl = m.get("norm_local", "")
            rv = m.get("norm_visit", "")
            s1 = SequenceMatcher(None, nl, rl).ratio()
            s2 = SequenceMatcher(None, nv, rv).ratio()
            score_direct = (s1 + s2) / 2.0
            s1_sw = SequenceMatcher(None, nl, rv).ratio()
            s2_sw = SequenceMatcher(None, nv, rl).ratio()
            score_swapped = (s1_sw + s2_sw) / 2.0
            score = score_direct if score_direct >= score_swapped else score_swapped
            if score > best_score:
                best_score = score
                best_k = k

        if best_k >= 0:
            m = rfef_matches[best_k]
            used.add(best_k)
            row["acta_id"] = m.get("acta_id", "")
            row["acta_link"] = m.get("acta_link", "")
            log.debug(
                "%s J%s partido %s: match RFEF score=%.3f, %s vs %s",
                hoja,
                jornada,
                idx + 1,
                best_score,
                nom_loc,
                nom_vis,
            )
        else:
            log.debug(
                "%s J%s partido %s: sin match RFEF fiable (score_max=%.3f)",
                hoja,
                jornada,
                idx + 1,
                best_score,
            )

    log.info(
        "1FF J%s: partidos intranet=%s, actas RFEF asignadas=%s",
        jornada,
        len(rows_j),
        len(used),
    )


def _test_rfef_1ff(session: requests.Session) -> None:
    options = uc.ChromeOptions()
    driver = uc.Chrome(options=options, version_main=RFEF_CHROME_VERSION_MAIN)
    try:
        meta = COMPETITIONS["1FF"]
        jornadas = meta["jornadas"]
        for j in range(1, jornadas + 1):
            try:
                acts = fetch_rfef_actas_for_jornada_1ff(driver, j)
            except Exception as exc:
                log.warning("Error obteniendo actas RFEF para jornada %s: %s", j, exc)
                continue
            print(f"Jornada {j}: {len(acts)} actas")
            for acta_id, acta_link in acts:
                print(f"  {acta_id} -> {acta_link}")
        try:
            input("Pulsa Enter para cerrar el navegador de RFEF...")
        except EOFError:
            pass
    finally:
        driver.quit()


def extract_rows_from_html(html: str, nombre: str, comp_id: int, temp_id: int, jornada: int) -> List[Dict[str, str]]:
    soup = BeautifulSoup(html, "lxml")
    out: List[Dict[str, str]] = []
    for block in soup.select(MATCH_BLOCK_SELECTOR):
        tds = _get_first_row_tds(block)
        if len(tds) < 3:
            continue
        td_local, td_center, td_visit = tds[0], tds[1], tds[2]
        nom_loc, id_loc, url_loc, esc_loc = _parse_team_cell(td_local)
        nom_vis, id_vis, url_vis, esc_vis = _parse_team_cell(td_visit)
        gl, gv, fecha, hora = _parse_center_info(td_center)
        campo, tipo_hierba, arbitro = _parse_info_cells(block)
        a_acta = block.select_one(ACTA_BTN_SELECTOR)
        acta_id = ""
        acta_link = ""
        if a_acta and a_acta.get("href"):
            acta_link = urljoin(BASE_URL, a_acta["href"])
            qs = parse_qs(urlparse(acta_link).query)
            acta_id = qs.get("CodActa", [""])[0] or qs.get("cod_acta", [""])[0]
        out.append(
            {
                "jornada": jornada,
                "id_equipo_local": id_loc,
                "id_equipo_visitante": id_vis,
                "goles_equipo_local": gl,
                "goles_equipo_visitante": gv,
                "fecha": fecha,
                "hora": hora,
                "campo": campo,
                "tipo_hierba": tipo_hierba,
                "arbitro": arbitro,
                "acta_id": acta_id,
                "acta_link": acta_link,
                "nombre_local": nom_loc,
                "nombre_visitante": nom_vis,
            }
        )
    return out


def scrape_competition(session: requests.Session, hoja: str, meta: Dict, rfef_driver=None) -> pd.DataFrame:
    nombre = meta["competicion_nombre"]
    comp_id = meta["competicion_id"]
    grupo_id = meta["grupo_id"]
    temp_id = meta["temporada_id"]
    jornadas = meta["jornadas"]
    max_jornadas = jornadas
    if FAST_DEBUG_JORNADAS_MAX is not None and FAST_DEBUG_JORNADAS_MAX > 0:
        max_jornadas = min(max_jornadas, FAST_DEBUG_JORNADAS_MAX)

    rows: List[Dict[str, str]] = []
    for j in range(1, max_jornadas + 1):
        url = JORNADA_URL_TEMPLATE.format(competicion_id=comp_id, grupo_id=grupo_id, temporada_id=temp_id, j=j)
        log.info("%s J%s: URL jornada intranet: %s", hoja, j, url)
        for intento in range(3):
            try:
                r = session.get(url, headers=HTTP_HEADERS, timeout=30)
                if is_login_page(r.text):
                    if not login(session, url):
                        raise RuntimeError("No se pudo reautenticar")
                    r = session.get(url, headers=HTTP_HEADERS, timeout=30)
                r.raise_for_status()

                rows_j = extract_rows_from_html(r.text, nombre, comp_id, temp_id, j)

                if nombre == "1FF" and rfef_driver is not None:
                    log.info("1FF J%s: intentando emparejar actas desde RFEF por nombres/resultados...", j)
                    try:
                        rfef_matches = fetch_rfef_matches_for_jornada_1ff(rfef_driver, j)
                    except Exception as exc:
                        log.warning("1FF J%s: error obteniendo partidos RFEF: %s", j, exc)
                        rfef_matches = []
                    if rfef_matches:
                        _assign_rfef_actas_by_fuzzy(rows_j, rfef_matches, hoja, j)

                rows.extend(rows_j)
                partidos_count = max(0, r.text.count('class="portlet-body body_fed"'))
                log.info("%s J%s: %s, partidos: %s", hoja, j, r.status_code, partidos_count)
                break
            except Exception as exc:
                wait = 1.5 * (intento + 1)
                log.warning("%s J%s: intento %s fallido (%s). Reintentando en %.1fs...", hoja, j, intento + 1, exc, wait)
                time.sleep(wait)
        else:
            log.error("%s J%s: sin datos tras reintentos", hoja, j)

    columns = [
        "jornada",
        "id_equipo_local",
        "id_equipo_visitante",
        "goles_equipo_local",
        "goles_equipo_visitante",
        "fecha",
        "hora",
        "campo",
        "tipo_hierba",
        "arbitro",
        "acta_id",
        "acta_link",
    ]
    df = pd.DataFrame(rows, columns=columns)
    return df


def build_jornadas_df(session: requests.Session) -> pd.DataFrame:
    """Scrapea todas las jornadas y devuelve el DataFrame en memoria.

    No escribe CSV ni tablas SQL; eso se hace en la capa orquestadora (main o run).
    """

    if FAST_TEST_JORNADAS_1FF:
        _test_rfef_1ff(session)
        # En modo test rápido no devolvemos datos útiles para el pipeline principal
        return pd.DataFrame(
            columns=[
                "jornada",
                "id_equipo_local",
                "id_equipo_visitante",
                "goles_equipo_local",
                "goles_equipo_visitante",
                "fecha",
                "hora",
                "campo",
                "tipo_hierba",
                "arbitro",
                "acta_id",
                "acta_link",
            ]
        )

    probe_meta = next(iter(COMPETITIONS.values()))
    probe_url = JORNADA_URL_TEMPLATE.format(
        competicion_id=probe_meta["competicion_id"],
        grupo_id=probe_meta["grupo_id"],
        temporada_id=probe_meta["temporada_id"],
        j=1,
    )
    if not login(session, probe_url):
        log.error("Login fallido o sin permisos en jornadas")
        raise SystemExit(2)

    dfs = []
    for hoja, meta in COMPETITIONS.items():
        log.info("Scrapeando %s - %s", hoja, meta["competicion_nombre"])
        if meta.get("competicion_nombre") == "1FF":
            # Crear un driver propio para 1FF y cerrarlo justo al terminar
            options = uc.ChromeOptions()
            options.add_argument("--disable-gpu")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_argument("--lang=es-ES")
            rfef_driver = uc.Chrome(
                options=options,
                version_main=RFEF_CHROME_VERSION_MAIN,
                use_subprocess=True,
            )
            try:
                df = scrape_competition(session, hoja, meta, rfef_driver=rfef_driver)
            finally:
                rfef_driver.quit()
        else:
            df = scrape_competition(session, hoja, meta, rfef_driver=None)
        dfs.append(df)

    if dfs:
        all_df = pd.concat(dfs, ignore_index=True)
    else:
        all_df = pd.DataFrame(
            columns=[
                "jornada",
                "id_equipo_local",
                "id_equipo_visitante",
                "goles_equipo_local",
                "goles_equipo_visitante",
                "fecha",
                "hora",
                "campo",
                "tipo_hierba",
                "arbitro",
                "acta_id",
                "acta_link",
            ]
        )

    return all_df


def run() -> None:
    with create_session() as session:
        all_df = build_jornadas_df(session)

    OUTPUT_JORNADAS_CSV.parent.mkdir(parents=True, exist_ok=True)
    all_df.to_csv(OUTPUT_JORNADAS_CSV, index=False, encoding="utf-8-sig")

    with ENGINE.begin() as conn:
        all_df.to_sql("jornadas", con=conn, if_exists="replace", index=False)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    run()
