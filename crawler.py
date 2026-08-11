#!/usr/bin/env python3
"""
Fantacalcio giornata-per-giornata crawler
----------------------------------------
Crea un archivio:
calciatore -> stagione -> giornata -> Voto/FV/Entrato/Uscito/Bonus-Malus.

Caratteristiche:
- parte dagli ID già presenti nell'app;
- usa i team storici per costruire URL plausibili;
- prova più pattern URL ufficiali;
- individua la tabella per intestazioni, non per posizione;
- salva progressivamente in JSON Lines;
- cache HTML opzionale;
- resume automatico;
- ritardo configurabile tra richieste;
- esportazione finale JSON + CSV.

Uso:
  python crawler.py --season 2025/26 --limit 5
  python crawler.py --season 2025/26
  python crawler.py --all-seasons
  python crawler.py --all-seasons --delay 1.8

Dipendenze:
  pip install requests beautifulsoup4 pandas lxml
"""

from __future__ import annotations
import argparse
import csv
import html as html_lib
import json
import re
import sys
import time
import unicodedata
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

BASE = "https://www.fantacalcio.it"
HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "players_manifest.json"
OUTDIR = HERE / "output"
CACHE = OUTDIR / "html_cache"
JSONL = OUTDIR / "giornate.jsonl"
FINAL_JSON = OUTDIR / "giornate_complete.json"
FINAL_CSV = OUTDIR / "giornate_complete.csv"
ERRORS = OUTDIR / "errors.jsonl"

SEASONS = ["2020/21","2021/22","2022/23","2023/24","2024/25","2025/26","2026/27"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/127.0 Safari/537.36",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.7",
}

def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower().replace("'", " ").replace(".", " ")
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or "giocatore"

def season_dash(season: str) -> str:
    return season.replace("/", "-")

def candidate_urls(player: dict, season: str) -> list[str]:
    info = player.get("seasons", {}).get(season, {})
    team = slugify(info.get("team",""))
    name = slugify(player.get("name",""))
    pid = player["id"]
    seas = season_dash(season)

    # Fantacalcio has used more than one route shape over time.
    candidates = [
        f"{BASE}/serie-a/squadre/{team}/{name}/{pid}/{seas}/italia",
        f"{BASE}/serie-a/squadre/{team}/{name}/{pid}/{seas}",
        f"{BASE}/serie-a/squadre/giocatore/{name}/{pid}/{seas}/italia",
        f"{BASE}/serie-a/squadre/giocatore/{name}/{pid}/{seas}",
    ]

    # Some profile routes are ID-centric enough to tolerate shortened slugs.
    last = slugify((player.get("name","").split() or ["giocatore"])[-1])
    candidates += [
        f"{BASE}/serie-a/squadre/{team}/{last}/{pid}/{seas}/italia",
        f"{BASE}/serie-a/squadre/{team}/{last}/{pid}/{seas}",
    ]
    return list(dict.fromkeys(candidates))

def cache_path(pid: int, season: str) -> Path:
    return CACHE / f"{pid}_{season.replace('/','-')}.html"

def fetch_page(session: requests.Session, player: dict, season: str, delay: float, use_cache: bool=True):
    cp = cache_path(player["id"], season)
    if use_cache and cp.exists():
        return cp.read_text(encoding="utf-8", errors="ignore"), "cache"

    last_error = None
    for url in candidate_urls(player, season):
        try:
            r = session.get(url, headers=HEADERS, timeout=25, allow_redirects=True)
            if r.status_code == 200 and len(r.text) > 10000:
                # A valid profile should mention either the player or the daily table concept.
                low = r.text.lower()
                if ("giornata" in low and ("fantavoto" in low or ">fv<" in low or "bonus" in low)):
                    CACHE.mkdir(parents=True, exist_ok=True)
                    cp.write_text(r.text, encoding="utf-8")
                    time.sleep(delay)
                    return r.text, r.url
            last_error = f"{r.status_code} {r.url}"
        except requests.RequestException as e:
            last_error = repr(e)
        time.sleep(delay)

    raise RuntimeError(last_error or "nessun URL valido")

