pip install flask requests werkzeug pillow pyzbar reportlab fpdf

sudo apt install libzbar0

To start:
python3 app.py

default admin: # default admin - admin123

Flask==2.3.3
Flask-Login==0.6.3
Werkzeug==2.3.6
requests==2.32.1
reportlab==4.0
pandas==2.1.0
PyPDF2==3.1.1
Pillow==10.0.0


Library.db will autogenerate on first start. You will need to generate a cert with OpenSSL and place it in the same directory as the app.py in order to use the camera scanning function, which will be blocked by default in Firefox and Chrome for obvious reasons.
