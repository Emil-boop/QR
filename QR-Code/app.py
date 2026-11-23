import io
import os
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import segno
from flask import (
    Flask,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from werkzeug.utils import secure_filename


APP_DIR = Path(__file__).resolve().parent
DATABASE_PATH = Path(
    os.environ.get("DATABASE_PATH", APP_DIR / "bibliothek_neu.db")
).expanduser()
QR_CODE_DIR = Path(
    os.environ.get("QR_CODE_DIR", APP_DIR / "erstellte_qr_codes")
).expanduser()
QR_BACKGROUNDS = {
    "Weiß": "#FFFFFF",
    "Hellblau": "#DCEEFF",
    "Creme": "#FFF4CC",
    "Hellgrün": "#DDF5E1",
    "Rosa": "#FFE0E6",
    "Lavendel": "#E9DFFF",
    "Hellgrau": "#EEEEEE",
    "Mint": "#D8F3EA",
    "Apricot": "#FFE1C2",
    "Hellgelb": "#FFF9B0",
}

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024


def connect_db():
    connection = sqlite3.connect(str(DATABASE_PATH), timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def get_db():
    if "database" not in g:
        g.database = connect_db()
    return g.database


@app.teardown_appcontext
def close_db(_error=None):
    database = g.pop("database", None)
    if database is not None:
        database.close()


@app.get("/health")
def health():
    try:
        get_db().execute("SELECT 1").fetchone()
        return {"status": "ok"}, 200
    except sqlite3.Error:
        return {"status": "error"}, 503


def init_db():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(DATABASE_PATH)) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS buecher (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                isbn TEXT NOT NULL,
                titel TEXT NOT NULL,
                ausgeliehen INTEGER DEFAULT 0,
                regalstandort TEXT,
                ausleiher TEXT,
                rueckgabedatum TEXT
            )
            """
        )
        columns = {
            column[1]
            for column in connection.execute("PRAGMA table_info(buecher)")
        }
        if "ausleiher" not in columns:
            connection.execute("ALTER TABLE buecher ADD COLUMN ausleiher TEXT")
        if "rueckgabedatum" not in columns:
            connection.execute(
                "ALTER TABLE buecher ADD COLUMN rueckgabedatum TEXT"
            )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS qr_scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                qr_inhalt TEXT NOT NULL,
                isbn TEXT,
                titel TEXT,
                regalstandort TEXT,
                erfasst_am TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def format_date(value):
    if not value:
        return "-"
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return value


def find_book_for_qr(database, qr_content):
    if qr_content.isdigit():
        book = database.execute(
            """
            SELECT id, isbn, titel, ausgeliehen, regalstandort,
                   ausleiher, rueckgabedatum
            FROM buecher WHERE id = ?
            """,
            (int(qr_content),),
        ).fetchone()
        if book is not None:
            return book

    return database.execute(
        """
        SELECT id, isbn, titel, ausgeliehen, regalstandort,
               ausleiher, rueckgabedatum
        FROM buecher
        WHERE isbn = ? OR titel = ?
        ORDER BY id
        LIMIT 1
        """,
        (qr_content, qr_content),
    ).fetchone()


def save_scan(qr_content):
    database = get_db()
    book = find_book_for_qr(database, qr_content)
    isbn = book["isbn"] if book else None
    title = book["titel"] if book else None
    shelf = book["regalstandort"] if book else None
    cursor = database.execute(
        """
        INSERT INTO qr_scans (qr_inhalt, isbn, titel, regalstandort)
        VALUES (?, ?, ?, ?)
        """,
        (qr_content, isbn, title, shelf),
    )
    database.commit()
    return cursor.lastrowid


def book_action_redirect():
    scan_id = request.form.get("scan_id", "").strip()
    if scan_id.isdigit():
        return redirect(url_for("scans", scan_id=int(scan_id)))
    return redirect(url_for("depot"))


@app.get("/")
def index():
    query = request.args.get("q", "").strip()
    database = get_db()
    statistics = database.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN ausgeliehen = 1 THEN 1 ELSE 0 END) AS loaned
        FROM buecher
        """
    ).fetchone()
    books = []
    if query:
        pattern = f"%{query}%"
        books = database.execute(
            """
            SELECT * FROM buecher
            WHERE titel LIKE ? OR isbn LIKE ? OR regalstandort LIKE ?
                  OR CAST(id AS TEXT) LIKE ?
            ORDER BY titel, id
            """,
            (pattern, pattern, pattern, pattern),
        ).fetchall()
    return render_template(
        "index.html",
        query=query,
        books=books,
        total=statistics["total"] or 0,
        loaned=statistics["loaned"] or 0,
    )