def flatten_col(c) -> str:
    if isinstance(c, tuple):
        c = " ".join(str(x) for x in c if str(x) != "nan")
    return re.sub(r"\s+", " ", str(c)).strip()

def norm_col(c: str) -> str:
    c = unicodedata.normalize("NFKD", c)
    c = "".join(ch for ch in c if not unicodedata.combining(ch))
    c = c.lower()
    c = re.sub(r"[^a-z0-9+/-]+", " ", c)
    return re.sub(r"\s+", " ", c).strip()

def find_daily_table(page_html: str) -> pd.DataFrame:
    # First route: pandas HTML table recognition.
    tables = pd.read_html(page_html, decimal=",", thousands=".")
    best = None
    best_score = -1
    for df in tables:
        cols = [norm_col(flatten_col(c)) for c in df.columns]
        joined = " | ".join(cols)
        score = 0
        if any("giornata" == c or c.startswith("giornata") for c in cols): score += 5
        if any(c in ("voto","v") or c.startswith("voto") for c in cols): score += 2
        if any(c in ("fv","fantavoto","fanta voto") or "fantavoto" in c for c in cols): score += 3
        if any("bonus" in c or "malus" in c for c in cols): score += 2
        if any("entrato" in c for c in cols): score += 1
        if any("uscito" in c for c in cols): score += 1
        if score > best_score:
            best_score, best = score, df

    if best is None or best_score < 7:
        raise ValueError(f"tabella giornata non identificata (score={best_score})")

    best.columns = [flatten_col(c) for c in best.columns]
    return best

def clean_scalar(v):
    if pd.isna(v):
        return None
    if isinstance(v, float):
        return round(v, 4)
    s = re.sub(r"\s+", " ", str(v)).strip()
    if not s or s.lower() in {"nan","nd","---","--","-"}:
        return None
    return s

def identify_column(cols: list[str], aliases: list[str]) -> str|None:
    norms = {c: norm_col(c) for c in cols}
    # exact first
    for c,n in norms.items():
        if n in aliases:
            return c
    # contains
    for c,n in norms.items():
        if any(a in n for a in aliases):
            return c
    return None

def parse_rows(df: pd.DataFrame, player: dict, season: str, source_url: str) -> list[dict]:
    cols = list(df.columns)
    c_day = identify_column(cols, ["giornata"])
    c_vote = identify_column(cols, ["voto"])
    c_fv = identify_column(cols, ["fv","fantavoto","fanta voto"])
    c_in = identify_column(cols, ["entrato"])
    c_out = identify_column(cols, ["uscito"])
    c_bonus = identify_column(cols, ["bonus/malus","bonus malus","bonus","malus"])
    c_match = identify_column(cols, ["partita","match","gara"])

    if c_day is None:
        raise ValueError("colonna Giornata non trovata")

    rows = []
    for _, row in df.iterrows():
        raw_day = clean_scalar(row.get(c_day))
        if raw_day is None:
            continue

        m = re.search(r"\b([1-9]|[12][0-9]|3[0-8])\b", str(raw_day))
        if not m:
            continue
        day = int(m.group(1))

        rec = {
            "player_id": player["id"],
            "player_name": player["name"],
            "season": season,
            "giornata": day,
            "team": player.get("seasons",{}).get(season,{}).get("team",""),
            "voto": clean_scalar(row.get(c_vote)) if c_vote else None,
            "fv": clean_scalar(row.get(c_fv)) if c_fv else None,
            "entrato": clean_scalar(row.get(c_in)) if c_in else None,
            "uscito": clean_scalar(row.get(c_out)) if c_out else None,
            "bonus_malus": clean_scalar(row.get(c_bonus)) if c_bonus else None,
            "partita": clean_scalar(row.get(c_match)) if c_match else None,
            "source_url": source_url,
        }

        # Preserve every raw table cell as well, so no information is lost.
        rec["raw"] = {str(c): clean_scalar(row.get(c)) for c in cols}
        rows.append(rec)

    # Deduplicate by giornata, keeping last occurrence.
    dedup = {r["giornata"]: r for r in rows}
    return [dedup[k] for k in sorted(dedup)]

