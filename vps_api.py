#!/usr/bin/env python3
from flask import Flask, request, jsonify
import subprocess, os

app = Flask(__name__)
API_KEY = "nextaccount2026"

def auth(req):
    return req.headers.get("X-API-Key") == API_KEY

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

@app.route("/read", methods=["POST"])
def read_file():
    if not auth(request): return jsonify({"error": "unauthorized"}), 401
    path = request.json.get("path")
    try:
        return jsonify({"content": open(path).read()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/run", methods=["POST"])
def run_cmd():
    if not auth(request): return jsonify({"error": "unauthorized"}), 401
    cmd = request.json.get("cmd")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
    return jsonify({"stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode})

@app.route("/write", methods=["POST"])
def write_file():
    if not auth(request): return jsonify({"error": "unauthorized"}), 401
    path = request.json.get("path")
    content = request.json.get("content")
    try:
        open(path, "w").write(content)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=37778)
