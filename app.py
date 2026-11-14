import os
import re
import cv2
import numpy as np
import pytesseract
from PIL import Image
from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# Configuración de Tesseract OCR
tesseract_cmd = os.environ.get("TESSERACT_CMD", "/usr/bin/tesseract")
if os.path.exists(tesseract_cmd):
    pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

# Configuración de Base de Datos PostgreSQL (Supabase)
database_url = os.environ.get("DATABASE_URL", "")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql+psycopg://", 1)
elif database_url.startswith("postgresql://") and "+psycopg" not in database_url:
    database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "anditsa_secret_key_2026_production")
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
}

db = SQLAlchemy(app)

# ==========================================
# MODELOS DE BASE DE DATOS (LIBRERÍA ANDITSA)
# ==========================================
class Usuario(db.Model):
    __tablename__ = "usuarios"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    nombre = db.Column(db.String(120), nullable=False)
    rol = db.Column(db.String(30), nullable=False, default="VENDEDOR")  # ADMIN, VENDEDOR, ALMACENERO

    def to_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "nombre": self.nombre,
            "rol": self.rol,
        }


class Producto(db.Model):
    __tablename__ = "productos"
    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(50), unique=True, nullable=True)
    nombre = db.Column(db.String(150), nullable=False)
    categoria = db.Column(db.String(80), nullable=False, default="Útiles Escolares")
    precio = db.Column(db.Float, nullable=False, default=0.0)
    stock = db.Column(db.Integer, nullable=False, default=0)

    def to_dict(self):
        return {
            "id": self.id,
            "codigo": self.codigo,
            "nombre": self.nombre,
            "categoria": self.categoria,
            "precio": self.precio,
            "stock": self.stock,
        }


with app.app_context():
    try:
        db.create_all()
    except Exception as e:
        print(f"Advertencia inicializando tablas: {e}")


# ==========================================
# ENDPOINTS PRINCIPALES Y SALUD
# ==========================================
@app.route("/", methods=["GET"])
@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({
        "status": "online",
        "service": "Librería Anditsa Backend + OCR",
        "tesseract": os.path.exists(tesseract_cmd),
    }), 200


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")

    if not username or not password:
        return jsonify({"error": "Usuario y contraseña requeridos"}), 400

    user = Usuario.query.filter_by(username=username).first()
    if user and (check_password_hash(user.password_hash, password) or user.password_hash == password):
        return jsonify({"message": "Login exitoso", "user": user.to_dict()}), 200

    return jsonify({"error": "Credenciales inválidas"}), 401


@app.route("/api/productos", methods=["GET"])
def listar_productos():
    productos = Producto.query.order_by(Producto.nombre.asc()).all()
    return jsonify([p.to_dict() for p in productos]), 200


@app.route("/api/productos", methods=["POST"])
def crear_producto():
    data = request.get_json(silent=True) or {}
    nuevo = Producto(
        codigo=data.get("codigo"),
        nombre=data.get("nombre", "Producto sin nombre"),
        categoria=data.get("categoria", "General"),
        precio=float(data.get("precio", 0)),
        stock=int(data.get("stock", 0)),
    )
    db.session.add(nuevo)
    db.session.commit()
    return jsonify(nuevo.to_dict()), 201


# ==========================================
# ENDPOINT OCR (OPENCV + TESSERACT)
# ==========================================
def procesar_imagen_ocr(file_bytes):
    np_arr = np.frombuffer(file_bytes, np.uint8)
    img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    if img is None:
        return ""

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    thresh = cv2.adaptiveThreshold(
        blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 10
    )

    config = "--oem 3 --psm 6"
    try:
        texto = pytesseract.image_to_string(thresh, lang="spa", config=config)
    except Exception:
        texto = pytesseract.image_to_string(thresh, config=config)
    return texto


@app.route("/api/ocr", methods=["POST"])
def escanear_lista():
    if "image" not in request.files and "file" not in request.files:
        return jsonify({"error": "No se envió ninguna imagen"}), 400

    archivo = request.files.get("image") or request.files.get("file")
    texto_extraido = procesar_imagen_ocr(archivo.read())

    lineas = [l.strip() for l in texto_extraido.splitlines() if len(l.strip()) > 2]
    productos_db = Producto.query.all()
    resultados = []

    for linea in lineas:
        match_cant = re.match(r"^(\d+)\s+(.+)$", linea)
        cantidad = int(match_cant.group(1)) if match_cant else 1
        descripcion = match_cant.group(2) if match_cant else linea

        coincidencia = None
        for prod in productos_db:
            if prod.nombre.lower() in descripcion.lower() or descripcion.lower() in prod.nombre.lower():
                coincidencia = prod.to_dict()
                break

        resultados.append({
            "texto_detectado": linea,
            "cantidad": cantidad,
            "descripcion": descripcion,
            "producto": coincidencia,
        })

    return jsonify({
        "texto_completo": texto_extraido,
        "items": resultados,
    }), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)