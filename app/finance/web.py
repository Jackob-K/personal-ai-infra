from __future__ import annotations

import html
import json
from collections import defaultdict

ENTRY_TYPE_OPTIONS = [
    ("standard", "Moje položka"),
    ("shared", "Sdílená / cizí"),
    ("internal_transfer", "Interní převod"),
    ("investment", "Investice"),
    ("settlement", "Srovnání dluhu"),
]


def render_finance_page(
    *,
    preview_rows: list[dict],
    month_rows: list[dict],
    selected_month: str,
    available_months: list[str],
    is_closed_month: bool,
    category_options: list[str],
    training_count: int,
    last_import_count: int,
    message: str | None = None,
    error: str | None = None,
) -> str:
    notice = f"<p style='color:#0b5'>{html.escape(message)}</p>" if message else ""
    danger = f"<p style='color:#b00'>{html.escape(error)}</p>" if error else ""
    template_headers = "datum,částka,obchodník,číslo protiúčtu,účet,poznámka,kategorie"

    rows = "".join(_render_row(item, category_options) for item in month_rows[:250])
    month_nav = _render_month_nav(available_months, selected_month)
    summary_block = _render_month_summary(month_rows, selected_month, is_closed_month)
    table = (
        "<p>Zatím není uložený žádný náhled importu.</p>"
        if not month_rows
        else "<form method='post' action='/finance/month/save' id='month-save-form'>"
        f"<input type='hidden' name='month_id' value='{html.escape(selected_month)}'>"
        "<input type='hidden' name='payload_json' id='month-save-payload' value=''>"
        "<p><button type='submit'>Uložit změny měsíce</button></p>"
        "<table border='1' cellpadding='6' cellspacing='0'>"
        "<thead><tr><th>Řádek</th><th>ID</th><th>Datum</th><th>Protistrana</th><th>Částka</th><th>Účet protistrany</th>"
        "<th>Popis</th><th>Napárovaný email</th><th>Navržená kategorie</th><th>Vybraná kategorie</th><th>Typ položky</th><th>Moje částka</th><th>Efektivní měsíc</th><th>Koho se týká</th><th>Confidence</th><th>Důvod</th><th>Původní kategorie</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        "<p style='margin-top:12px'><button type='submit'>Uložit změny měsíce</button></p>"
        "</form>"
    )

    return (
        "<h1>Finance</h1>"
        "<p>První verze finančního modulu: ruční upload exportu, sjednocení transakcí a návrhy kategorií z historie.</p>"
        f"{notice}{danger}"
        f"{month_nav}"
        f"{summary_block}"
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
        "<form method='post' action='/finance/close-month' style='margin-top:10px'>"
        f"<input type='hidden' name='month_id' value='{html.escape(selected_month)}'>"
        "<button type='submit'>Uzavřít měsíc</button>"
        "</form>"
        "<form method='post' action='/finance/month/reset-categories' style='margin-top:10px'>"
        f"<input type='hidden' name='month_id' value='{html.escape(selected_month)}'>"
        "<button type='submit'>Obnovit kategorie z návrhu/původní</button>"
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
        f"<p>Zvolený měsíc: <b>{html.escape(selected_month or '-')}</b> | uzavřený: <b>{'ano' if is_closed_month else 'ne'}</b></p>"
        f"<p>Napárované emaily: <b>{sum(1 for item in month_rows if item.get('email_match_status') == 'matched')}</b></p>"
        "<p>Logika návrhu je zatím záměrně jednoduchá: účet protistrany, název protistrany a poznámka. "
        "Později sem doplníme pravidla, ruční potvrzení a Discord digest.</p>"
        "</div>"
        "</div>"
        "<h2>Transakce měsíce</h2>"
        f"{table}"
        f"{_month_save_script()}"
    )