def load_done() -> set[tuple[int,str]]:
    done = set()
    if not JSONL.exists():
        return done
    for line in JSONL.read_text(encoding="utf-8").splitlines():
        try:
            x = json.loads(line)
            if x.get("status") == "ok":
                done.add((int(x["player_id"]), x["season"]))
        except Exception:
            pass
    return done

def append_jsonl(path: Path, obj: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")

def consolidate():
    records = []
    if JSONL.exists():
        for line in JSONL.read_text(encoding="utf-8").splitlines():
            try:
                x = json.loads(line)
                if x.get("status") == "ok":
                    records.extend(x.get("rows", []))
            except Exception:
                pass

    records.sort(key=lambda r: (r["player_name"], r["season"], r["giornata"]))
    FINAL_JSON.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    fields = [
        "player_id","player_name","season","giornata","team",
        "partita","voto","fv","entrato","uscito","bonus_malus","source_url"
    ]
    with FINAL_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in records:
            w.writerow({k:r.get(k) for k in fields})

    return len(records)

def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--season", choices=SEASONS)
    g.add_argument("--all-seasons", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="limite giocatori, 0=tutti")
    ap.add_argument("--delay", type=float, default=1.5, help="secondi tra richieste")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--force", action="store_true", help="riscarica coppie già completate")
    args = ap.parse_args()

    players = json.loads(MANIFEST.read_text(encoding="utf-8"))
    seasons = SEASONS if args.all_seasons else [args.season]
    done = load_done()
    session = requests.Session()

    jobs = []
    for p in players:
        for season in seasons:
            if season not in p.get("seasons", {}):
                continue
            jobs.append((p, season))

    if args.limit:
        # Limit unique players, not player-season pairs.
        ids = []
        for p,_ in jobs:
            if p["id"] not in ids:
                ids.append(p["id"])
            if len(ids) >= args.limit:
                break
        keep = set(ids)
        jobs = [(p,s) for p,s in jobs if p["id"] in keep]

    print(f"Job totali: {len(jobs)}")
    ok = err = skipped = 0

    for i,(p,season) in enumerate(jobs,1):
        key=(p["id"],season)
        if key in done and not args.force:
            skipped += 1
            continue

        print(f"[{i}/{len(jobs)}] {p['name']} · {season}", flush=True)
        try:
            page, src = fetch_page(session, p, season, args.delay, not args.no_cache)
            df = find_daily_table(page)
            rows = parse_rows(df, p, season, src)

            # A Serie A season should normally expose up to 38 rounds.
            # Accept partial seasons, but require at least one valid round.
            if not rows:
                raise ValueError("0 giornate valide")

            append_jsonl(JSONL, {
                "status":"ok",
                "player_id":p["id"],
                "player_name":p["name"],
                "season":season,
                "source_url":src,
                "rows":rows,
            })
            ok += 1
            print(f"  OK: {len(rows)} giornate")
        except Exception as e:
            append_jsonl(ERRORS, {
                "status":"error",
                "player_id":p["id"],
                "player_name":p["name"],
                "season":season,
                "error":repr(e),
                "candidate_urls":candidate_urls(p,season),
            })
            err += 1
            print(f"  ERRORE: {e}")

    total_rows = consolidate()
    print()
    print(f"Completati: {ok} · saltati: {skipped} · errori: {err}")
    print(f"Record giornata consolidati: {total_rows}")
    print(f"JSON: {FINAL_JSON}")
    print(f"CSV : {FINAL_CSV}")
    print(f"Errori: {ERRORS}")

if __name__ == "__main__":
    main()
