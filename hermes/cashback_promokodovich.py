#!/usr/bin/env python3
"""Cashback Промокодович — local SQLite cashback catalog for Hermes."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

HOME = Path.home() / ".hermes"
DEFAULT_DB = HOME / "cashback-promokodovich" / "cashback.sqlite3"
WEB_DATA_PATH = Path("/var/www/webuirenat-fallback/cashback/data.json")
LIFE_ASSISTANT = Path("/home/renat/openclaw-vps-mcp/life-assistant/life_assistant.py")
LIFE_CONTEXT = Path("/home/renat/openclaw-vps-mcp/life-assistant/life_context.py")
PEOPLE_REMINDERS = Path("/home/renat/openclaw-vps-mcp/life-assistant/people_reminders.py")
TZ = ZoneInfo("Asia/Yekaterinburg")

SEED_BANKS = [
    ("vtb", "ВТБ", ["втб", "vtb"]),
    ("alfa", "Альфа-Банк", ["альфа", "альфа-банк", "alfa", "alfabank"]),
    ("sber", "Сбербанк", ["сбер", "сбербанк", "sber", "sberbank"]),
    ("ozon", "Ozon Банк", ["озон", "озон банк", "ozon", "ozon банк", "ozon bank"]),
]


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower().replace("ё", "е"))


def slugify(text: str) -> str:
    base = norm(text)
    translit = str.maketrans({
        "а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ж":"zh","з":"z","и":"i","й":"y",
        "к":"k","л":"l","м":"m","н":"n","о":"o","п":"p","р":"r","с":"s","т":"t","у":"u",
        "ф":"f","х":"h","ц":"c","ч":"ch","ш":"sh","щ":"sch","ъ":"","ы":"y","ь":"","э":"e","ю":"yu","я":"ya"
    })
    base = base.translate(translit)
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    return base or "bank"


def valid_month(value: str | None) -> str:
    if not value:
        return datetime.now(TZ).strftime("%Y-%m")
    if not re.fullmatch(r"\d{4}-\d{2}", value):
        raise ValueError("Месяц должен быть в формате YYYY-MM")
    year, month = map(int, value.split("-"))
    if not 1 <= month <= 12 or year < 2000:
        raise ValueError("Некорректный месяц")
    return value


def ru_month(period: str) -> str:
    names = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]
    y, m = map(int, period.split("-"))
    return f"{names[m-1]} {y}"


def ru_month_genitive(period: str) -> str:
    names = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
    y, m = map(int, period.split("-"))
    return f"{names[m-1]} {y}"


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS banks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL UNIQUE,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS bank_aliases (
            bank_id INTEGER NOT NULL REFERENCES banks(id) ON DELETE CASCADE,
            alias TEXT NOT NULL UNIQUE,
            PRIMARY KEY (bank_id, alias)
        );
        CREATE TABLE IF NOT EXISTS cashbacks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bank_id INTEGER NOT NULL REFERENCES banks(id) ON DELETE CASCADE,
            period TEXT NOT NULL,
            category TEXT NOT NULL,
            category_key TEXT NOT NULL,
            cashback TEXT NOT NULL DEFAULT '',
            limit_text TEXT NOT NULL DEFAULT '',
            conditions TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            source_type TEXT NOT NULL DEFAULT 'manual',
            source_ref TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(bank_id, period, category_key)
        );
        CREATE INDEX IF NOT EXISTS idx_cashbacks_period ON cashbacks(period);
        CREATE INDEX IF NOT EXISTS idx_cashbacks_bank_period ON cashbacks(bank_id, period);
        CREATE TABLE IF NOT EXISTS monthly_tasks (
            period TEXT PRIMARY KEY,
            task_title TEXT NOT NULL,
            task_ref TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'created',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS bank_month_status (
            bank_id INTEGER NOT NULL REFERENCES banks(id) ON DELETE CASCADE,
            period TEXT NOT NULL,
            complete INTEGER NOT NULL DEFAULT 0,
            completed_at TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (bank_id, period)
        );
        CREATE TABLE IF NOT EXISTS bank_month_tasks (
            bank_id INTEGER NOT NULL REFERENCES banks(id) ON DELETE CASCADE,
            period TEXT NOT NULL,
            task_ref TEXT NOT NULL DEFAULT '',
            task_title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (bank_id, period)
        );
        CREATE TABLE IF NOT EXISTS daily_notifications (
            period TEXT NOT NULL,
            notification_date TEXT NOT NULL,
            sent_at TEXT NOT NULL,
            PRIMARY KEY (period, notification_date)
        );
        """
    )
    for slug, name, aliases in SEED_BANKS:
        ts = now_iso()
        conn.execute(
            "INSERT INTO banks(slug,name,active,created_at,updated_at) VALUES(?,?,1,?,?) "
            "ON CONFLICT(slug) DO UPDATE SET name=excluded.name, updated_at=excluded.updated_at",
            (slug, name, ts, ts),
        )
        bank_id = conn.execute("SELECT id FROM banks WHERE slug=?", (slug,)).fetchone()["id"]
        for alias in aliases + [name]:
            try:
                conn.execute("INSERT OR IGNORE INTO bank_aliases(bank_id,alias) VALUES(?,?)", (bank_id, norm(alias)))
            except sqlite3.IntegrityError:
                pass
    conn.commit()


