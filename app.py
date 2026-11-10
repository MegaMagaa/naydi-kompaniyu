from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime
import sqlite3, os

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key-for-real-deployment")
DB_PATH = os.path.join(os.path.dirname(__file__), "app.db")

CATEGORIES = ["Игры", "Спорт", "Прогулки", "Совместная учёба", "Творчество", "Другое"]

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT NOT NULL,
            starts_at TEXT NOT NULL,
            format TEXT NOT NULL CHECK(format IN ('Онлайн', 'Очно')),
            place TEXT DEFAULT '',
            capacity INTEGER NOT NULL CHECK(capacity BETWEEN 2 AND 30),
            creator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            is_closed INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS participants (
            activity_id INTEGER NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            joined_at TEXT NOT NULL,
            PRIMARY KEY(activity_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            activity_id INTEGER NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """)
        # Keep the creator in the participant list for each activity.
        con.execute("""
            INSERT OR IGNORE INTO participants(activity_id, user_id, joined_at)
            SELECT id, creator_id, created_at FROM activities
        """)

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Сначала войдите в аккаунт или зарегистрируйтесь.", "info")
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper

def activity_with_counts(row, con):
    item = dict(row)
    item["participant_count"] = con.execute(
        "SELECT COUNT(*) FROM participants WHERE activity_id=?", (item["id"],)
    ).fetchone()[0]
    item["is_full"] = item["participant_count"] >= item["capacity"]
    item["is_joined"] = bool(session.get("user_id") and con.execute(
        "SELECT 1 FROM participants WHERE activity_id=? AND user_id=?",
        (item["id"], session["user_id"])
    ).fetchone())
    return item

@app.context_processor
def inject_globals():
    return {"current_user": session.get("username"), "categories": CATEGORIES}

@app.route("/")
def index():
    category = request.args.get("category", "")
    fmt = request.args.get("format", "")
    search = request.args.get("q", "").strip()
    with db() as con:
        sql = """SELECT a.*, u.username AS creator_name FROM activities a
                 JOIN users u ON u.id=a.creator_id
                 WHERE a.is_closed=0 AND a.starts_at >= ?"""
        params = [datetime.now().strftime("%Y-%m-%dT%H:%M")]
        if category in CATEGORIES:
            sql += " AND a.category=?"; params.append(category)
        if fmt in ("Онлайн", "Очно"):
            sql += " AND a.format=?"; params.append(fmt)
        if search:
            sql += " AND (a.title LIKE ? OR a.description LIKE ?)"
            params.extend([f"%{search}%", f"%{search}%"])
        sql += " ORDER BY a.starts_at ASC"
        rows = con.execute(sql, params).fetchall()
        activities = [activity_with_counts(r, con) for r in rows]
    return render_template("index.html", activities=activities, selected_category=category,
                           selected_format=fmt, q=search)

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if len(username) < 3 or len(username) > 24:
            flash("Имя пользователя должно содержать от 3 до 24 символов.", "error")
        elif len(password) < 8:
            flash("Пароль должен содержать минимум 8 символов.", "error")
        else:
            try:
                with db() as con:
                    cur = con.execute(
                        "INSERT INTO users(username,password_hash,created_at) VALUES(?,?,?)",
                        (username, generate_password_hash(password), datetime.now().isoformat(timespec="seconds"))
                    )
                    session["user_id"] = cur.lastrowid
                    session["username"] = username
                flash("Аккаунт создан. Добро пожаловать!", "success")
                return redirect(url_for("index"))
            except sqlite3.IntegrityError:
                flash("Это имя пользователя уже занято.", "error")
    return render_template("auth.html", mode="register")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        with db() as con:
            user = con.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            flash("Вы вошли в аккаунт.", "success")
            return redirect(url_for("index"))
        flash("Неверное имя пользователя или пароль.", "error")
    return render_template("auth.html", mode="login")

@app.route("/logout")
def logout():
    session.clear()
    flash("Вы вышли из аккаунта.", "info")
    return redirect(url_for("index"))

@app.route("/create", methods=["GET", "POST"])
@login_required
def create():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        category = request.form.get("category", "")
        description = request.form.get("description", "").strip()
        starts_at = request.form.get("starts_at", "")
        fmt = request.form.get("format", "Онлайн")
        place = request.form.get("place", "").strip()
        try:
            capacity = int(request.form.get("capacity", "4"))
        except ValueError:
            capacity = 0
        if not title or len(title) > 70 or category not in CATEGORIES or not description:
            flash("Заполните название, категорию и описание. Название: до 70 символов.", "error")
        elif not starts_at or starts_at < datetime.now().strftime("%Y-%m-%dT%H:%M"):
            flash("Выберите время в будущем.", "error")
        elif fmt not in ("Онлайн", "Очно") or (fmt == "Очно" and not place):
            flash("Для очной встречи укажите общественное место, например библиотеку или парк.", "error")
        elif capacity < 2 or capacity > 30:
            flash("Количество участников должно быть от 2 до 30.", "error")
        else:
            now = datetime.now().isoformat(timespec="seconds")
            with db() as con:
                cur = con.execute("""INSERT INTO activities
                    (title,category,description,starts_at,format,place,capacity,creator_id,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?)""",
                    (title, category, description, starts_at, fmt, place if fmt == "Очно" else "",
                     capacity, session["user_id"], now))
                con.execute("INSERT INTO participants VALUES(?,?,?)", (cur.lastrowid, session["user_id"], now))
            flash("Встреча опубликована!", "success")
            return redirect(url_for("detail", activity_id=cur.lastrowid))
    return render_template("create.html")

@app.route("/activity/<int:activity_id>")
def detail(activity_id):
    with db() as con:
        row = con.execute("""SELECT a.*,u.username AS creator_name FROM activities a
                             JOIN users u ON u.id=a.creator_id WHERE a.id=?""", (activity_id,)).fetchone()
        if not row: abort(404)
        activity = activity_with_counts(row, con)
        people = con.execute("""SELECT u.username FROM participants p JOIN users u ON u.id=p.user_id
                                WHERE p.activity_id=? ORDER BY p.joined_at""", (activity_id,)).fetchall()
    return render_template("detail.html", activity=activity, people=people)

@app.route("/activity/<int:activity_id>/join", methods=["POST"])
@login_required
def join(activity_id):
    with db() as con:
        row = con.execute("SELECT * FROM activities WHERE id=?", (activity_id,)).fetchone()
        if not row: abort(404)
        count = con.execute("SELECT COUNT(*) FROM participants WHERE activity_id=?", (activity_id,)).fetchone()[0]
        if row["is_closed"] or row["starts_at"] < datetime.now().strftime("%Y-%m-%dT%H:%M"):
            flash("Эта встреча уже недоступна.", "error")
        elif count >= row["capacity"]:
            flash("Свободных мест больше нет.", "error")
        else:
            con.execute("INSERT OR IGNORE INTO participants VALUES(?,?,?)",
                        (activity_id, session["user_id"], datetime.now().isoformat(timespec="seconds")))
            flash("Вы присоединились к встрече!", "success")
    return redirect(url_for("detail", activity_id=activity_id))

@app.route("/activity/<int:activity_id>/leave", methods=["POST"])
@login_required
def leave(activity_id):
    with db() as con:
        row = con.execute("SELECT * FROM activities WHERE id=?", (activity_id,)).fetchone()
        if not row: abort(404)
        if row["creator_id"] == session["user_id"]:
            flash("Создатель не может покинуть свою встречу. Её можно закрыть.", "info")
        else:
            con.execute("DELETE FROM participants WHERE activity_id=? AND user_id=?",
                        (activity_id, session["user_id"]))
            flash("Вы покинули встречу.", "info")
    return redirect(url_for("detail", activity_id=activity_id))

@app.route("/activity/<int:activity_id>/close", methods=["POST"])
@login_required
def close_activity(activity_id):
    with db() as con:
        row = con.execute("SELECT * FROM activities WHERE id=?", (activity_id,)).fetchone()
        if not row: abort(404)
        if row["creator_id"] != session["user_id"]: abort(403)
        con.execute("UPDATE activities SET is_closed=1 WHERE id=?", (activity_id,))
    flash("Встреча закрыта.", "success")
    return redirect(url_for("index"))

@app.route("/activity/<int:activity_id>/report", methods=["POST"])
@login_required
def report(activity_id):
    reason = request.form.get("reason", "").strip()
    if len(reason) < 5:
        flash("Опишите причину жалобы чуть подробнее.", "error")
    else:
        with db() as con:
            exists = con.execute("SELECT 1 FROM activities WHERE id=?", (activity_id,)).fetchone()
            if not exists: abort(404)
            con.execute("INSERT INTO reports(activity_id,user_id,reason,created_at) VALUES(?,?,?,?)",
                        (activity_id, session["user_id"], reason[:500], datetime.now().isoformat(timespec="seconds")))
        flash("Жалоба сохранена для рассмотрения администратором.", "success")
    return redirect(url_for("detail", activity_id=activity_id))

@app.route("/profile")
@login_required
def profile():
    with db() as con:
        rows = con.execute("""SELECT a.*,u.username AS creator_name FROM participants p
            JOIN activities a ON a.id=p.activity_id JOIN users u ON u.id=a.creator_id
            WHERE p.user_id=? ORDER BY a.starts_at DESC""", (session["user_id"],)).fetchall()
        activities = [activity_with_counts(r, con) for r in rows]
    return render_template("profile.html", activities=activities)

@app.errorhandler(404)
def not_found(_):
    return render_template("message.html", title="Страница не найдена", message="Похоже, такой страницы или встречи нет."), 404

@app.errorhandler(403)
def forbidden(_):
    return render_template("message.html", title="Нет доступа", message="У вас нет прав для этого действия."), 403

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
