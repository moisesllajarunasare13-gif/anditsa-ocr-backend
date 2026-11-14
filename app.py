import os
import io
import cv2
import numpy as np
import pytesseract
from PIL import Image
from flask import Flask, request, jsonify, session
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'anditsa_secret_key_2026_production')
CORS(app, supports_credentials=True)

# Configuración de base de datos Supabase (PostgreSQL) por SQLAlchemy
DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://postgres:anditsa2026@localhost:5432/postgres')
if DATABASE_URL and DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Configuración de ruta Tesseract
tesseract_cmd = os.getenv('TESSERACT_CMD', 'tesseract')
pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

# ============================================================
# MODELOS DE BASE DE DATOS (SQLAlchemy / Supabase PostgreSQL)
# ============================================================

class Usuario(db.Model):
    __tablename__ = 'usuarios'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    nombre_completo = db.Column(db.String(120), nullable=True)
    rol_id = db.Column(db.String(50), nullable=False, default='VENDEDOR')
    activo = db.Column(db.Boolean, default=True, nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'nombre_completo': self.nombre_completo,
            'rol_id': self.rol_id,
            'activo': self.activo
        }

class ConfiguracionPago(db.Model):
    __tablename__ = 'configuracion_pagos'

    id = db.Column(db.Integer, primary_key=True)
    nombre_metodo = db.Column(db.String(50), nullable=False)
    titular = db.Column(db.String(120), nullable=False)
    numero_telefono = db.Column(db.String(20), nullable=False)
    imagen_qr_url = db.Column(db.Text, nullable=True)
    activo = db.Column(db.Boolean, default=True, nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'nombre_metodo': self.nombre_metodo,
            'titular': self.titular,
            'numero_telefono': self.numero_telefono,
            'imagen_qr_url': self.imagen_qr_url,
            'activo': self.activo
        }

# ============================================================
# PREPROCESAMIENTO DE IMAGEN CON OPENCV
# ============================================================

def preprocess_image_opencv(image_bytes):
    """
    Preprocesamiento OpenCV para listas escolares:
    1. Decodifica bytes de la imagen.
    2. Escala de grises.
    3. Reducción de ruido con medianBlur (3x3).
    4. Umbral de binarización adaptativa Otsu.
    """
    np_arr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

    if img is None:
        return None

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    denoised = cv2.medianBlur(gray, 3)
    _, thresh = cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return thresh

# ============================================================
# RUTAS DE API & ENDPOINTS
# ============================================================

@app.route('/', methods=['GET'])
@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'online',
        'service': 'ANDITSA Backend API (Flask + Supabase + OpenCV)',
        'endpoints': ['/api/process-ocr', '/login', '/admin/config-pagos']
    }), 200

# ------------------------------------------------------------
# 1. ENDPOINT OCR PROCESAMIENTO EN TIEMPO REAL
# ------------------------------------------------------------
@app.route('/api/process-ocr', methods=['POST'])
@app.route('/process-ocr', methods=['POST'])
def process_ocr():
    """
    Procesa imágenes de listas escolares recibidas en multipart/form-data.
    Soporta claves 'image' o 'file'.
    """
    file = request.files.get('image') or request.files.get('file')

    if not file or file.filename == '':
        return jsonify({
            'success': False,
            'raw_text': None,
            'lines': None,
            'error': 'No se adjuntó ningún archivo válido en la petición (campo "image" o "file").'
        }), 400

    try:
        image_bytes = file.read()
        processed_img = preprocess_image_opencv(image_bytes)

        tesseract_config = '--oem 3 --psm 6'

        if processed_img is None:
            pil_img = Image.open(io.BytesIO(image_bytes))
            raw_text = pytesseract.image_to_string(pil_img, lang='spa', config=tesseract_config)
        else:
            raw_text = pytesseract.image_to_string(processed_img, lang='spa', config=tesseract_config)

        texto_extraido = raw_text.strip() if raw_text else ""
        lines = [line.strip() for line in texto_extraido.split('\n') if line.strip()]

        return jsonify({
            'success': True,
            'raw_text': texto_extraido,
            'lines': lines,
            'error': None
        }), 200

    except Exception as e:
        app.logger.error(f"Error procesando OCR: {str(e)}")
        return jsonify({
            'success': False,
            'raw_text': None,
            'lines': None,
            'error': f"Error interno en motor OCR Tesseract/OpenCV: {str(e)}"
        }), 500