def resolve_bank(conn: sqlite3.Connection, value: str, *, create: bool = False) -> sqlite3.Row:
    q = norm(value)
    row = conn.execute(
        "SELECT b.* FROM banks b LEFT JOIN bank_aliases a ON a.bank_id=b.id "
        "WHERE lower(b.slug)=? OR lower(b.name)=? OR a.alias=? LIMIT 1",
        (q, q, q),
    ).fetchone()
    if row:
        return row
    if not create:
        raise ValueError(f"Банк не найден: {value}. Сначала добавьте его командой bank-add.")
    slug = slugify(value)
    base = slug
    n = 2
    while conn.execute("SELECT 1 FROM banks WHERE slug=?", (slug,)).fetchone():
        slug = f"{base}-{n}"
        n += 1
    ts = now_iso()
    cur = conn.execute("INSERT INTO banks(slug,name,active,created_at,updated_at) VALUES(?,?,1,?,?)", (slug, value.strip(), ts, ts))
    bank_id = cur.lastrowid
    conn.execute("INSERT OR IGNORE INTO bank_aliases(bank_id,alias) VALUES(?,?)", (bank_id, q))
    conn.commit()
    return conn.execute("SELECT * FROM banks WHERE id=?", (bank_id,)).fetchone()


def add_bank(conn: sqlite3.Connection, name: str, aliases: list[str]) -> sqlite3.Row:
    bank = resolve_bank(conn, name, create=True)
    for alias in aliases:
        alias_n = norm(alias)
        existing = conn.execute("SELECT bank_id FROM bank_aliases WHERE alias=?", (alias_n,)).fetchone()
        if existing and existing["bank_id"] != bank["id"]:
            raise ValueError(f"Псевдоним «{alias}» уже принадлежит другому банку")
        conn.execute("INSERT OR IGNORE INTO bank_aliases(bank_id,alias) VALUES(?,?)", (bank["id"], alias_n))
    conn.commit()
    return conn.execute("SELECT * FROM banks WHERE id=?", (bank["id"],)).fetchone()


def upsert_cashback(conn: sqlite3.Connection, bank_id: int, period: str, entry: dict[str, Any], source_type: str, source_ref: str) -> None:
    category = str(entry.get("category", "")).strip()
    if not category:
        raise ValueError("У каждой записи нужна категория")
    ts = now_iso()
    values = (
        bank_id,
        period,
        category,
        norm(category),
        str(entry.get("cashback", entry.get("rate", ""))).strip(),
        str(entry.get("limit", entry.get("limit_text", ""))).strip(),
        str(entry.get("conditions", "")).strip(),
        str(entry.get("note", "")).strip(),
        source_type,
        source_ref,
        ts,
        ts,
    )
    conn.execute(
        """
        INSERT INTO cashbacks(bank_id,period,category,category_key,cashback,limit_text,conditions,note,source_type,source_ref,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(bank_id,period,category_key) DO UPDATE SET
          category=excluded.category,
          cashback=excluded.cashback,
          limit_text=excluded.limit_text,
          conditions=excluded.conditions,
          note=excluded.note,
          source_type=excluded.source_type,
          source_ref=excluded.source_ref,
          updated_at=excluded.updated_at
        """,
        values,
    )


