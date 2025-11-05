import sqlite3
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file
import requests
import os
import csv
from io import BytesIO
from fpdf import FPDF
import pyotp
import qrcode
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = 'your_secret_key'

DB_PATH = 'library.db'
COVERS_DIR = 'static/covers'
os.makedirs(COVERS_DIR, exist_ok=True)

# ---------------- Database -----------------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    username TEXT UNIQUE,
                    password TEXT,
                    approved INTEGER DEFAULT 0,
                    mfa_secret TEXT
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS books (
                    id INTEGER PRIMARY KEY,
                    title TEXT,
                    authors TEXT,
                    publisher TEXT,
                    publishedDate TEXT,
                    isbn TEXT UNIQUE,
                    cover_url TEXT,
                    in_library INTEGER DEFAULT 1,
                    owner TEXT
                )''')
    # Default admin
    c.execute("SELECT * FROM users WHERE username='admin'")
    if not c.fetchone():
        c.execute("INSERT INTO users (username, password, approved) VALUES (?, ?, ?)",
                  ('admin', generate_password_hash('admin123'), 1))
    conn.commit()
    conn.close()

init_db()

# ---------------- Auth -----------------
def current_user():
    return session.get('username')

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user():
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if current_user() != 'admin':
            flash("Admin access required", "danger")
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated

# ---------------- Routes -----------------
@app.route("/")
@login_required
def index():
    conn = get_db()
    if current_user() == 'admin':
        books = conn.execute("SELECT * FROM books").fetchall()
    else:
        books = conn.execute("SELECT * FROM books WHERE owner=?", (current_user(),)).fetchall()
    conn.close()
    return render_template("index.html", books=books, current_user=current_user())

@app.route("/", methods=["POST"])
@login_required
def add_book():
    isbn = request.form.get("isbn")
    if not isbn:
        flash("ISBN is required", "danger")
        return redirect(url_for('index'))
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM books WHERE isbn=?", (isbn,))
    if c.fetchone():
        flash("Book already exists", "warning")
        conn.close()
        return redirect(url_for('index'))

    resp = requests.get(f'https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}')
    data = resp.json()
    if 'items' not in data:
        flash("Book not found via ISBN", "warning")
        conn.close()
        return redirect(url_for('index'))
    info = data['items'][0]['volumeInfo']
    title = info.get('title', '')
    authors = ", ".join(info.get('authors', []))
    publisher = info.get('publisher', '')
    publishedDate = info.get('publishedDate', '')
    cover_url = info.get('imageLinks', {}).get('thumbnail', '')

    local_cover = None
    if cover_url:
        r = requests.get(cover_url)
        if r.status_code == 200:
            filename = f"{isbn}.jpg"
            path = os.path.join(COVERS_DIR, filename)
            with open(path, 'wb') as f:
                f.write(r.content)
            local_cover = filename

    c.execute('''INSERT INTO books (title, authors, publisher, publishedDate, isbn, cover_url, owner)
                 VALUES (?, ?, ?, ?, ?, ?, ?)''',
              (title, authors, publisher, publishedDate, isbn, local_cover, current_user()))
    conn.commit()
    conn.close()
    flash("Book added successfully", "success")
    return redirect(url_for('index'))

@app.route("/toggle_in_library/<int:book_id>", methods=["POST"])
@login_required
def toggle_in_library(book_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT in_library FROM books WHERE id=?", (book_id,))
    book = c.fetchone()
    if not book:
        conn.close()
        return jsonify({"success": False, "message": "Book not found"})
    new_status = 0 if book['in_library'] else 1
    c.execute("UPDATE books SET in_library=? WHERE id=?", (new_status, book_id))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

# ---------------- User Auth -----------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method=="POST":
        username = request.form.get("username")
        password = request.form.get("password")
        token = request.form.get("token")
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        conn.close()
        if not user or not check_password_hash(user['password'], password):
            flash("Invalid credentials", "danger")
            return redirect(url_for('login'))
        if not user['approved']:
            flash("User not approved by admin", "warning")
            return redirect(url_for('login'))
        if user['mfa_secret']:
            if not token or not pyotp.TOTP(user['mfa_secret']).verify(token):
                flash("Invalid MFA token", "danger")
                return redirect(url_for('login'))
        session['username'] = username
        flash("Login successful", "success")
        return redirect(url_for('index'))
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out", "success")
    return redirect(url_for('login'))

# ---------------- Registration -----------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        if not username or not password:
            flash("Username and password are required", "danger")
            return redirect(url_for('register'))

        conn = get_db()
        c = conn.cursor()
        if c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone():
            flash("Username already exists", "warning")
            conn.close()
            return redirect(url_for('register'))

        # New users pending admin approval
        c.execute("INSERT INTO users (username, password) VALUES (?, ?)",
                  (username, generate_password_hash(password)))
        conn.commit()
        conn.close()
        flash("Registration submitted! Wait for admin approval.", "success")
        return redirect(url_for('login'))

    return render_template("register.html")

# ---------------- Change Password -----------------
@app.route("/change_password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current_pass = request.form.get("current_password")
        new_pass = request.form.get("new_password")
        verify_pass = request.form.get("verify_password")

        if not current_pass or not new_pass or not verify_pass:
            flash("All fields are required", "danger")
            return redirect(url_for('change_password'))

        if new_pass != verify_pass:
            flash("New passwords do not match", "danger")
            return redirect(url_for('change_password'))

        conn = get_db()
        c = conn.cursor()
        user = c.execute("SELECT * FROM users WHERE username=?", (current_user(),)).fetchone()
        if not check_password_hash(user['password'], current_pass):
            flash("Current password incorrect", "danger")
            conn.close()
            return redirect(url_for('change_password'))

        c.execute("UPDATE users SET password=? WHERE username=?",
                  (generate_password_hash(new_pass), current_user()))
        conn.commit()
        conn.close()
        flash("Password changed successfully", "success")
        return redirect(url_for('index'))

    return render_template("change_password.html")

# ---------------- MFA Setup -----------------
@app.route("/setup_mfa", methods=["GET", "POST"])
@login_required
def setup_mfa():
    conn = get_db()
    c = conn.cursor()
    user = c.execute("SELECT * FROM users WHERE username=?", (current_user(),)).fetchone()

    if request.method == "POST":
        token = request.form.get("token")
        totp = pyotp.TOTP(user['mfa_secret'])
        if totp.verify(token):
            flash("MFA setup successful!", "success")
            return redirect(url_for('index'))
        else:
            flash("Invalid token. Try again.", "danger")
            return redirect(url_for('setup_mfa'))

    # Generate secret if not exists
    if not user['mfa_secret']:
        secret = pyotp.random_base32()
        c.execute("UPDATE users SET mfa_secret=? WHERE username=?", (secret, current_user()))
        conn.commit()
        user = c.execute("SELECT * FROM users WHERE username=?", (current_user(),)).fetchone()

    # Generate QR code URL
    totp_uri = pyotp.totp.TOTP(user['mfa_secret']).provisioning_uri(name=current_user(), issuer_name="PyLibraryCatalog")
    qr = qrcode.make(totp_uri)
    buf = BytesIO()
    qr.save(buf)
    buf.seek(0)
    return send_file(buf, mimetype="image/png")

# ---------------- Dark Mode -----------------
@app.route("/set_dark_mode", methods=["POST"])
def set_dark_mode():
    data = request.get_json()
    session['dark_mode'] = data.get('dark', False)
    return '', 204

# ---------------- Export -----------------
@app.route("/export_csv")
@login_required
def export_csv():
    conn = get_db()
    if current_user() == 'admin':
        books = conn.execute("SELECT * FROM books").fetchall()
    else:
        books = conn.execute("SELECT * FROM books WHERE owner=?", (current_user(),)).fetchall()
    conn.close()
    si = BytesIO()
    writer = csv.writer(si)
    writer.writerow(['Title','Authors','Publisher','Published','ISBN','In Library'])
    for b in books:
        writer.writerow([b['title'], b['authors'], b['publisher'], b['publishedDate'], b['isbn'], 'Yes' if b['in_library'] else 'No'])
    si.seek(0)
    return send_file(si, mimetype='text/csv', download_name='library.csv', as_attachment=True)

@app.route("/export_pdf")
@login_required
def export_pdf():
    conn = get_db()
    if current_user() == 'admin':
        books = conn.execute("SELECT * FROM books").fetchall()
    else:
        books = conn.execute("SELECT * FROM books WHERE owner=?", (current_user(),)).fetchall()
    conn.close()
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", "B", 14)
    pdf.cell(0, 10, "Library Catalog", ln=True, align='C')
    pdf.set_font("Arial", "", 12)
    for b in books:
        pdf.ln(5)
        pdf.multi_cell(0, 6, f"Title: {b['title']}\nAuthors: {b['authors']}\nPublisher: {b['publisher']}\nPublished: {b['publishedDate']}\nISBN: {b['isbn']}\nIn Library: {'Yes' if b['in_library'] else 'No'}")
    out = BytesIO()
    pdf.output(out)
    out.seek(0)
    return send_file(out, mimetype='application/pdf', download_name='library.pdf', as_attachment=True)

# ---------------- User Management -----------------
@app.route("/user_management")
@login_required
@admin_required
def user_management():
    conn = get_db()
    users = conn.execute("SELECT * FROM users").fetchall()
    conn.close()
    return render_template("user_management.html", users=users)

@app.route("/approve_user/<int:user_id>", methods=["POST"])
@login_required
@admin_required
def approve_user(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE users SET approved=1 WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route("/delete_user/<int:user_id>", methods=["POST"])
@login_required
@admin_required
def delete_user(user_id):
    conn = get_db()
    c = conn.cursor()
    user = c.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
    if user and user['username'] != 'admin':
        c.execute("DELETE FROM users WHERE id=?", (user_id,))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    conn.close()
    return jsonify({"success": False, "message": "Cannot delete admin"})

@app.route("/reset_password/<int:user_id>", methods=["POST"])
@login_required
@admin_required
def reset_password(user_id):
    new_password = request.form.get("new_password")
    if not new_password:
        return jsonify({"success": False, "message": "Password required"})
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE users SET password=? WHERE id=?", (generate_password_hash(new_password), user_id))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

# ---------------- Run App -----------------
if __name__ == "__main__":
    #app.run(debug=True)
    app.run(ssl_context=('cert.pem', 'key.pem'), host='0.0.0.0', port=5000)
