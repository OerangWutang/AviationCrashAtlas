"""HTML report renderer.

Turns a ``ReportModel`` into an HTML string suitable for:
- In-app preview (rendered in an iframe or shadow DOM)
- WeasyPrint input for PDF generation

Design: court/professional style.
- Serif body font (document feel)
- Numbered sections
- Every fact has an inline footnote number → sources appendix
- Resolution history appendix: who decided what, when, with rationale
- Audit hash appendix
- Mandatory disclaimer page
- Page numbers via CSS @page (only meaningful in PDF output)
"""

from __future__ import annotations

# Jinja2 is a dependency of FastAPI/Starlette so it is always available.
from jinja2 import Environment

from atlas.domain.publication.report_model import ReportModel
from atlas.security import sign_evidence_hash

_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ model.case_title }} — Atlas Report v{{ model.report_version }}</title>
<style>
  /* ── Base ── */
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  html { font-size: 10pt; }
  body {
    font-family: "Georgia", "Cambria", "Times New Roman", serif;
    color: #1a1a2e;
    background: #ffffff;
    line-height: 1.6;
    max-width: 780px;
    margin: 0 auto;
    padding: 2rem 2.5rem;
  }

  /* ── Print / PDF ── */
  @media print {
    body { max-width: 100%; padding: 0; }
    @page { size: A4; margin: 2.5cm 2cm; }
    @page { @bottom-right { content: "Page " counter(page) " of " counter(pages); font-size: 8pt; } }
    h1, h2, h3 { page-break-after: avoid; }
    .page-break { page-break-before: always; }
  }

  /* ── Typography ── */
  h1 { font-size: 1.5rem; font-weight: 700; margin-bottom: 0.5rem; line-height: 1.3; }
  h2 { font-size: 1.1rem; font-weight: 700; margin: 2rem 0 0.75rem; border-bottom: 1px solid #ccc; padding-bottom: 0.25rem; }
  h3 { font-size: 0.95rem; font-weight: 600; margin: 1.25rem 0 0.5rem; }
  p  { margin-bottom: 0.75rem; }

  /* ── Header ── */
  .report-header { border-bottom: 2px solid #102a43; padding-bottom: 1rem; margin-bottom: 1.5rem; }
  .report-header .subtitle { font-size: 0.85rem; color: #627d98; margin-top: 0.25rem; }
  .report-meta { display: flex; gap: 2rem; flex-wrap: wrap; margin-top: 0.75rem; font-size: 0.8rem; color: #486581; }
  .report-meta span::before { content: attr(data-label) ": "; font-weight: 600; }

  /* ── Status chips ── */
  .chip { display: inline-block; padding: 0.1em 0.5em; border-radius: 0.2em; font-size: 0.75rem; font-weight: 600; border: 1px solid; }
  .chip-disputed { background: #fffbeb; color: #b45309; border-color: #fde68a; }
  .chip-resolved { background: #ecfdf5; color: #047857; border-color: #a7f3d0; }
  .chip-manual   { background: #f0f4f8; color: #334e68; border-color: #bcccdc; }
  .chip-warn     { background: #fef2f2; color: #b91c1c; border-color: #fecaca; }

  /* ── Facts table ── */
  table { width: 100%; border-collapse: collapse; margin: 0.75rem 0 1.5rem; font-size: 0.85rem; }
  th { text-align: left; font-size: 0.7rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: #627d98; padding: 0.4rem 0.6rem; border-bottom: 2px solid #d9e2ec; }
  td { padding: 0.45rem 0.6rem; border-bottom: 1px solid #e8edf2; vertical-align: top; }
  tr:hover td { background: #f8fafc; }
  .field-name { font-weight: 600; color: #243b53; }
  .field-value { color: #102a43; }
  .field-value.null { color: #829ab1; font-style: italic; }
  sup.footnote { font-size: 0.65rem; color: #829ab1; margin-left: 0.1em; }

  /* ── Appendices ── */
  .appendix-entry { border: 1px solid #d9e2ec; border-radius: 4px; padding: 0.75rem 1rem; margin-bottom: 0.75rem; }
  .appendix-entry .entry-header { font-weight: 600; font-size: 0.85rem; margin-bottom: 0.4rem; }
  .appendix-entry .entry-meta { font-size: 0.75rem; color: #627d98; }
  .appendix-entry .rationale { font-size: 0.85rem; font-style: italic; margin-top: 0.4rem; color: #334e68; }
  .hash-value { font-family: "Courier New", "Courier", monospace; font-size: 0.75rem; background: #f0f4f8; padding: 0.2em 0.5em; border-radius: 2px; word-break: break-all; color: #334e68; }

  /* ── Disclaimer ── */
  .disclaimer {
    margin-top: 3rem;
    padding: 1rem 1.25rem;
    background: #f0f4f8;
    border: 1px solid #bcccdc;
    border-left: 4px solid #627d98;
    border-radius: 2px;
    font-size: 0.8rem;
    color: #486581;
  }
  .disclaimer strong { display: block; margin-bottom: 0.4rem; color: #243b53; }

  /* ── Completeness bar ── */
  .completeness { display: flex; align-items: center; gap: 0.75rem; margin-bottom: 1.5rem; font-size: 0.8rem; }
  .completeness-bar { flex: 1; height: 6px; background: #d9e2ec; border-radius: 3px; }
  .completeness-fill { height: 100%; border-radius: 3px; background: #10b981; }
  .completeness-label { font-weight: 600; color: #243b53; min-width: 3rem; text-align: right; }

  /* ── Unresolved warning ── */
  .unresolved-warning {
    padding: 0.75rem 1rem;
    background: #fffbeb;
    border: 1px solid #fde68a;
    border-radius: 4px;
    margin-bottom: 1rem;
    font-size: 0.85rem;
    color: #92400e;
  }

  /* ── Sources footnotes ── */
  .sources-list { font-size: 0.8rem; }
  .sources-list li { margin-bottom: 0.5rem; padding-left: 0.25rem; }
  .source-tier { font-size: 0.7rem; color: #829ab1; }
</style>
</head>
<body>

<!-- ── Report header ── -->
<div class="report-header">
  <h1>{{ model.case_title }}</h1>
  <div class="subtitle">Atlas Defensible Record Report · Version {{ model.report_version }}</div>
  <div class="report-meta">
    {% if model.occurrence_date %}<span data-label="Date">{{ model.occurrence_date }}</span>{% endif %}
    {% if model.location %}<span data-label="Location">{{ model.location }}</span>{% endif %}
    {% if model.operator %}<span data-label="Operator">{{ model.operator }}</span>{% endif %}
    {% if model.aircraft_type %}<span data-label="Aircraft">{{ model.aircraft_type }}</span>{% endif %}
    <span data-label="Generated">{{ model.generated_at.strftime("%Y-%m-%d %H:%M UTC") }}</span>
    {% if model.generated_by %}<span data-label="By">{{ model.generated_by }}</span>{% endif %}
    <span data-label="Projection">v{{ model.projection_version }}</span>
  </div>
</div>

<!-- ── Evidence completeness ── -->
<div class="completeness">
  <span>Evidence completeness</span>
  <div class="completeness-bar">
    <div class="completeness-fill" style="width: {{ model.completeness_pct }}%"></div>
  </div>
  <span class="completeness-label">{{ model.completeness_pct }}%</span>
</div>

{% if model.has_unresolved_conflicts %}
<div class="unresolved-warning">
  ⚠ This report contains <strong>{{ model.unresolved_conflict_fields | length }} unresolved conflict(s)</strong>
  on the following field(s): {{ model.unresolved_conflict_fields | join(", ") | replace("_", " ") }}.
  Values for these fields are marked DISPUTED and should not be relied upon as final.
</div>
{% endif %}

<!-- ── 1. Projected facts ── -->
<h2>1. Projected Record</h2>
<p style="font-size:0.8rem;color:#627d98;margin-bottom:0.75rem;">
  This is the current best explanation of the evidence.
  Footnote numbers refer to the Sources appendix.
  Disputed fields are unresolved and should be verified independently.
</p>

<table>
  <thead>
    <tr>
      <th style="width:30%">Field</th>
      <th style="width:40%">Value</th>
      <th style="width:15%">Status</th>
      <th style="width:15%">Source</th>
    </tr>
  </thead>
  <tbody>
    {% for fact in model.facts %}
    <tr>
      <td class="field-name">{{ fact.field_name | replace("_", " ") | title }}</td>
      <td class="field-value {% if fact.field_value is none or fact.field_value == '' %}null{% endif %}">
        {% if fact.field_value is none or fact.field_value == '' %}
          —
        {% elif fact.field_value == 'DISPUTED' %}
          <span class="chip chip-disputed">Disputed</span>
        {% else %}
          {{ fact.field_value }}
        {% endif %}
        {% if fact.footnote_number %}<sup class="footnote">[{{ fact.footnote_number }}]</sup>{% endif %}
      </td>
      <td>
        {% if fact.is_disputed %}
          <span class="chip chip-disputed">Disputed</span>
        {% elif fact.is_manually_overridden %}
          <span class="chip chip-manual">Manual override</span>
        {% elif fact.claim_type == 'CONFIRMED' %}
          <span class="chip chip-resolved">Confirmed</span>
        {% else %}
          <span class="chip" style="background:#f0f4f8;color:#486581;border-color:#bcccdc;">{{ fact.claim_type | replace("_", " ") | lower }}</span>
        {% endif %}
      </td>
      <td style="font-size:0.75rem;color:#627d98;">
        {% if fact.source_ref %}{{ fact.source_ref.source_name[:30] }}{% if fact.source_ref.source_name|length > 30 %}…{% endif %}{% else %}—{% endif %}
      </td>
    </tr>
    {% endfor %}
  </tbody>
</table>

<!-- ── 2. Conflict resolution history ── -->
<h2>2. Conflict Resolution History</h2>
{% if model.resolution_history %}
<p style="font-size:0.8rem;color:#627d98;margin-bottom:0.75rem;">
  {{ model.resolution_history | length }} conflict(s) resolved on the record.
  Each resolution includes the curator rationale.
</p>
{% for res in model.resolution_history %}
<div class="appendix-entry">
  <div class="entry-header">{{ res.field_name | replace("_", " ") | title }}</div>
  <div class="entry-meta">
    {% if res.resolved_at %}Resolved {{ res.resolved_at.strftime("%Y-%m-%d %H:%M UTC") }}{% endif %}
    {% if res.resolved_by %} · by {{ res.resolved_by }}{% endif %}
  </div>
  <div class="entry-meta" style="margin-top:0.3rem;">
    Accepted value: <strong>{{ res.winning_value if res.winning_value is not none else "—" }}</strong>
    {% if res.superseded_values %}
      · Superseded: {{ res.superseded_values | join(", ") }}
    {% endif %}
  </div>
  {% if res.rationale %}
  <div class="rationale">"{{ res.rationale }}"</div>
  {% endif %}
</div>
{% endfor %}
{% else %}
<p style="font-size:0.85rem;color:#627d98;">No conflicts have been resolved for this case.</p>
{% endif %}

<!-- ── 3. Sources ── -->
<h2>3. Sources</h2>
<ol class="sources-list">
{% for source in model.sources %}
  <li>
    <strong>{{ source.source_name }}</strong>
    <span class="source-tier"> ({{ source.source_kind | replace("_", " ") | lower }} · Tier {{ source.reliability_tier }})</span>
  </li>
{% endfor %}
{% if not model.sources %}
  <li style="color:#829ab1;">No sources recorded.</li>
{% endif %}
</ol>

<!-- ── 4. Audit ── -->
<h2>4. Audit & Integrity</h2>
{% for entry in model.audit_entries %}
<div class="appendix-entry">
  <div class="entry-header">{{ entry.description }}</div>
  {% if entry.timestamp %}
  <div class="entry-meta">{{ entry.timestamp.strftime("%Y-%m-%d %H:%M UTC") }}</div>
  {% endif %}
  <div style="margin-top:0.4rem;"><span class="hash-value">{{ entry.hash_value }}</span></div>
</div>
{% endfor %}

<!-- ── Disclaimer ── -->
<div class="disclaimer">
  <strong>Disclaimer</strong>
  {{ model.disclaimer }}
</div>

</body>
</html>"""


_JINJA_ENV = Environment(autoescape=True)
_COMPILED_TEMPLATE = _JINJA_ENV.from_string(_TEMPLATE)


def render_html(model: ReportModel) -> str:
    """Render a ReportModel to an HTML string."""
    return _COMPILED_TEMPLATE.render(model=model)


def render_pdf(html_content: str) -> bytes:
    """Render an HTML report string to PDF bytes via WeasyPrint.

    The returned bytes can be served directly as a PDF download or signed
    for evidence verification.  WeasyPrint system dependencies (libpango,
    libcairo) must be installed at the OS level for this to work.
    """
    from weasyprint import HTML

    doc = HTML(string=html_content).render()
    pdf = doc.write_pdf()
    assert pdf is not None, "WeasyPrint returned None for valid HTML"
    return pdf


def render_pdf_signed(html_content: str) -> tuple[bytes, str, str]:
    """Render HTML to PDF and sign the result.

    Returns (pdf_bytes, content_sha256, evidence_signature).
    The signature can be returned to the caller for independent verification.
    """
    import hashlib

    pdf_bytes = render_pdf(html_content)
    content_hash = hashlib.sha256(pdf_bytes).hexdigest()
    signature = sign_evidence_hash(content_hash)
    return pdf_bytes, content_hash, signature