@app.route("/qr-erstellen", methods=["GET", "POST"])
def create_qr():
    if request.method == "POST":
        title = request.form.get("titel", "").strip()
        isbn = request.form.get("isbn", "").strip()
        shelf = request.form.get("regal", "").strip()
        background_name = request.form.get("hintergrund", "Weiß")
        if not title or not isbn or not shelf:
            flash("Bitte Titel, ISBN und Regal ausfüllen.", "error")
            return render_template(
                "create.html", backgrounds=QR_BACKGROUNDS
            ), 400
        if len(title) > 200 or len(isbn) > 40 or len(shelf) > 120:
            flash("Eine Eingabe ist zu lang.", "error")
            return render_template(
                "create.html", backgrounds=QR_BACKGROUNDS
            ), 400
        if background_name not in QR_BACKGROUNDS:
            flash("Bitte eine gültige Hintergrundfarbe auswählen.", "error")
            return render_template(
                "create.html", backgrounds=QR_BACKGROUNDS
            ), 400

        image_path = None
        text_path = None
        try:
            database = get_db()
            with database:
                cursor = database.execute(
                    """
                    INSERT INTO buecher (isbn, titel, ausgeliehen, regalstandort)
                    VALUES (?, ?, 0, ?)
                    """,
                    (isbn, title, shelf),
                )
                book_id = cursor.lastrowid
                image_buffer = io.BytesIO()
                segno.make_qr(str(book_id)).save(
                    image_buffer,
                    kind="png",
                    scale=10,
                    light=QR_BACKGROUNDS[background_name],
                    dark="black",
                )
                QR_CODE_DIR.mkdir(parents=True, exist_ok=True)
                safe_title = secure_filename(title) or "qr-code"
                image_path = QR_CODE_DIR / f"{safe_title}_{book_id}.png"
                text_path = image_path.with_suffix(".txt")
                image_path.write_bytes(image_buffer.getvalue())
                text_path.write_text(str(book_id), encoding="utf-32")
        except (OSError, sqlite3.Error, ValueError) as error:
            for path in (image_path, text_path):
                if path is not None and path.exists():
                    path.unlink()
            flash(f"Der QR-Code konnte nicht erstellt werden: {error}", "error")
            return render_template(
                "create.html", backgrounds=QR_BACKGROUNDS
            ), 500

        flash(f"QR-Code für „{title}“ wurde erstellt.", "success")
        return render_template(
            "create.html",
            backgrounds=QR_BACKGROUNDS,
            created={
                "title": title,
                "book_id": book_id,
                "filename": image_path.name,
            },
        )

    return render_template("create.html", backgrounds=QR_BACKGROUNDS)


@app.get("/qr/<path:filename>")
def qr_file(filename):
    return send_from_directory(QR_CODE_DIR, filename, mimetype="image/png")


@app.post("/qr/<filename>/loeschen")
def delete_qr(filename):
    if Path(filename).name != filename or not filename.lower().endswith(".png"):
        flash("Ungültiger QR-Code-Dateiname.", "error")
        return redirect(url_for("gallery"))
    image_path = QR_CODE_DIR / filename
    text_path = image_path.with_suffix(".txt")
    try:
        image_path.unlink()
        if text_path.exists():
            text_path.unlink()
    except FileNotFoundError:
        flash("Der QR-Code wurde nicht gefunden.", "error")
    except OSError as error:
        flash(f"Der QR-Code konnte nicht gelöscht werden: {error}", "error")
    else:
        flash("QR-Code-Datei gelöscht. Der Bucheintrag bleibt erhalten.", "success")
    return redirect(url_for("gallery"))