def set_month_complete(conn: sqlite3.Connection, bank_id: int, period: str, complete: bool) -> None:
    ts = now_iso()
    conn.execute(
        """
        INSERT INTO bank_month_status(bank_id,period,complete,completed_at,updated_at)
        VALUES(?,?,?,?,?)
        ON CONFLICT(bank_id,period) DO UPDATE SET
          complete=excluded.complete,
          completed_at=excluded.completed_at,
          updated_at=excluded.updated_at
        """,
        (bank_id, period, 1 if complete else 0, ts if complete else None, ts),
    )


def month_is_complete(conn: sqlite3.Connection, bank_id: int, period: str) -> bool:
    row = conn.execute(
        "SELECT complete FROM bank_month_status WHERE bank_id=? AND period=?",
        (bank_id, period),
    ).fetchone()
    return bool(row and row["complete"])


def replace_month(conn: sqlite3.Connection, bank_name: str, period: str, entries: list[dict[str, Any]], source_type: str, source_ref: str) -> int:
    bank = resolve_bank(conn, bank_name, create=False)
    if not isinstance(entries, list):
        raise ValueError("JSON должен содержать список записей")
    with conn:
        conn.execute("DELETE FROM cashbacks WHERE bank_id=? AND period=?", (bank["id"], period))
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("Каждая запись должна быть JSON-объектом")
            upsert_cashback(conn, bank["id"], period, entry, source_type, source_ref)
        set_month_complete(conn, bank["id"], period, True)
    return len(entries)


def format_summary(conn: sqlite3.Connection, period: str, only_bank: str | None = None) -> str:
    if only_bank:
        bank = resolve_bank(conn, only_bank)
        banks = [bank]
    else:
        banks = conn.execute("SELECT * FROM banks WHERE active=1 ORDER BY id").fetchall()
    lines = [f"Cashback Промокодович — {ru_month(period)}"]
    for bank in banks:
        rows = conn.execute(
            "SELECT * FROM cashbacks WHERE bank_id=? AND period=? ORDER BY category COLLATE NOCASE",
            (bank["id"], period),
        ).fetchall()
        lines.append("")
        lines.append(f"{bank['name']}:")
        if not rows:
            lines.append("  — не заполнено")
            continue
        for row in rows:
            details = []
            if row["cashback"]:
                details.append(row["cashback"])
            if row["limit_text"]:
                details.append(f"лимит: {row['limit_text']}")
            if row["conditions"]:
                details.append(row["conditions"])
            suffix = " — " + "; ".join(details) if details else ""
            lines.append(f"  • {row['category']}{suffix}")
            if row["note"]:
                lines.append(f"    примечание: {row['note']}")
    return "\n".join(lines)


def summary_json(conn: sqlite3.Connection, period: str) -> dict[str, Any]:
    result: dict[str, Any] = {"period": period, "banks": []}
    for bank in conn.execute("SELECT * FROM banks WHERE active=1 ORDER BY id"):
        rows = conn.execute(
            "SELECT category,cashback,limit_text,conditions,note,source_type,source_ref,updated_at FROM cashbacks WHERE bank_id=? AND period=? ORDER BY category COLLATE NOCASE",
            (bank["id"], period),
        ).fetchall()
        result["banks"].append({"name": bank["name"], "slug": bank["slug"], "entries": [dict(r) for r in rows]})
    return result


