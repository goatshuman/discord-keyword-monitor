from flask import Flask
from threading import Thread

app = Flask(__name__)

@app.route('/')
def home():
    return '<h1>I am alive!</h1><p>Discord keyword monitor bot is running.</p>'

@app.route('/health')
def health():
    return {'status': 'alive', 'bot': 'running'}

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.daemon = True
    t.start()