@app.get("/galerie")
def gallery():
    QR_CODE_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(
        QR_CODE_DIR.glob("*.png"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    entries = []
    for path in files:
        content = "Information nicht verfügbar"
        text_path = path.with_suffix(".txt")
        if text_path.exists():
            try:
                content = text_path.read_text(encoding="utf-32").strip()
            except (OSError, UnicodeError) as error:
                content = f"Information konnte nicht gelesen werden: {error}"
        entries.append({"name": path.name, "content": content})
    return render_template("gallery.html", files=entries)


@app.route("/scannen", methods=["GET", "POST"])
def scan():
    if request.method == "POST":
        qr_content = request.form.get("qr_inhalt", "").strip()
        if not qr_content:
            flash("Bitte QR-Inhalt eingeben oder mit der Kamera scannen.", "error")
            return redirect(url_for("scan"))
        if len(qr_content) > 2000:
            flash("Der QR-Inhalt ist zu lang.", "error")
            return redirect(url_for("scan"))
        scan_id = save_scan(qr_content)
        flash("QR-Code wurde gespeichert und ausgewertet.", "success")
        return redirect(url_for("scans", scan_id=scan_id))
    return render_template("scan.html")


@app.post("/scannen/bild")
def scan_image():
    image = request.files.get("bild")
    if image is None or not image.filename:
        flash("Bitte ein Bild mit einem QR-Code auswählen.", "error")
        return redirect(url_for("scan"))

    image_bytes = image.read()
    if not image_bytes:
        flash("Die Bilddatei ist leer.", "error")
        return redirect(url_for("scan"))
    decoded_image = cv2.imdecode(
        np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR
    )
    if decoded_image is None:
        flash("Die Datei konnte nicht als Bild gelesen werden.", "error")
        return redirect(url_for("scan"))

    qr_content, _, _ = cv2.QRCodeDetector().detectAndDecode(decoded_image)
    if not qr_content:
        flash("Auf dem Bild wurde kein QR-Code erkannt.", "error")
        return redirect(url_for("scan"))
    scan_id = save_scan(qr_content.strip())
    flash("QR-Code wurde aus dem Bild ausgelesen.", "success")
    return redirect(url_for("scans", scan_id=scan_id))


@app.get("/qr-scans")
def scans():
    database = get_db()
    history = database.execute(
        """
        SELECT id, qr_inhalt, isbn, titel, regalstandort, erfasst_am
        FROM qr_scans ORDER BY id DESC LIMIT 100
        """
    ).fetchall()
    scan_id_text = request.args.get("scan_id", "")
    selected_scan = None
    matched_book = None
    if scan_id_text.isdigit():
        selected_scan = database.execute(
            "SELECT * FROM qr_scans WHERE id = ?", (int(scan_id_text),)
        ).fetchone()
        if selected_scan is not None:
            matched_book = find_book_for_qr(
                database, selected_scan["qr_inhalt"]
            )
    return render_template(
        "scans.html",
        scans=history,
        selected_scan=selected_scan,
        matched_book=matched_book,
        format_date=format_date,
    )


@app.get("/depot")
def depot():
    database = get_db()
    books = database.execute(
        "SELECT * FROM buecher ORDER BY titel, id"
    ).fetchall()
    total = len(books)
    loaned = sum(book["ausgeliehen"] for book in books)
    return render_template(
        "depot.html",
        books=books,
        total=total,
        available=total - loaned,
        loaned=loaned,
        format_date=format_date,
    )


@app.post("/buecher/<int:book_id>/ausleihen")
def loan_book(book_id):
    borrower = request.form.get("ausleiher", "").strip()
    return_date_text = request.form.get("rueckgabedatum", "").strip()
    if not borrower:
        flash("Bitte den Namen des Ausleihers eintragen.", "error")
        return book_action_redirect()
    try:
        return_date = datetime.strptime(
            return_date_text, "%Y-%m-%d"
        ).date().isoformat()
    except ValueError:
        flash("Bitte ein gültiges Rückgabedatum auswählen.", "error")
        return book_action_redirect()

    database = get_db()
    cursor = database.execute(
        """
        UPDATE buecher
        SET ausgeliehen = 1, ausleiher = ?, rueckgabedatum = ?
        WHERE id = ? AND ausgeliehen = 0
        """,
        (borrower, return_date, book_id),
    )
    database.commit()
    if cursor.rowcount != 1:
        flash("Das Buch ist nicht verfügbar oder wurde nicht gefunden.", "error")
    else:
        flash("Buch wurde ausgeliehen.", "success")
    return book_action_redirect()


@app.post("/buecher/<int:book_id>/zurueckgeben")
def return_book(book_id):
    database = get_db()
    cursor = database.execute(
        """
        UPDATE buecher
        SET ausgeliehen = 0, ausleiher = NULL, rueckgabedatum = NULL
        WHERE id = ? AND ausgeliehen = 1
        """,
        (book_id,),
    )
    database.commit()
    if cursor.rowcount != 1:
        flash("Das Buch ist nicht ausgeliehen oder wurde nicht gefunden.", "error")
    else:
        flash("Buch wurde zurückgegeben.", "success")
    return book_action_redirect()


@app.errorhandler(413)
def file_too_large(_error):
    flash("Die Bilddatei darf höchstens 8 MB groß sein.", "error")
    return redirect(url_for("scan"))


init_db()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
        debug=False,
    )