def _render_row(item: dict, category_options: list[str]) -> str:
    suggestion = item.get("suggestion") or {}
    email_match = item.get("email_match") or {}
    email_debug = item.get("email_match_debug") or {}
    amount = item.get("amount", 0)
    amount_text = f"{float(amount):,.2f}".replace(",", " ").replace(".", ",")
    personal_amount = float(item.get("personal_amount", amount) or 0)
    personal_amount_text = f"{personal_amount:,.2f}".replace(",", " ").replace(".", ",")
    transaction_id = str(item.get("transaction_id", ""))
    row_key = transaction_id or f"row-{html.escape(str(item.get('source_row', '')))}"
    effective_selected_category = (
        str(item.get("selected_category", "")).strip()
        or str(suggestion.get("category", "")).strip()
        or str(item.get("raw_category", "")).strip()
        or "Nezařazeno"
    )
    effective_entry_type = (
        str(item.get("entry_type", "")).strip()
        or ("investment" if effective_selected_category == "Investování" else "standard")
    )
    effective_month = str(item.get("effective_month", "")).strip() or str(item.get("booking_date", ""))[:7]
    related_party = str(item.get("related_party", "")).strip()
    category_locked = bool(item.get("category_locked"))
    category_locked_badge = "" if not category_locked else "<div style='color:#555;margin-top:4px'>Ručně upraveno</div>"
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
        f"<td>{html.escape(str(item.get('source_row', '')))}<input type='hidden' name='row_key' value='{row_key}'></td>"
        f"<td><code>{html.escape(transaction_id[:8])}</code></td>"
        f"<td>{html.escape(str(item.get('booking_date', '')))}</td>"
        f"<td>{html.escape(str(item.get('counterparty', '')))}</td>"
        f"<td>{html.escape(amount_text)} {html.escape(str(item.get('currency', 'CZK')))}</td>"
        f"<td>{html.escape(str(item.get('counterparty_account', '')))}</td>"
        f"<td><input type='text' name='description__{row_key}' data-transaction-id='{row_key}' data-field='description' value='{html.escape(str(item.get('description', '')))}' style='width:260px'></td>"
        f"<td>{email_block}</td>"
        f"<td>{html.escape(str(suggestion.get('category', '')))}</td>"
        f"<td><select name='selected_category__{row_key}' data-transaction-id='{row_key}' data-field='selected_category'>{_category_options(category_options, effective_selected_category)}</select>"
        f"{category_locked_badge}</td>"
        f"<td><select name='entry_type__{row_key}' data-transaction-id='{row_key}' data-field='entry_type'>{_entry_type_options(effective_entry_type)}</select></td>"
        f"<td><input type='text' name='personal_amount__{row_key}' data-transaction-id='{row_key}' data-field='personal_amount' value='{html.escape(personal_amount_text)}' style='width:110px'></td>"
        f"<td><input type='text' name='effective_month__{row_key}' data-transaction-id='{row_key}' data-field='effective_month' value='{html.escape(effective_month)}' style='width:90px'></td>"
        f"<td><input type='text' name='related_party__{row_key}' data-transaction-id='{row_key}' data-field='related_party' value='{html.escape(related_party)}' style='width:140px'></td>"
        f"<td>{html.escape(str(suggestion.get('confidence', '')))}</td>"
        f"<td>{html.escape(str(suggestion.get('reason', '')))}"
        f"{'' if not suggestion.get('matched_on') else ' (' + html.escape(str(suggestion.get('matched_on'))) + ')'}"
        "</td>"
        f"<td>{html.escape(str(item.get('raw_category', '')))}</td>"
        "</tr>"
    )


def _category_options(options: list[str], selected: str) -> str:
    if selected and selected not in options:
        options = [selected] + options
    rendered: list[str] = []
    for option in options:
        sel = " selected" if option == selected else ""
        rendered.append(f"<option value='{html.escape(option)}'{sel}>{html.escape(option)}</option>")
    return "".join(rendered)


def _entry_type_options(selected: str) -> str:
    rendered: list[str] = []
    for value, label in ENTRY_TYPE_OPTIONS:
        sel = " selected" if value == selected else ""
        rendered.append(f"<option value='{html.escape(value)}'{sel}>{html.escape(label)}</option>")
    return "".join(rendered)


