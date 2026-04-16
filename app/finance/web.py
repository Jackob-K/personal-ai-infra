from __future__ import annotations

import html


def render_finance_page(
    *,
    preview_rows: list[dict],
    training_count: int,
    last_import_count: int,
    message: str | None = None,
    error: str | None = None,
) -> str:
    notice = f"<p style='color:#0b5'>{html.escape(message)}</p>" if message else ""
    danger = f"<p style='color:#b00'>{html.escape(error)}</p>" if error else ""
    template_headers = "datum,částka,obchodník,číslo protiúčtu,účet,poznámka,kategorie"

    rows = "".join(_render_row(item) for item in preview_rows[:200])
    table = (
        "<p>Zatím není uložený žádný náhled importu.</p>"
        if not preview_rows
        else "<table border='1' cellpadding='6' cellspacing='0'>"
        "<thead><tr><th>Řádek</th><th>ID</th><th>Datum</th><th>Protistrana</th><th>Částka</th><th>Účet protistrany</th>"
        "<th>Popis</th><th>Napárovaný email</th><th>Navržená kategorie</th><th>Confidence</th><th>Důvod</th><th>Původní kategorie</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )

    return (
        "<h1>Finance</h1>"
        "<p>První verze finančního modulu: ruční upload exportu, sjednocení transakcí a návrhy kategorií z historie.</p>"
        f"{notice}{danger}"
        "<div style='display:flex;gap:24px;align-items:flex-start;flex-wrap:wrap'>"
        "<div style='flex:1;min-width:320px'>"
        "<h2>Import</h2>"
        "<form method='post' action='/finance/preview' enctype='multipart/form-data'>"
        "<p><input type='file' name='statement'></p>"
        "<p>nebo vlož CSV obsah přímo:</p>"
        "<p><textarea name='csv_text' rows='10' style='width:100%;font-family:monospace'></textarea></p>"
        "<p><label><input type='checkbox' name='save_training' value='1'> Pokud CSV obsahuje sloupec <code>kategorie</code>, ulož ho i jako trénovací data.</label></p>"
        "<p><button type='submit'>Nahrát a zobrazit náhled</button></p>"
        "</form>"
        "<form method='post' action='/finance/rematch' style='margin-top:10px'>"
        "<button type='submit'>Zkusit znovu napárovat emaily</button>"
        "</form>"
        "<h3>Doporučená hlavička</h3>"
        f"<p><code>{html.escape(template_headers)}</code></p>"
        "<p>Nutné minimum je <code>datum</code>, <code>částka</code> a <code>obchodník</code>/protistrana. "
        "Účty necháváme jako volitelné, ale pomůžou přesně odlišit třeba stejné jméno u různých lidí.</p>"
        "</div>"
        "<div style='flex:1;min-width:320px'>"
        "<h2>Stav</h2>"
        f"<p>Uložených trénovacích příkladů: <b>{training_count}</b></p>"
        f"<p>Poslední náhled obsahuje: <b>{last_import_count}</b> transakcí</p>"
        f"<p>Napárované emaily: <b>{sum(1 for item in preview_rows if item.get('email_match_status') == 'matched')}</b></p>"
        "<p>Logika návrhu je zatím záměrně jednoduchá: účet protistrany, název protistrany a poznámka. "
        "Později sem doplníme pravidla, ruční potvrzení a Discord digest.</p>"
        "</div>"
        "</div>"
        "<h2>Poslední náhled</h2>"
        f"{table}"
    )


def _render_row(item: dict) -> str:
    suggestion = item.get("suggestion") or {}
    email_match = item.get("email_match") or {}
    email_debug = item.get("email_match_debug") or {}
    amount = item.get("amount", 0)
    amount_text = f"{float(amount):,.2f}".replace(",", " ").replace(".", ",")
    transaction_id = str(item.get("transaction_id", ""))
    email_block = ""
    if email_match:
        email_block = (
            f"<div><b>{html.escape(str(email_match.get('subject', '')))}</b></div>"
            f"<div style='color:#555'>{html.escape(str(email_match.get('sender', '')))}</div>"
            f"<div style='color:#555'>confidence {html.escape(str(email_match.get('confidence', '')))} | "
            f"{html.escape(str(email_match.get('reason', '')))}</div>"
        )
    else:
        email_block = f"<div style='color:#777'><b>{html.escape(str(item.get('email_match_status', 'unmatched')))}</b></div>"
    if email_debug:
        email_block += f"<div style='margin-top:6px;color:#555'>{html.escape(str(email_debug.get('summary', '')))}</div>"
        candidates = email_debug.get("top_candidates") or []
        if candidates:
            email_block += "<div style='margin-top:6px'><b>Top kandidáti:</b></div><ul style='padding-left:18px;margin:4px 0'>"
            for candidate in candidates[:3]:
                label = (
                    f"{candidate.get('score')} | "
                    f"{candidate.get('subject', '')[:60]} | "
                    f"{candidate.get('reason', '')}"
                )
                details = (
                    f"a={candidate.get('amount_score')} "
                    f"t={candidate.get('text_score')} "
                    f"d={candidate.get('date_score')} "
                    f"days={candidate.get('delta_days', '-')}"
                )
                email_block += (
                    f"<li><div>{html.escape(label)}</div>"
                    f"<div style='color:#666'>{html.escape(details)}</div></li>"
                )
            email_block += "</ul>"
    return (
        "<tr>"
        f"<td>{html.escape(str(item.get('source_row', '')))}</td>"
        f"<td><code>{html.escape(transaction_id[:8])}</code></td>"
        f"<td>{html.escape(str(item.get('booking_date', '')))}</td>"
        f"<td>{html.escape(str(item.get('counterparty', '')))}</td>"
        f"<td>{html.escape(amount_text)} {html.escape(str(item.get('currency', 'CZK')))}</td>"
        f"<td>{html.escape(str(item.get('counterparty_account', '')))}</td>"
        "<td>"
        "<form method='post' action='/finance/preview/update'>"
        f"<input type='hidden' name='transaction_id' value='{html.escape(transaction_id)}'>"
        f"<input type='text' name='description' value='{html.escape(str(item.get('description', '')))}' style='width:260px'> "
        "<button type='submit'>Uložit</button>"
        "</form>"
        "</td>"
        f"<td>{email_block}</td>"
        f"<td>{html.escape(str(suggestion.get('category', '')))}</td>"
        f"<td>{html.escape(str(suggestion.get('confidence', '')))}</td>"
        f"<td>{html.escape(str(suggestion.get('reason', '')))}"
        f"{'' if not suggestion.get('matched_on') else ' (' + html.escape(str(suggestion.get('matched_on'))) + ')'}"
        "</td>"
        f"<td>{html.escape(str(item.get('raw_category', '')))}</td>"
        "</tr>"
    )
