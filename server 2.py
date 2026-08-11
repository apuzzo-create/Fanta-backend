from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from pathlib import Path
import subprocess, sys, json, threading

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": [
    "https://statuesque-malasada-ca2b49.netlify.app"
]}})
HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "output"
state = {"running": False, "message": "Pronto", "returncode": None}

def run_crawler(season, limit):
    state.update(running=True, message=f"Recupero {season} in corso…", returncode=None)
    cmd = [sys.executable, str(HERE/"crawler.py"), "--season", season, "--delay", "1.8"]
    if limit:
        cmd += ["--limit", str(limit)]
    try:
        p = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True)
        state["returncode"] = p.returncode
        state["message"] = "Completato" if p.returncode == 0 else ("Errore: " + p.stderr[-500:])
    except Exception as e:
        state["returncode"] = 1
        state["message"] = f"Errore: {e}"
    finally:
        state["running"] = False

@app.get("/api/status")
def status():
    return jsonify(state)

@app.post("/api/start")
def start():
    if state["running"]:
        return jsonify({"ok": False, "message": "Recupero già in corso"}), 409
    body = request.get_json(silent=True) or {}
    season = body.get("season", "2025/26")
    limit = int(body.get("limit", 0) or 0)
    threading.Thread(target=run_crawler, args=(season, limit), daemon=True).start()
    return jsonify({"ok": True, "message": "Avviato"})

@app.get("/api/data")
def data():
    p = OUTPUT / "giornate_complete.json"
    if not p.exists():
        return jsonify([])
    return send_file(p, mimetype="application/json")

@app.get("/health")
def health():
    return {"ok": True}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