def web_payload(conn: sqlite3.Connection, period: str | None = None) -> dict[str, Any]:
    period = valid_month(period)
    banks: list[dict[str, Any]] = []
    update_times: list[str] = []
    for bank in conn.execute("SELECT * FROM banks WHERE active=1 ORDER BY id"):
        rows = conn.execute(
            "SELECT category,cashback,limit_text,conditions,note,updated_at "
            "FROM cashbacks WHERE bank_id=? AND period=? ORDER BY category COLLATE NOCASE",
            (bank["id"], period),
        ).fetchall()
        status = conn.execute(
            "SELECT complete,updated_at FROM bank_month_status WHERE bank_id=? AND period=?",
            (bank["id"], period),
        ).fetchone()
        if status and status["updated_at"]:
            update_times.append(status["updated_at"])
        for row in rows:
            if row["updated_at"]:
                update_times.append(row["updated_at"])
        banks.append({
            "name": bank["name"],
            "slug": bank["slug"],
            "complete": bool(status and status["complete"]),
            "entries": [dict(row) for row in rows],
        })

    return {
        "period": period,
        "period_label": ru_month(period).capitalize(),
        "generated_at": now_iso(),
        "updated_at": max(update_times) if update_times else now_iso(),
        "banks": banks,
    }


def publish_web(conn: sqlite3.Connection, path: Path = WEB_DATA_PATH) -> Path:
    payload = web_payload(conn)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.chmod(0o644)
    tmp.replace(path)
    return path


