from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from pathlib import Path
import subprocess, sys, threading

app=Flask(__name__)
CORS(app,resources={r"/api/*":{"origins":["https://statuesque-malasada-ca2b49.netlify.app"]}})
HERE=Path(__file__).resolve().parent
state={"running":False,"message":"Pronto","returncode":None}

def run_diag(season):
    state.update(running=True,message=f"Diagnostica {season} in corso…",returncode=None)
    try:
        p=subprocess.run([sys.executable,str(HERE/"diagnostica.py"),season],
                         cwd=HERE,capture_output=True,text=True,timeout=180)
        state["returncode"]=p.returncode
        state["message"]="Diagnostica completata" if p.returncode==0 else "Errore: "+p.stderr[-700:]
    except Exception as e:
        state["returncode"]=1
        state["message"]=f"Errore: {e}"
    finally:
        state["running"]=False

@app.get("/health")
def health(): return {"ok":True}

@app.post("/api/diagnose")
def diagnose():
    if state["running"]:
        return jsonify({"ok":False,"message":"Operazione già in corso"}),409
    body=request.get_json(silent=True) or {}
    season=body.get("season","2025/26")
    threading.Thread(target=run_diag,args=(season,),daemon=True).start()
    return jsonify({"ok":True,"message":"Diagnostica avviata"})

@app.get("/api/status")
def status(): return jsonify(state)

@app.get("/api/diagnostic")
def diagnostic():
    p=HERE/"diagnostica.json"
    if not p.exists(): return jsonify({"message":"Diagnostica non ancora disponibile"}),404
    return send_file(p,mimetype="application/json")

@app.get("/api/data")
def data():
    p=HERE/"output"/"giornate_complete.json"
    if not p.exists(): return jsonify([])
    return send_file(p,mimetype="application/json")
