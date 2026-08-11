#!/usr/bin/env python3
import json, re, sys, unicodedata
from pathlib import Path
import requests
from bs4 import BeautifulSoup

BASE="https://www.fantacalcio.it"
HERE=Path(__file__).resolve().parent
MANIFEST=HERE/"players_manifest.json"
OUT=HERE/"diagnostica.json"
HEADERS={
 "User-Agent":"Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1",
 "Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
 "Accept-Language":"it-IT,it;q=0.9",
}

def slugify(v):
    v=unicodedata.normalize("NFKD",v or "")
    v="".join(c for c in v if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+","-",v.lower()).strip("-")

def candidate_urls(p,season):
    info=p.get("seasons",{}).get(season,{})
    team=slugify(info.get("team",""))
    name=slugify(p.get("name",""))
    last=slugify((p.get("name","").split() or ["giocatore"])[-1])
    pid=p["id"]; s=season.replace("/","-")
    return list(dict.fromkeys([
      f"{BASE}/serie-a/squadre/{team}/{name}/{pid}/{s}",
      f"{BASE}/serie-a/squadre/{team}/{name}/{pid}/{s}/italia",
      f"{BASE}/serie-a/squadre/{team}/{last}/{pid}/{s}",
      f"{BASE}/serie-a/squadre/{team}/{last}/{pid}/{s}/italia",
    ]))

def main():
    season=sys.argv[1] if len(sys.argv)>1 else "2025/26"
    players=json.loads(MANIFEST.read_text(encoding="utf-8"))
    p=next(x for x in players if season in x.get("seasons",{}))
    report={"player":p["name"],"player_id":p["id"],"season":season,"attempts":[]}
    sess=requests.Session()

    for url in candidate_urls(p,season):
        item={"url":url}
        try:
            r=sess.get(url,headers=HEADERS,timeout=30,allow_redirects=True)
            item.update(status=r.status_code,final_url=r.url,length=len(r.text),
                        content_type=r.headers.get("content-type",""))
            low=r.text.lower()
            item["keywords"]={k:(k in low) for k in ["giornata","fantavoto","bonus","malus","entrato","uscito"]}
            soup=BeautifulSoup(r.text,"lxml")
            item["title"]=soup.title.get_text(" ",strip=True) if soup.title else None
            tables=soup.find_all("table")
            item["table_count"]=len(tables)
            item["tables"]=[]
            for ti,t in enumerate(tables[:20]):
                headers=[re.sub(r"\s+"," ",x.get_text(" ",strip=True)) for x in t.find_all("th")]
                sample=[]
                for tr in t.find_all("tr")[:4]:
                    sample.append([re.sub(r"\s+"," ",x.get_text(" ",strip=True)) for x in tr.find_all(["th","td"])])
                item["tables"].append({"index":ti,"headers":headers,"sample":sample})
            hits=[]
            for si,s in enumerate(soup.find_all("script")):
                txt=s.string or s.get_text() or ""
                lowtxt=txt.lower()
                if any(k in lowtxt for k in ["giornata","fantavoto","bonusmalus","bonus_malus"]):
                    hits.append({"index":si,"length":len(txt),"preview":txt[:1800]})
            item["script_hits"]=hits[:10]
        except Exception as e:
            item["error"]=repr(e)
        report["attempts"].append(item)

    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False))

if __name__=="__main__":
    main()
