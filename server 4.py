from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from pathlib import Path
import subprocess, sys, threading

app = Flask(__name__)

# Consenti le chiamate dal frontend Netlify e gestisci i preflight CORS.
CORS(
    app,
    resources={r"/api/*": {"origins": "*"}},
    allow_headers=["Content-Type"],
    methods=["GET", "POST", "OPTIONS"]
)

HERE = Path(__file__).resolve().parent
state = {"running": False, "message": "Pronto", "returncode": None}

@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    return response

def run_diag(season):
    state.update(
        running=True,
        message=f"Diagnostica {season} in corso…",
        returncode=None
    )
    try:
        p = subprocess.run(
            [sys.executable, str(HERE / "diagnostica.py"), season],
            cwd=HERE,
            capture_output=True,
            text=True,
            timeout=180
        )
        state["returncode"] = p.returncode
        if p.returncode == 0:
            state["message"] = "Diagnostica completata"
        else:
            state["message"] = "Errore diagnostica: " + (p.stderr[-1000:] or p.stdout[-1000:])
    except Exception as e:
        state["returncode"] = 1
        state["message"] = f"Errore: {e}"
    finally:
        state["running"] = False

@app.get("/")
def home():
    return jsonify({
        "ok": True,
        "service": "Fanta backend",
        "diagnostic_endpoint": "/api/diagnose"
    })

@app.get("/health")
def health():
    return jsonify({"ok": True})

@app.route("/api/status", methods=["GET", "OPTIONS"])
def status():
    if request.method == "OPTIONS":
        return ("", 204)
    return jsonify(state)

@app.route("/api/diagnose", methods=["POST", "OPTIONS"])
def diagnose():
    if request.method == "OPTIONS":
        return ("", 204)

    if state["running"]:
        return jsonify({
            "ok": False,
            "message": "Operazione già in corso"
        }), 409

    body = request.get_json(silent=True) or {}
    season = body.get("season", "2025/26")

    threading.Thread(
        target=run_diag,
        args=(season,),
        daemon=True
    ).start()

    return jsonify({
        "ok": True,
        "message": "Diagnostica avviata",
        "season": season
    })

@app.route("/api/diagnostic", methods=["GET", "OPTIONS"])
def diagnostic():
    if request.method == "OPTIONS":
        return ("", 204)

    p = HERE / "diagnostica.json"
    if not p.exists():
        return jsonify({
            "ok": False,
            "message": "Diagnostica non ancora disponibile"
        }), 404

    return send_file(p, mimetype="application/json")

@app.route("/api/data", methods=["GET", "OPTIONS"])
def data():
    if request.method == "OPTIONS":
        return ("", 204)

    p = HERE / "output" / "giornate_complete.json"
    if not p.exists():
        return jsonify([])

    return send_file(p, mimetype="application/json")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
