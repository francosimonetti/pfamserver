from flask import Flask
from flask_cache import Cache
from config import config
import os
from flask.json import JSONEncoder
from decimal import Decimal
from flask import send_from_directory


class ExtendedEncoder(JSONEncoder):

    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super(JSONEncoder, self).default(obj)


app = Flask(__name__)
app.config.update(config)
app.secret_key = config['SECRET_KEY']
app.json_encoder = ExtendedEncoder
cache = Cache(app, config={'CACHE_TYPE': 'simple'})


@app.route('/favicon.png')
@app.route('/favicon.ico')
def favicon():
    static_path = os.path.join(app.root_path, 'static/img')
    return send_from_directory(static_path, 'favicon.png',
                               mimetype='image/png')