def list_banks(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    out = []
    for bank in conn.execute("SELECT * FROM banks ORDER BY active DESC,id"):
        aliases = [r["alias"] for r in conn.execute("SELECT alias FROM bank_aliases WHERE bank_id=? ORDER BY alias", (bank["id"],))]
        out.append({"slug": bank["slug"], "name": bank["name"], "active": bool(bank["active"]), "aliases": aliases})
    return out


def set_bank_active(conn: sqlite3.Connection, bank_name: str, active: bool) -> None:
    bank = resolve_bank(conn, bank_name)
    conn.execute("UPDATE banks SET active=?,updated_at=? WHERE id=?", (1 if active else 0, now_iso(), bank["id"]))
    conn.commit()


def extract_json_array(text: str) -> list[Any]:
    obj = json.loads(text)
    if isinstance(obj, dict) and "entries" in obj:
        obj = obj["entries"]
    if not isinstance(obj, list):
        raise ValueError("Ожидается JSON-массив или объект с полем entries")
    return obj


def board_has_title(title: str) -> str | None:
    proc = subprocess.run(
        ["hermes", "kanban", "--board", "life", "list", "--json"],
        text=True,
        capture_output=True,
        timeout=30,
    )
    if proc.returncode != 0:
        return None
    raw = proc.stdout.strip()
    start = raw.find("[")
    if start < 0:
        return None
    try:
        cards = json.loads(raw[start:])
    except json.JSONDecodeError:
        return None
    for card in cards:
        if str(card.get("title", "")).strip() == title and card.get("status") != "done":
            return str(card.get("id", ""))
    return None


def board_cards() -> dict[str, dict[str, Any]]:
    proc = subprocess.run(
        ["hermes", "kanban", "--board", "life", "list", "--json"],
        text=True,
        capture_output=True,
        timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Не удалось прочитать Kanban life: {proc.stderr.strip() or proc.stdout.strip()}")
    raw = proc.stdout.strip()
    start = raw.find("[")
    if start < 0:
        raise RuntimeError("Kanban life вернул неожиданный ответ")
    cards = json.loads(raw[start:])
    return {str(card.get("id")): card for card in cards if card.get("id")}


def create_bank_task(conn: sqlite3.Connection, bank: sqlite3.Row, period: str, cards: dict[str, dict[str, Any]]) -> str:
    existing = conn.execute(
        "SELECT * FROM bank_month_tasks WHERE bank_id=? AND period=?",
        (bank["id"], period),
    ).fetchone()
    if existing and existing["task_ref"]:
        card = cards.get(existing["task_ref"])
        if card and card.get("status") not in {"done", "archived"}:
            return existing["task_ref"]

    title = f"Заполнить кэшбэки {bank['name']} — {ru_month(period)}"
    body = (
        f"Cashback Промокодович: для {bank['name']} нужно полностью заполнить категории/предложения кэшбэка на {ru_month(period)}. "
        "Можно прислать скриншоты из приложения банка или написать данные текстом. "
        "Задача остаётся активной, пока полный набор за месяц не сохранён в базе; частичные данные задачу не закрывают."
    )
    suffix = ""
    if existing and existing["task_ref"] and cards.get(existing["task_ref"], {}).get("status") in {"done", "archived"}:
        suffix = f":retry:{datetime.now(TZ).strftime('%Y%m%d')}"
    key = f"cashback-promokodovich:{period}:{bank['slug']}{suffix}"
    proc = subprocess.run(
        [
            "hermes", "kanban", "--board", "life", "create", title,
            "--body", body,
            "--priority", "3",
            "--idempotency-key", key,
            "--created-by", "cashback-promokodovich",
            "--json",
        ],
        text=True,
        capture_output=True,
        timeout=45,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Не удалось создать задачу {bank['name']}: {proc.stderr.strip() or proc.stdout.strip()}")
    raw = (proc.stdout or "").strip()
    match = re.search(r'"id"\s*:\s*"(t_[0-9a-fA-F]+)"', raw) or re.search(r"t_[0-9a-fA-F]+", raw)
    if not match:
        raise RuntimeError(f"Не удалось определить ID задачи {bank['name']}")
    task_ref = match.group(1) if match.lastindex else match.group(0)

    for cmd in (
        ["python3", str(LIFE_CONTEXT), "location", task_ref, "anywhere"],
        ["python3", str(LIFE_CONTEXT), "lane", task_ref, "today"],
    ):
        helper = subprocess.run(cmd, text=True, capture_output=True, timeout=30)
        if helper.returncode != 0:
            raise RuntimeError(f"Задача {task_ref} создана, но не удалось классифицировать её: {helper.stderr.strip() or helper.stdout.strip()}")

    ts = now_iso()
    conn.execute(
        """
        INSERT INTO bank_month_tasks(bank_id,period,task_ref,task_title,status,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(bank_id,period) DO UPDATE SET
          task_ref=excluded.task_ref,
          task_title=excluded.task_title,
          status='active',
          updated_at=excluded.updated_at
        """,
        (bank["id"], period, task_ref, title, "active", ts, ts),
    )
    conn.commit()
    return task_ref


def complete_bank_task(conn: sqlite3.Connection, bank: sqlite3.Row, period: str, cards: dict[str, dict[str, Any]]) -> str | None:
    row = conn.execute(
        "SELECT * FROM bank_month_tasks WHERE bank_id=? AND period=?",
        (bank["id"], period),
    ).fetchone()
    if not row or not row["task_ref"]:
        return None
    task_ref = row["task_ref"]
    card = cards.get(task_ref)
    if card and card.get("status") != "done":
        proc = subprocess.run(
            [
                "hermes", "kanban", "--board", "life", "complete", task_ref,
                "--result", f"Cashback Промокодович: {bank['name']} полностью заполнен за {ru_month(period)}.",
            ],
            text=True,
            capture_output=True,
            timeout=30,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"Не удалось закрыть задачу {task_ref}: {proc.stderr.strip() or proc.stdout.strip()}")
    conn.execute(
        "UPDATE bank_month_tasks SET status='complete',updated_at=? WHERE bank_id=? AND period=?",
        (now_iso(), bank["id"], period),
    )
    conn.commit()
    return task_ref


def send_daily_missing(conn: sqlite3.Connection, period: str, missing: list[str], on_date: date) -> bool:
    if not missing:
        return False
    day = on_date.isoformat()
    already = conn.execute(
        "SELECT 1 FROM daily_notifications WHERE period=? AND notification_date=?",
        (period, day),
    ).fetchone()
    if already:
        return False

    bullets = "\n".join(f"• {name}" for name in missing)
    message = (
        f"На {ru_month(period)} ещё не заполнены кэшбэки:\n{bullets}\n"
        "Пришли скриншоты или напиши категории. По каждому банку задача останется активной до полного заполнения."
    )
    proc = subprocess.run(
        [
            "python3", str(PEOPLE_REMINDERS), "send", "Ренат", message,
            "--dedupe-key", f"cashback-promokodovich:{period}:{day}",
            "--sender-label", "Cashback Промокодович",
        ],
        text=True,
        capture_output=True,
        timeout=45,
    )
    if proc.returncode != 0:
        combined = f"{proc.stdout}\n{proc.stderr}"
        # people_reminders reports a dedupe hit as sent=0 with exit 1/2.
        # That still means today's reminder was already delivered earlier.
        if not ('"sent": 0' in combined and '"errors": []' in combined):
            raise RuntimeError(f"Не удалось отправить ежедневное напоминание: {proc.stderr.strip() or proc.stdout.strip()}")
    conn.execute(
        "INSERT OR IGNORE INTO daily_notifications(period,notification_date,sent_at) VALUES(?,?,?)",
        (period, day, now_iso()),
    )
    conn.commit()
    return True


def daily_sync(conn: sqlite3.Connection, on_date: date, period: str | None = None, *, notify: bool = False, dry_run: bool = False) -> dict[str, Any]:
    period = valid_month(period or on_date.strftime("%Y-%m"))
    banks = conn.execute("SELECT * FROM banks WHERE active=1 ORDER BY id").fetchall()
    cards = board_cards() if not dry_run else {}
    missing: list[str] = []
    created: list[str] = []
    completed: list[str] = []

    for bank in banks:
        if month_is_complete(conn, bank["id"], period):
            if not dry_run:
                ref = complete_bank_task(conn, bank, period, cards)
                if ref:
                    completed.append(ref)
            continue
        missing.append(bank["name"])
        if not dry_run:
            before = conn.execute(
                "SELECT task_ref FROM bank_month_tasks WHERE bank_id=? AND period=?",
                (bank["id"], period),
            ).fetchone()
            ref = create_bank_task(conn, bank, period, cards)
            if not before or before["task_ref"] != ref:
                created.append(ref)

    notified = False
    if notify and missing and not dry_run:
        notified = send_daily_missing(conn, period, missing, on_date)

    return {
        "period": period,
        "missing": missing,
        "complete": not missing,
        "created_tasks": created,
        "completed_tasks": completed,
        "notified": notified,
    }


def task_check(conn: sqlite3.Connection, on_date: date, *, dry_run: bool = False) -> dict[str, Any]:
    target = on_date + timedelta(days=2)
    if target.day != 1:
        return {"action": "none", "date": on_date.isoformat(), "reason": "not_two_days_before_month"}
    period = target.strftime("%Y-%m")
    existing = conn.execute("SELECT * FROM monthly_tasks WHERE period=?", (period,)).fetchone()
    if existing:
        return {"action": "exists", "period": period, "task_ref": existing["task_ref"]}

    title = f"Заполнить кэшбэки на {ru_month(period)}"
    board_ref = board_has_title(title)
    if board_ref:
        conn.execute(
            "INSERT OR REPLACE INTO monthly_tasks(period,task_title,task_ref,status,created_at) VALUES(?,?,?,?,?)",
            (period, title, board_ref, "found_existing", now_iso()),
        )
        conn.commit()
        return {"action": "exists_on_board", "period": period, "task_ref": board_ref}

    banks = [r["name"] for r in conn.execute("SELECT * FROM banks WHERE active=1 ORDER BY id")]
    bank_text = ", ".join(banks) if banks else "активные банки"
    body = (
        f"Автоматически создано Cashback Промокодович за 2 дня до начала {ru_month_genitive(period)}. "
        f"Нужно собрать категории/предложения кэшбэка по всем активным банкам: {bank_text}. "
        "Данные можно присылать скриншотами из банковских приложений или обычным текстом; Hermes должен распознать их и сохранить в SQLite-базу Cashback Промокодович. "
        "После заполнения проверить сводный список и явно отметить банки, по которым данных нет."
    )
    if dry_run:
        return {"action": "would_create", "period": period, "title": title, "body": body}

    proc = subprocess.run(
        ["python3", str(LIFE_ASSISTANT), "task-add", title, "--body", body, "--priority", "3"],
        text=True,
        capture_output=True,
        timeout=45,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Не удалось создать life-задачу: {proc.stderr.strip() or proc.stdout.strip()}")
    output = (proc.stdout or "").strip()
    ref_match = re.search(r"t_[0-9a-fA-F]+", output)
    task_ref = ref_match.group(0) if ref_match else ""
    conn.execute(
        "INSERT INTO monthly_tasks(period,task_title,task_ref,status,created_at) VALUES(?,?,?,?,?)",
        (period, title, task_ref, "created", now_iso()),
    )
    conn.commit()
    return {"action": "created", "period": period, "task_ref": task_ref, "title": title}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cashback-promokodovich", description="SQLite-каталог кэшбэков Hermes")
    p.add_argument("--db", default=str(DEFAULT_DB), help="Путь к SQLite (для тестов можно указать другой)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init")
    sub.add_parser("banks")
    sub.add_parser("publish-web")

    badd = sub.add_parser("bank-add")
    badd.add_argument("name")
    badd.add_argument("--alias", action="append", default=[])

    ben = sub.add_parser("bank-enable")
    ben.add_argument("bank")
    bdis = sub.add_parser("bank-disable")
    bdis.add_argument("bank")

    add = sub.add_parser("add")
    add.add_argument("--bank", required=True)
    add.add_argument("--month")
    add.add_argument("--category", required=True)
    add.add_argument("--cashback", default="")
    add.add_argument("--limit", default="")
    add.add_argument("--conditions", default="")
    add.add_argument("--note", default="")
    add.add_argument("--source", choices=["manual", "screenshot", "import"], default="manual")
    add.add_argument("--source-ref", default="")

    rep = sub.add_parser("replace-json")
    rep.add_argument("--bank", required=True)
    rep.add_argument("--month")
    src = rep.add_mutually_exclusive_group(required=True)
    src.add_argument("--json")
    src.add_argument("--file")
    rep.add_argument("--source", choices=["manual", "screenshot", "import"], default="manual")
    rep.add_argument("--source-ref", default="")

    clear = sub.add_parser("clear-month")
    clear.add_argument("--bank", required=True)
    clear.add_argument("--month")

    ls = sub.add_parser("list")
    ls.add_argument("--month")
    ls.add_argument("--bank")
    ls.add_argument("--json", action="store_true")

    miss = sub.add_parser("missing")
    miss.add_argument("--month")

    mark = sub.add_parser("mark-complete")
    mark.add_argument("--bank", required=True)
    mark.add_argument("--month")
    unmark = sub.add_parser("mark-incomplete")
    unmark.add_argument("--bank", required=True)
    unmark.add_argument("--month")

    ds = sub.add_parser("daily-sync")
    ds.add_argument("--date", help="YYYY-MM-DD; по умолчанию сегодня Asia/Yekaterinburg")
    ds.add_argument("--month")
    ds.add_argument("--notify", action="store_true")
    ds.add_argument("--dry-run", action="store_true")
    ds.add_argument("--json", action="store_true")

    tc = sub.add_parser("task-check")
    tc.add_argument("--date", help="YYYY-MM-DD; по умолчанию сегодня Asia/Yekaterinburg")
    tc.add_argument("--dry-run", action="store_true")
    tc.add_argument("--json", action="store_true")
    return p


def main() -> int:
    args = parser().parse_args()
    db_path = Path(args.db).expanduser()
    try:
        conn = connect(db_path)
        if args.cmd == "init":
            print(f"OK: {db_path}")
        elif args.cmd == "banks":
            print(json.dumps(list_banks(conn), ensure_ascii=False, indent=2))
        elif args.cmd == "publish-web":
            path = publish_web(conn)
            print(json.dumps({"ok": True, "path": str(path)}, ensure_ascii=False))
        elif args.cmd == "bank-add":
            bank = add_bank(conn, args.name, args.alias)
            print(json.dumps({"ok": True, "bank": bank["name"], "slug": bank["slug"]}, ensure_ascii=False))
        elif args.cmd in {"bank-enable", "bank-disable"}:
            set_bank_active(conn, args.bank, args.cmd == "bank-enable")
            print(json.dumps({"ok": True, "bank": args.bank, "active": args.cmd == "bank-enable"}, ensure_ascii=False))
        elif args.cmd == "add":
            period = valid_month(args.month)
            bank = resolve_bank(conn, args.bank)
            upsert_cashback(conn, bank["id"], period, {
                "category": args.category,
                "cashback": args.cashback,
                "limit": args.limit,
                "conditions": args.conditions,
                "note": args.note,
            }, args.source, args.source_ref)
            conn.commit()
            print(json.dumps({"ok": True, "bank": bank["name"], "period": period, "category": args.category}, ensure_ascii=False))
        elif args.cmd == "replace-json":
            period = valid_month(args.month)
            text = args.json if args.json is not None else Path(args.file).read_text(encoding="utf-8")
            entries = extract_json_array(text)
            count = replace_month(conn, args.bank, period, entries, args.source, args.source_ref)
            print(json.dumps({"ok": True, "bank": args.bank, "period": period, "count": count}, ensure_ascii=False))
        elif args.cmd == "clear-month":
            period = valid_month(args.month)
            bank = resolve_bank(conn, args.bank)
            cur = conn.execute("DELETE FROM cashbacks WHERE bank_id=? AND period=?", (bank["id"], period))
            set_month_complete(conn, bank["id"], period, False)
            conn.commit()
            print(json.dumps({"ok": True, "deleted": cur.rowcount, "bank": bank["name"], "period": period}, ensure_ascii=False))
        elif args.cmd in {"mark-complete", "mark-incomplete"}:
            period = valid_month(args.month)
            bank = resolve_bank(conn, args.bank)
            set_month_complete(conn, bank["id"], period, args.cmd == "mark-complete")
            conn.commit()
            print(json.dumps({"ok": True, "bank": bank["name"], "period": period, "complete": args.cmd == "mark-complete"}, ensure_ascii=False))
        elif args.cmd == "list":
            period = valid_month(args.month)
            if args.json:
                print(json.dumps(summary_json(conn, period), ensure_ascii=False, indent=2))
            else:
                print(format_summary(conn, period, args.bank))
        elif args.cmd == "missing":
            period = valid_month(args.month)
            missing = []
            for bank in conn.execute("SELECT * FROM banks WHERE active=1 ORDER BY id"):
                if not month_is_complete(conn, bank["id"], period):
                    missing.append(bank["name"])
            print(json.dumps({"period": period, "missing": missing, "complete": not missing}, ensure_ascii=False, indent=2))
        elif args.cmd == "daily-sync":
            on_date = date.fromisoformat(args.date) if args.date else datetime.now(TZ).date()
            result = daily_sync(conn, on_date, args.month, notify=args.notify, dry_run=args.dry_run)
            if args.json or result["created_tasks"] or result["completed_tasks"] or result["notified"]:
                print(json.dumps(result, ensure_ascii=False))
        elif args.cmd == "task-check":
            on_date = date.fromisoformat(args.date) if args.date else datetime.now(TZ).date()
            result = task_check(conn, on_date, dry_run=args.dry_run)
            if args.json or result.get("action") not in {"none", "exists"}:
                print(json.dumps(result, ensure_ascii=False))

        refresh_web_commands = {
            "init", "bank-add", "bank-enable", "bank-disable", "add", "replace-json",
            "clear-month", "mark-complete", "mark-incomplete", "daily-sync",
        }
        if (
            db_path.resolve() == DEFAULT_DB.resolve()
            and args.cmd in refresh_web_commands
            and not getattr(args, "dry_run", False)
        ):
            try:
                publish_web(conn)
            except Exception as web_exc:
                print(f"WARN: web publish failed: {web_exc}", file=sys.stderr)
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