def _render_month_nav(months: list[str], selected_month: str) -> str:
    if not months:
        return ""
    current_index = months.index(selected_month) if selected_month in months else -1
    newer_link = ""
    older_link = ""
    if current_index > 0:
        newer = months[current_index - 1]
        newer_link = f"<a href='/finance?month={html.escape(newer)}' style='margin-right:12px'>&larr; novější</a>"
    if 0 <= current_index < len(months) - 1:
        older = months[current_index + 1]
        older_link = f"<a href='/finance?month={html.escape(older)}' style='margin-left:12px'>starší &rarr;</a>"
    links = []
    for month in months:
        style = "font-weight:bold;text-decoration:underline;" if month == selected_month else ""
        links.append(f"<a href='/finance?month={html.escape(month)}' style='margin-right:12px;{style}'>{html.escape(month)}</a>")
    return (
        "<div style='margin-bottom:8px'><b>Časová osa:</b> "
        f"{newer_link}{older_link}</div>"
        "<div style='margin-bottom:16px'><b>Měsíce:</b> "
        + "".join(links)
        + "</div>"
    )


def _render_month_summary(rows: list[dict], selected_month: str, is_closed_month: bool) -> str:
    if not rows:
        return "<p>Pro vybraný měsíc zatím nejsou data.</p>"
    category_sums: dict[str, float] = defaultdict(float)
    bank_income = 0.0
    bank_expense = 0.0
    personal_income = 0.0
    personal_expense = 0.0
    shared_portion = 0.0
    settlement_total = 0.0
    for item in rows:
        booking_amount = float(item.get("amount", 0) or 0)
        if booking_amount >= 0:
            bank_income += booking_amount
        else:
            bank_expense += abs(booking_amount)

        effective_month = str(item.get("effective_month", "")).strip() or str(item.get("booking_date", ""))[:7]
        if effective_month != selected_month:
            continue
        category = str(item.get("selected_category") or item.get("raw_category") or "Nezařazeno")
        amount = float(item.get("amount", 0) or 0)
        personal_amount = float(item.get("personal_amount", amount) or 0)
        entry_type = str(item.get("entry_type", "")).strip() or ("investment" if category == "Investování" else "standard")

        if entry_type == "settlement":
            settlement_total += amount
            continue
        if entry_type == "internal_transfer":
            continue

        if entry_type == "shared":
            shared_part = amount - personal_amount
            if amount < 0 and shared_part < 0:
                shared_portion += abs(shared_part)

        budget_amount = personal_amount
        category_sums[category] += budget_amount
        if budget_amount >= 0:
            personal_income += budget_amount
        else:
            personal_expense += abs(budget_amount)

    invest = abs(
        sum(
            float(item.get("personal_amount", item.get("amount", 0)) or 0)
            for item in rows
            if (str(item.get("effective_month", "")).strip() or str(item.get("booking_date", ""))[:7]) == selected_month
            and (
                (str(item.get("entry_type", "")).strip() == "investment")
                or str(item.get("selected_category", "")).strip() == "Investování"
            )
        )
    )
    available = max(0.0, personal_income - personal_expense)
    bars = _render_bar_chart(category_sums)
    pie_one = _render_pie(
        [
            ("Bankovní příjmy", bank_income),
            ("Bankovní výdaje", bank_expense),
            ("Investice", invest),
        ]
    )
    pie_two = _render_pie(_top_category_slices(category_sums))
    return (
        "<section style='margin-bottom:20px'>"
        f"<h2>Měsíc {html.escape(selected_month)}</h2>"
        f"<p>Stav: <b>{'uzavřený' if is_closed_month else 'pracovní náhled'}</b> | "
        f"Bankovní příjmy: <b>{_fmt_amount(bank_income)}</b> | Bankovní výdaje: <b>{_fmt_amount(-bank_expense)}</b> | "
        f"Moje příjmy: <b>{_fmt_amount(personal_income)}</b> | Moje výdaje: <b>{_fmt_amount(-personal_expense)}</b> | "
        f"Cizí podíl v platbách: <b>{_fmt_amount(shared_portion)}</b> | "
        f"Srovnání dluhů: <b>{_fmt_amount(settlement_total)}</b> | "
        f"K investování orientačně: <b>{_fmt_amount(available)}</b></p>"
        f"{bars}"
        "<div style='display:flex;gap:24px;flex-wrap:wrap;margin-top:16px'>"
        f"{pie_one}"
        f"{pie_two}"
        "</div>"
        "</section>"
    )


