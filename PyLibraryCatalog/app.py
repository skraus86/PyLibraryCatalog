import os
import requests
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, send_file
from werkzeug.utils import secure_filename
from io import BytesIO
from fpdf import FPDF
import csv

app = Flask(__name__)
app.secret_key = "supersecretkey"

# Folder to save cover images
COVER_FOLDER = os.path.join(app.root_path, 'static', 'covers')
os.makedirs(COVER_FOLDER, exist_ok=True)

# In-memory data storage (replace with DB in production)
users = {"admin": {"password": "admin123", "approved": True}}
books = []
book_id_counter = 1

# -------------------------------
# Helper functions
# -------------------------------

def current_user():
    username = session.get("username")
    if username and username in users and users[username]['approved']:
        return username
    return None

@app.context_processor
def inject_user():
    return dict(current_user=current_user())

def fetch_book_info(isbn):
    """Fetch book info and cover from Google Books API"""
    url = f"https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}"
    try:
        res = requests.get(url, timeout=10)
        data = res.json()
        if 'items' not in data:
            return None
        info = data['items'][0]['volumeInfo']
        book = {
            'title': info.get('title', 'Unknown Title'),
            'authors': ", ".join(info.get('authors', [])) if 'authors' in info else "Unknown",
            'publisher': info.get('publisher', ''),
            'publishedDate': info.get('publishedDate', ''),
            'isbn': isbn,
            'in_library': True
        }
        # Cover
        image_links = info.get('imageLinks', {})
        if 'thumbnail' in image_links:
            img_url = image_links['thumbnail']
            ext = os.path.splitext(img_url)[1].split("?")[0] or ".jpg"
            filename = f"{isbn}{ext}"
            path = os.path.join(COVER_FOLDER, filename)
            try:
                r = requests.get(img_url, timeout=10)
                if r.status_code == 200:
                    with open(path, 'wb') as f:
                        f.write(r.content)
                    book['cover_url'] = url_for('static', filename=f'covers/{filename}')
            except Exception as e:
                print("Error downloading cover:", e)
        return book
    except Exception as e:
        print("Error fetching book info:", e)
        return None

# -------------------------------
# Routes
# -------------------------------

@app.route("/", methods=["GET", "POST"])
def index():
    global book_id_counter
    if not current_user():
        return redirect(url_for("login"))

    if request.method == "POST":
        isbn = request.form.get("isbn")
        if isbn:
            book = fetch_book_info(isbn)
            if book:
                book['id'] = book_id_counter
                book_id_counter += 1
                books.append(book)
                flash("Book added successfully!", "success")
            else:
                flash("Could not find book info for ISBN.", "danger")
        return redirect(url_for("index"))

    in_library = request.args.get('in_library')
    filter_in_library = bool(in_library)
    filtered_books = [b for b in books if b['in_library']] if filter_in_library else books
    return render_template("index.html", books=filtered_books, filter_in_library=filter_in_library)

@app.route("/toggle_in_library/<int:book_id>", methods=["POST"])
def toggle_in_library(book_id):
    for b in books:
        if b['id'] == book_id:
            b['in_library'] = not b['in_library']
            return jsonify(success=True)
    return jsonify(success=False, message="Book not found")

# -------------------------------
# User management
# -------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        if username in users and users[username]['approved'] and users[username]['password'] == password:
            session['username'] = username
            flash("Logged in successfully", "success")
            return redirect(url_for("index"))
        flash("Invalid credentials or not approved", "danger")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop('username', None)
    flash("Logged out", "success")
    return redirect(url_for("login"))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        if username in users:
            flash("Username already exists", "warning")
        else:
            users[username] = {"password": password, "approved": False}
            flash("Registration submitted, wait for admin approval", "info")
        return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/change_password", methods=["GET", "POST"])
def change_password():
    if not current_user():
        return redirect(url_for("login"))
    if request.method == "POST":
        new_pass = request.form.get("password")
        users[current_user()]['password'] = new_pass
        flash("Password changed successfully", "success")
        return redirect(url_for("index"))
    return render_template("change_password.html")

# -------------------------------
# Exports
# -------------------------------

@app.route("/export_csv")
def export_csv():
    if not current_user():
        flash("Login required", "warning")
        return redirect(url_for("login"))
    si = BytesIO()
    writer = csv.writer(si)
    writer.writerow(["Title", "Authors", "Publisher", "Published", "ISBN", "In Library"])
    for b in books:
        writer.writerow([b['title'], b['authors'], b['publisher'], b['publishedDate'], b['isbn'], b['in_library']])
    si.seek(0)
    return send_file(si, mimetype="text/csv", download_name="library.csv", as_attachment=True)

@app.route("/export_pdf")
def export_pdf():
    if not current_user():
        flash("Login required", "warning")
        return redirect(url_for("login"))
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, "Library", ln=True, align="C")
    pdf.ln(10)

    for b in books:
        pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 6, b['title'], ln=True)
        pdf.set_font("Arial", "", 11)
        pdf.cell(0, 5, f"Author(s): {b['authors']}", ln=True)
        pdf.cell(0, 5, f"ISBN: {b['isbn']}", ln=True)
        pdf.cell(0, 5, f"In Library: {b['in_library']}", ln=True)
        pdf.ln(2)
        # Add cover image if exists
        if 'cover_url' in b:
            try:
                # get local path
                cover_path = os.path.join(COVER_FOLDER, os.path.basename(b['cover_url']))
                pdf.image(cover_path, w=40)
                pdf.ln(5)
            except Exception as e:
                print("Error adding cover to PDF:", e)
        pdf.ln(5)
    pdf_output = BytesIO()
    pdf.output(pdf_output)
    pdf_output.seek(0)
    return send_file(pdf_output, mimetype="application/pdf", download_name="library.pdf", as_attachment=True)

# -------------------------------
# Run app
# -------------------------------
if __name__ == "__main__":
    app.run(ssl_context=('cert.pem', 'key.pem'), host='0.0.0.0', port=5000)
    #Ensure you generate cert for Flask to use, otherwise Camera function will not work
    