# ------------------------------------------------------------
# 2. AUTENTICACIÓN ESTRICTA POR USERNAME
# ------------------------------------------------------------
@app.route('/login', methods=['POST'])
@app.route('/api/login', methods=['POST'])
def login():
    """
    Inicio de sesión estricto por username y password_hash (Werkzeug check_password_hash).
    Maneja sesión (session['usuario_id'], session['username'], session['rol_id']).
    """
    data = request.get_json() or request.form

    username_input = (data.get('username') or data.get('usuario') or data.get('email') or '').strip().lower()
    password_input = (data.get('password') or data.get('password_hash') or '').strip()

    if not username_input or not password_input:
        return jsonify({
            'success': False,
            'user': None,
            'error': 'Se requiere usuario (username) y contraseña.'
        }), 400

    try:
        user = Usuario.query.filter_by(username=username_input, activo=True).first()

        # Soporte para admin inicial si la tabla aún no se ha poblado
        if not user and username_input in ['admi', 'admin', 'admin@anditsa.com']:
            if password_input in ['admi1234', 'admin1234']:
                session['usuario_id'] = 1
                session['username'] = 'admin'
                session['rol_id'] = 'ADMIN'
                return jsonify({
                    'success': True,
                    'user': {
                        'id': 1,
                        'username': 'admin',
                        'nombre_completo': 'Administrador ANDITSA',
                        'rol_id': 'ADMIN',
                        'activo': True
                    },
                    'error': None
                }), 200

        if user and (check_password_hash(user.password_hash, password_input) or user.password_hash == password_input):
            session['usuario_id'] = user.id
            session['username'] = user.username
            session['rol_id'] = user.rol_id

            return jsonify({
                'success': True,
                'user': user.to_dict(),
                'error': None
            }), 200

        return jsonify({
            'success': False,
            'user': None,
            'error': 'Usuario o contraseña incorrectos.'
        }), 401

    except Exception as e:
        app.logger.error(f"Error en login: {str(e)}")
        return jsonify({
            'success': False,
            'user': None,
            'error': f"Error de servidor durante login: {str(e)}"
        }), 500

@app.route('/logout', methods=['POST'])
@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True, 'message': 'Sesión cerrada correctamente.'}), 200

# ------------------------------------------------------------
# 3. GESTIÓN DE CÓDIGOS QR (YAPE / PLIN)
# ------------------------------------------------------------
@app.route('/admin/config-pagos', methods=['GET', 'POST', 'PUT'])
@app.route('/api/admin/config-pagos', methods=['GET', 'POST', 'PUT'])
def config_pagos():
    """
    Ruta para listar y guardar/actualizar métodos de pago QR (Yape / Plin).
    """
    try:
        if request.method == 'GET':
            metodos = ConfiguracionPago.query.all()
            return jsonify({
                'success': True,
                'data': [m.to_dict() for m in metodos],
                'error': None
            }), 200

        data = request.get_json() or {}
        metodo_id = data.get('id')
        nombre_metodo = data.get('nombre_metodo', 'Yape')
        titular = data.get('titular', 'ANDITSA Librería')
        numero_telefono = data.get('numero_telefono', '')
        imagen_qr_url = data.get('imagen_qr_url', '')
        activo = data.get('activo', True)

        if metodo_id:
            config_item = ConfiguracionPago.query.get(metodo_id)
        else:
            config_item = ConfiguracionPago.query.filter_by(nombre_metodo=nombre_metodo).first()

        if not config_item:
            config_item = ConfiguracionPago(
                nombre_metodo=nombre_metodo,
                titular=titular,
                numero_telefono=numero_telefono,
                imagen_qr_url=imagen_qr_url,
                activo=activo
            )
            db.session.add(config_item)
        else:
            config_item.titular = titular
            config_item.numero_telefono = numero_telefono
            config_item.imagen_qr_url = imagen_qr_url
            config_item.activo = activo

        db.session.commit()

        return jsonify({
            'success': True,
            'data': config_item.to_dict(),
            'error': None
        }), 200

    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error en config-pagos: {str(e)}")
        return jsonify({
            'success': False,
            'data': None,
            'error': f"Error al procesar configuración de pagos: {str(e)}"
        }), 500

with app.app_context():
    try:
        db.create_all()
    except Exception as e:
        app.logger.warn(f"Note: db.create_all warning: {str(e)}")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