def _render_bar_chart(category_sums: dict[str, float]) -> str:
    items = sorted(((k, abs(v)) for k, v in category_sums.items() if abs(v) > 0.01), key=lambda x: x[1], reverse=True)[:10]
    if not items:
        return "<p>Graf kategorií se objeví po přiřazení kategorií.</p>"
    max_value = max(value for _, value in items) or 1.0
    rows = []
    for label, value in items:
        width = max(2, int((value / max_value) * 100))
        rows.append(
            "<div style='display:grid;grid-template-columns:220px 1fr 120px;gap:10px;align-items:center;margin:6px 0'>"
            f"<div>{html.escape(label)}</div>"
            f"<div style='background:#e9eef3;border-radius:999px;overflow:hidden'><div style='width:{width}%;background:#2f6fed;height:14px'></div></div>"
            f"<div>{html.escape(_fmt_amount(value))}</div>"
            "</div>"
        )
    return "<div style='border:1px solid #ddd;border-radius:10px;padding:14px'><h3 style='margin-top:0'>Kategorie</h3>" + "".join(rows) + "</div>"


def _render_pie(slices: list[tuple[str, float]]) -> str:
    filtered = [(label, value) for label, value in slices if value > 0.01]
    if not filtered:
        return "<div style='border:1px solid #ddd;border-radius:10px;padding:14px;min-width:260px'><p>Koláč zatím není k dispozici.</p></div>"
    total = sum(value for _, value in filtered) or 1.0
    colors = ["#2f6fed", "#ef8a17", "#17a673", "#ca3e47", "#7a5cff", "#888"]
    stops = []
    start = 0.0
    legend = []
    for idx, (label, value) in enumerate(filtered):
        pct = (value / total) * 100
        end = start + pct
        color = colors[idx % len(colors)]
        stops.append(f"{color} {start:.2f}% {end:.2f}%")
        legend.append(f"<div><span style='display:inline-block;width:10px;height:10px;background:{color};margin-right:6px'></span>{html.escape(label)}: {html.escape(_fmt_amount(value))}</div>")
        start = end
    return (
        "<div style='border:1px solid #ddd;border-radius:10px;padding:14px;min-width:260px'>"
        f"<div style='width:180px;height:180px;border-radius:50%;background:conic-gradient({', '.join(stops)});margin-bottom:12px'></div>"
        + "".join(legend)
        + "</div>"
    )


def _top_category_slices(category_sums: dict[str, float]) -> list[tuple[str, float]]:
    items = sorted(((label, abs(value)) for label, value in category_sums.items() if abs(value) > 0.01), key=lambda x: x[1], reverse=True)
    top = items[:4]
    other = sum(value for _, value in items[4:])
    if other > 0:
        top.append(("Ostatní", other))
    return top


def _fmt_amount(value: float) -> str:
    sign = "-" if value < 0 else ""
    return sign + f"{abs(value):,.2f}".replace(",", " ").replace(".", ",") + " CZK"


def _month_save_script() -> str:
    script = """
<script>
(function () {
  const form = document.getElementById('month-save-form');
  if (!form) return;
  form.addEventListener('submit', function () {
    const payload = [];
    const byId = new Map();
    form.querySelectorAll('[data-transaction-id][data-field]').forEach((el) => {
      const transactionId = el.getAttribute('data-transaction-id');
      const field = el.getAttribute('data-field');
      if (!transactionId || !field) return;
      if (!byId.has(transactionId)) byId.set(transactionId, {transaction_id: transactionId});
      byId.get(transactionId)[field] = el.value || '';
    });
    byId.forEach((value) => payload.push(value));
    const target = document.getElementById('month-save-payload');
    if (target) target.value = JSON.stringify(payload);
  });
})();
</script>
"""
    return script
